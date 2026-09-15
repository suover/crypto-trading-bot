from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import HeuristicRankingWeights
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.live_policy_canary_service import (
    ACTIVE_CANARY_EXISTS,
    ALREADY_ACTIVATED,
    APPROVAL_SIGNATURE_CHANGED,
    CANARY_CONTEXT_BUSY,
    CREATED,
    DRY_RUN,
    INVALID_CANARY_ACTIVATION,
    LIMITED_LIVE_CANARY_V1A,
    NO_PROMOTION_APPROVAL,
    LivePolicyCanaryActivationService,
    baseline_ranking_policy,
    canary_activation_signature,
    canary_context_lock_key,
    canary_policy_definition_signature,
    canary_ranking_policy,
    canary_run_signature,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)


NOW = datetime(2026, 9, 14, tzinfo=UTC)


def settings(**overrides):
    values = {
        "_env_file": None,
        "database_url": "postgresql://test:test@localhost/test",
        "market_universe_mode": "DYNAMIC",
        "market_universe_top_n": 7,
        "market_universe_prefilter_n": 20,
    }
    values.update(overrides)
    return Settings(**values)


def component_weights():
    source = HeuristicRankingWeights()
    return {
        name: str(getattr(source, name))
        for name in (
            "liquidity",
            "trend_alignment",
            "momentum",
            "volume_confirmation",
            "spread",
            "volatility",
            "drawdown",
        )
    }


def approval(runtime_settings):
    scenario = parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [
                {"name": "canary-candidate", "component_weights": component_weights()}
            ],
        }
    )[0]
    _, baseline_signature = baseline_ranking_policy(runtime_settings)
    return SimpleNamespace(
        id=11,
        approval_signature="human-approved-promotion-v1:" + ("a" * 64),
        candidate_id=12,
        shadow_enrollment_id=13,
        user_id=14,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name=scenario.name,
        scenario_definition_signature=scenario.definition_signature,
        component_weights=component_weights(),
        dataset_schema_version="strategy-replay-dataset-v1",
        baseline_policy_signature=baseline_signature,
        effective_top_n=7,
    )


class FakeLock:
    def __init__(self, acquired=True):
        self.result = acquired
        self.acquired = False
        self.released = False

    def acquire(self):
        self.acquired = self.result
        return self.result

    def release(self):
        self.released = True
        self.acquired = False


def service(monkeypatch, *, runtime_settings=None, stored_approval=True, lock=None):
    runtime_settings = runtime_settings or settings()
    row = approval(runtime_settings)
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_policy_canary_service.load_and_validate_shadow_policy_promotion_approval",
        lambda *_: SimpleNamespace(row=row) if stored_approval else None,
    )
    session = MagicMock()
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    session.scalar.return_value = None
    session.scalars.return_value.all.return_value = []
    lock = lock or FakeLock()
    value = LivePolicyCanaryActivationService(
        session,
        settings=runtime_settings,
        now_fn=lambda: NOW,
        lock_factory=lambda _: lock,
    )
    return value, session, row, lock


def test_canary_policy_is_code_frozen_and_signature_is_deterministic():
    assert LIMITED_LIVE_CANARY_V1A.schema_version == "limited-live-canary-v1a"
    assert LIMITED_LIVE_CANARY_V1A.max_duration_hours == 48
    assert LIMITED_LIVE_CANARY_V1A.max_analysis_runs == 6
    assert canary_policy_definition_signature() == canary_policy_definition_signature()


def test_context_lock_key_is_stable_and_signed_bigint():
    first = canary_context_lock_key(1, "upbit", "krw")
    assert first == canary_context_lock_key(1, "UPBIT", "KRW")
    assert -(2**63) <= first <= 2**63 - 1


def test_activation_preview_is_read_only(monkeypatch):
    value, session, row, _ = service(monkeypatch)
    result = value.preview(promotion_approval_id=row.id)
    assert result.activation_status == DRY_RUN
    assert result.proposed_expires_at - result.proposed_started_at == timedelta(
        hours=48
    )
    assert result.activation.canary_policy_signature
    assert result.activation.activation_signature
    assert result.database_write is False
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_activation_no_approval_is_safe_noop(monkeypatch):
    value, session, _, _ = service(monkeypatch, stored_approval=False)
    result = value.preview(promotion_approval_id=99)
    assert result.activation_status == NO_PROMOTION_APPROVAL
    session.add.assert_not_called()


def test_activation_requires_exact_approval_signature(monkeypatch):
    value, session, row, _ = service(monkeypatch)
    different = row.approval_signature[:-1] + "b"
    result = value.activate(
        promotion_approval_id=row.id, expected_approval_signature=different
    )
    assert result.activation_status == APPROVAL_SIGNATURE_CHANGED
    session.add.assert_not_called()


def test_activation_exact_signature_creates_and_commits_under_lock(monkeypatch):
    value, session, row, lock = service(monkeypatch)
    flush_count = 0

    def assign_ids():
        nonlocal flush_count
        flush_count += 1
        if flush_count == 1:
            session.add.call_args.args[0].id = 21
        elif flush_count == 2:
            session.add.call_args.args[0].id = 22

    session.flush.side_effect = assign_ids
    result = value.activate(
        promotion_approval_id=row.id,
        expected_approval_signature=row.approval_signature,
    )
    assert result.activation_status == CREATED
    assert result.activation.activation_signature
    assert result.safety_binding.canary_activation_id == result.activation.id
    assert result.safety_binding.max_buy_order_amount_krw == 10_000
    assert result.safety_binding.daily_max_buy_amount_krw == 30_000
    assert result.database_write is True
    assert session.add.call_args_list[0].args[0] is result.activation
    assert session.add.call_args_list[1].args[0] is result.safety_binding
    assert session.add.call_args_list[2].args[0].error_code == "CANARY_STARTED"
    assert session.flush.call_count == 3
    session.commit.assert_called_once()
    assert lock.released is True


def test_activation_lock_busy_is_no_write(monkeypatch):
    lock = FakeLock(acquired=False)
    value, session, row, _ = service(monkeypatch, lock=lock)
    result = value.activate(
        promotion_approval_id=row.id,
        expected_approval_signature=row.approval_signature,
    )
    assert result.activation_status == CANARY_CONTEXT_BUSY
    session.add.assert_not_called()


def test_activation_rejects_static_and_baseline_drift(monkeypatch):
    runtime_settings = settings(market_universe_mode="STATIC")
    value, session, row, _ = service(monkeypatch, runtime_settings=runtime_settings)
    result = value.preview(promotion_approval_id=row.id)
    assert result.activation_status == INVALID_CANARY_ACTIVATION
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    (
        lambda row: setattr(row, "baseline_policy_signature", "corrupt"),
        lambda row: setattr(row, "effective_top_n", 8),
        lambda row: setattr(row, "exchange", "OTHER"),
        lambda row: setattr(row, "quote_asset", "USD"),
        lambda row: setattr(
            row, "component_weights", {**row.component_weights, "unknown": "1"}
        ),
    ),
)
def test_activation_rejects_approval_runtime_drift(monkeypatch, mutation):
    value, session, row, _ = service(monkeypatch)
    mutation(row)
    result = value.preview(promotion_approval_id=row.id)
    assert result.activation_status == INVALID_CANARY_ACTIVATION
    session.add.assert_not_called()


def test_existing_activation_is_validated_and_idempotent(monkeypatch):
    value, session, row, _ = service(monkeypatch)
    activation = value.preview(promotion_approval_id=row.id).activation
    session.scalar.return_value = activation
    binding = SimpleNamespace(id=22)
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_policy_canary_service.load_and_validate_canary_safety_binding",
        lambda *_: binding,
    )

    result = value.activate(
        promotion_approval_id=row.id,
        expected_approval_signature=row.approval_signature,
    )

    assert result.activation_status == ALREADY_ACTIVATED
    assert result.database_write is False
    assert result.safety_binding is binding
    session.add.assert_not_called()


def test_existing_activation_corruption_fails_closed(monkeypatch):
    value, session, row, _ = service(monkeypatch)
    activation = value.preview(promotion_approval_id=row.id).activation
    activation.activation_signature = "corrupt"
    session.scalar.return_value = activation

    result = value.activate(
        promotion_approval_id=row.id,
        expected_approval_signature=row.approval_signature,
    )

    assert result.activation_status == INVALID_CANARY_ACTIVATION
    session.add.assert_not_called()


def test_activation_reports_existing_context(monkeypatch):
    value, session, row, _ = service(monkeypatch)
    session.scalars.return_value.all.return_value = [
        SimpleNamespace(id=22, max_analysis_runs=6)
    ]
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_policy_canary_service.canary_run_count",
        lambda *_: 0,
    )
    result = value.preview(promotion_approval_id=row.id)
    assert result.activation_status == ACTIVE_CANARY_EXISTS
    assert result.activation.canary_policy_signature


def test_activation_signature_is_timezone_canonical_and_covers_weights(monkeypatch):
    value, _, row, _ = service(monkeypatch)
    result = value.preview(promotion_approval_id=row.id)
    activation = value._build_activation(
        row,
        started_at=result.proposed_started_at,
        expires_at=result.proposed_expires_at,
    )
    activation.activation_signature = ""
    first = canary_activation_signature(activation)
    activation.started_at = activation.started_at.astimezone(
        timezone(timedelta(hours=9))
    )
    activation.expires_at = activation.expires_at.astimezone(
        timezone(timedelta(hours=9))
    )
    assert canary_activation_signature(activation) == first
    activation.component_weights = {**activation.component_weights, "momentum": "0"}
    assert canary_activation_signature(activation) != first


def test_canary_run_signature_is_deterministic_and_covers_lineage():
    run = SimpleNamespace(
        run_schema_version="limited-live-canary-run-v1a",
        canary_activation_id=1,
        activation_signature="activation",
        promotion_approval_id=2,
        promotion_approval_signature="approval",
        candidate_id=3,
        user_id=4,
        exchange="UPBIT",
        quote_asset="KRW",
        analysis_run_id=5,
        pipeline_run_id="00000000-0000-0000-0000-000000000001",
        run_ordinal=1,
        baseline_policy_signature="baseline",
        canary_policy_signature="canary",
        used_canary_policy=True,
        reserved_at=NOW,
    )
    first = canary_run_signature(run)
    assert canary_run_signature(run) == first
    run.run_ordinal = 2
    assert canary_run_signature(run) != first


def test_candidate_weights_change_ranking_without_changing_formula():
    runtime_settings = settings()
    row = approval(runtime_settings)
    row.component_weights = {
        "liquidity": "0",
        "trend_alignment": "0",
        "momentum": "1",
        "volume_confirmation": "0",
        "spread": "0",
        "volatility": "0",
        "drawdown": "0",
    }
    scenario = parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [
                {"name": row.scenario_name, "component_weights": row.component_weights}
            ],
        }
    )[0]
    row.scenario_definition_signature = scenario.definition_signature
    baseline, _ = baseline_ranking_policy(runtime_settings)
    candidate_policy, candidate_signature = canary_ranking_policy(row, runtime_settings)
    features = [
        {
            "market": "KRW-A",
            "quote_trade_value_24h": "1000",
            "timeframes": {
                "15m": {
                    "data_quality": "SUFFICIENT",
                    "trend_label": "관망",
                    "recent_change_rate": "-10",
                    "volume_ratio": "100",
                    "realized_volatility": "10",
                    "max_drawdown": "10",
                }
            },
            "orderbook": {"spread_rate": "0.001"},
        },
        {
            "market": "KRW-B",
            "quote_trade_value_24h": "100",
            "timeframes": {
                "15m": {
                    "data_quality": "SUFFICIENT",
                    "trend_label": "관망",
                    "recent_change_rate": "10",
                    "volume_ratio": "100",
                    "realized_volatility": "10",
                    "max_drawdown": "10",
                }
            },
            "orderbook": {"spread_rate": "0.001"},
        },
    ]
    assert baseline.rank(features)[0]["market"] == "KRW-A"
    assert candidate_policy.rank(features)[0]["market"] == "KRW-B"
    assert candidate_signature == canary_ranking_policy(row, runtime_settings)[1]
