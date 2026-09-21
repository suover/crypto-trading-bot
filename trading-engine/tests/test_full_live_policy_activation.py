from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    FullLivePolicyActivation,
    FullLivePolicyTerminationEvent,
    MarketUniversePolicyRun,
)
from crypto_trading_bot.services.full_live_policy_activation_service import (
    ALREADY_ACTIVE,
    BASELINE_MISMATCH,
    CREATED,
    DRY_RUN,
    FullLivePolicyActivationService,
)
from crypto_trading_bot.services.full_live_policy_provenance import (
    ACTIVATION_SCHEMA_VERSION,
    MANUAL_CLI,
    TERMINATION_SCHEMA_VERSION,
    FullLivePolicyIntegrityError,
    activation_signature,
    termination_signature,
)
from crypto_trading_bot.services.full_live_policy_termination_service import (
    ALREADY_TERMINATED,
    TERMINATED,
    FullLivePolicyTerminationService,
)
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    BASELINE,
    FULL_LIVE,
    LiveRankingPolicyResolution,
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.market_universe_service import MarketUniverseService
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)


NOW = datetime(2026, 9, 16, tzinfo=UTC)


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://test:test@localhost/test",
        "market_universe_mode": "DYNAMIC",
        "market_universe_exchange": "UPBIT",
        "market_universe_quote_asset": "KRW",
        "market_universe_top_n": 7,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def custom_component_weights() -> dict[str, Decimal]:
    return {
        "liquidity": Decimal("0"),
        "trend_alignment": Decimal("1"),
        "momentum": Decimal("0"),
        "volume_confirmation": Decimal("0"),
        "spread": Decimal("0"),
        "volatility": Decimal("0"),
        "drawdown": Decimal("0"),
    }


def activation_row(
    *,
    activation_id: int = 11,
    approval_id: int = 21,
    user_id: int = 3,
    activated_at: datetime = NOW,
) -> FullLivePolicyActivation:
    current = settings()
    weights = HeuristicRankingWeights(**custom_component_weights())
    policy = HeuristicMarketRankingPolicy(weights)
    baseline = policy_signature(
        build_policy_data(current, HeuristicMarketRankingPolicy())
    )
    definition = build_policy_data(current, policy)
    row = FullLivePolicyActivation(
        id=activation_id,
        activation_schema_version=ACTIVATION_SCHEMA_VERSION,
        promotion_approval_id=approval_id,
        promotion_approval_signature="human-approved-promotion-v1:" + "a" * 64,
        candidate_id=31,
        shadow_enrollment_id=41,
        user_id=user_id,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="trend-only",
        scenario_definition_signature="scenario-signature",
        component_weights={
            key: str(value) for key, value in custom_component_weights().items()
        },
        baseline_policy_signature=baseline,
        effective_policy_definition=definition,
        effective_policy_signature=policy_signature(definition),
        effective_top_n=7,
        activation_source=MANUAL_CLI,
        activated_at=activated_at,
        activation_signature="",
    )
    row.activation_signature = activation_signature(row)
    return row


def approval_for(row: FullLivePolicyActivation):
    return SimpleNamespace(
        id=row.promotion_approval_id,
        approval_signature=row.promotion_approval_signature,
        candidate_id=row.candidate_id,
        shadow_enrollment_id=row.shadow_enrollment_id,
        user_id=row.user_id,
        exchange=row.exchange,
        quote_asset=row.quote_asset,
        scenario_name=row.scenario_name,
        scenario_definition_signature=row.scenario_definition_signature,
        component_weights=row.component_weights,
        baseline_policy_signature=row.baseline_policy_signature,
        effective_top_n=row.effective_top_n,
    )


def sqlite_like_session() -> MagicMock:
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "sqlite"
    session.scalar.return_value = None
    session.scalars.return_value = []
    return session


def test_activation_preview_and_apply_freeze_exact_effective_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = settings()
    baseline_definition = build_policy_data(current, HeuristicMarketRankingPolicy())
    approval = SimpleNamespace(
        id=21,
        candidate_id=31,
        shadow_enrollment_id=41,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="trend-only",
        scenario_definition_signature="scenario-signature",
        component_weights={
            key: str(value) for key, value in custom_component_weights().items()
        },
        baseline_policy_signature=policy_signature(baseline_definition),
        effective_top_n=7,
        approval_signature="human-approved-promotion-v1:" + "a" * 64,
    )
    candidate = SimpleNamespace(
        reference_snapshot_id=51,
        component_weights=custom_component_weights(),
        effective_top_n=7,
        baseline_policy_signature=approval.baseline_policy_signature,
    )
    validated = SimpleNamespace(approval=approval, candidate=candidate)
    reference = SimpleNamespace(
        policy_signature=approval.baseline_policy_signature,
        policy_data=baseline_definition,
    )
    session = sqlite_like_session()
    session.get.side_effect = lambda model, identity: reference
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.load_and_validate_shadow_policy_promotion_approval",
        lambda *_: validated,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )
    service = FullLivePolicyActivationService(
        session, settings=current, now_fn=lambda: NOW
    )

    preview = service.preview(
        promotion_approval_id=21,
        user_id=3,
        exchange="upbit",
        quote_asset="krw",
    )
    assert preview.activation_status == DRY_RUN
    assert preview.database_write is False
    session.add.assert_not_called()

    applied = service.activate(
        promotion_approval_id=21,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        expected_approval_signature=approval.approval_signature,
    )
    assert applied.activation_status == CREATED
    assert applied.database_write is True
    assert applied.live_order_change is False
    assert (
        applied.activation.effective_policy_definition["ranking"]["weights"][
            "trend_alignment"
        ]
        == "1"
    )
    assert applied.activation.effective_policy_signature == policy_signature(
        applied.activation.effective_policy_definition
    )


def test_activation_rejects_current_baseline_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = SimpleNamespace(
        id=21,
        candidate_id=31,
        shadow_enrollment_id=41,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="x",
        baseline_policy_signature="strategy-replay-v1:" + "0" * 64,
        effective_top_n=7,
        approval_signature="human-approved-promotion-v1:" + "a" * 64,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.load_and_validate_shadow_policy_promotion_approval",
        lambda *_: SimpleNamespace(approval=approval),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )
    result = FullLivePolicyActivationService(
        sqlite_like_session(), settings=settings()
    ).preview(
        promotion_approval_id=21,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    assert result.activation_status == BASELINE_MISMATCH


def test_resolver_full_live_uses_exact_weights_and_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activation = activation_row()
    approval = approval_for(activation)
    session = sqlite_like_session()
    session.scalars.return_value = [activation]
    session.get.return_value = approval
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )

    resolution = LiveRankingPolicyResolver(session, settings=settings()).inspect(
        user_id=3, exchange="UPBIT", quote_asset="KRW"
    )

    assert resolution.mode == FULL_LIVE
    assert resolution.full_live_activation_id == activation.id
    assert resolution.promotion_approval_id == approval.id
    assert resolution.ranking_policy.weights.trend_alignment == Decimal("1")
    assert resolution.ranking_policy.weights.liquidity == Decimal("0")
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_resolver_terminated_activation_falls_back_to_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activation = activation_row()
    approval = approval_for(activation)
    event = FullLivePolicyTerminationEvent(
        id=61,
        termination_schema_version=TERMINATION_SCHEMA_VERSION,
        activation_id=activation.id,
        activation_signature=activation.activation_signature,
        user_id=activation.user_id,
        exchange=activation.exchange,
        quote_asset=activation.quote_asset,
        termination_source=MANUAL_CLI,
        termination_reason="manual rollback",
        terminated_at=NOW + timedelta(minutes=1),
        termination_signature="",
    )
    event.termination_signature = termination_signature(event)
    session = sqlite_like_session()
    session.scalars.return_value = [activation]
    session.get.return_value = approval
    session.scalar.return_value = event
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )

    resolution = LiveRankingPolicyResolver(session, settings=settings()).inspect(
        user_id=3, exchange="UPBIT", quote_asset="KRW"
    )
    assert resolution.mode == BASELINE
    assert resolution.full_live_activation_id is None


def test_resolver_tampered_or_duplicate_active_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = activation_row()
    approval = approval_for(first)
    session = sqlite_like_session()
    session.scalars.return_value = [first]
    session.get.return_value = approval
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )
    first.activation_signature = "tampered"
    with pytest.raises(FullLivePolicyIntegrityError):
        LiveRankingPolicyResolver(session, settings=settings()).inspect(
            user_id=3, exchange="UPBIT", quote_asset="KRW"
        )

    first = activation_row()
    second = activation_row(activation_id=12, approval_id=22)
    approvals = {
        first.promotion_approval_id: approval_for(first),
        second.promotion_approval_id: approval_for(second),
    }
    session = sqlite_like_session()
    session.scalars.return_value = [first, second]
    session.get.side_effect = lambda _model, identity: approvals[identity]
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.promotion_approval_signature",
        lambda row: row.approval_signature,
    )
    with pytest.raises(FullLivePolicyIntegrityError, match="multiple active"):
        LiveRankingPolicyResolver(session, settings=settings()).inspect(
            user_id=3, exchange="UPBIT", quote_asset="KRW"
        )


def test_stop_is_immutable_idempotent_and_next_resolution_can_be_baseline() -> None:
    activation = activation_row()
    session = sqlite_like_session()
    session.get.return_value = activation
    service = FullLivePolicyTerminationService(
        session, now_fn=lambda: NOW + timedelta(minutes=1)
    )

    result = service.terminate(
        activation_id=activation.id,
        user_id=activation.user_id,
        exchange=activation.exchange,
        quote_asset=activation.quote_asset,
        expected_activation_signature=activation.activation_signature,
        reason="manual rollback",
    )
    assert result.termination_status == TERMINATED
    assert result.database_write is True
    assert result.termination.activation_id == activation.id
    assert result.termination.termination_signature == termination_signature(
        result.termination
    )
    assert not hasattr(activation, "active")
    assert not hasattr(activation, "terminated_at")

    session.scalar.return_value = result.termination
    again = service.terminate(
        activation_id=activation.id,
        user_id=activation.user_id,
        exchange=activation.exchange,
        quote_asset=activation.quote_asset,
        expected_activation_signature=activation.activation_signature,
        reason="manual rollback",
    )
    assert again.termination_status == ALREADY_TERMINATED
    assert again.database_write is False


def test_same_approval_is_idempotently_already_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activation = activation_row()
    approval = approval_for(activation)
    session = sqlite_like_session()
    session.scalar.side_effect = [activation, None]
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.load_and_validate_shadow_policy_promotion_approval",
        lambda *_: SimpleNamespace(approval=approval),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.promotion_approval_signature",
        lambda *_: approval.approval_signature,
    )
    result = FullLivePolicyActivationService(session, settings=settings()).activate(
        promotion_approval_id=approval.id,
        user_id=approval.user_id,
        exchange=approval.exchange,
        quote_asset=approval.quote_asset,
        expected_approval_signature=approval.approval_signature,
    )
    assert result.activation_status == ALREADY_ACTIVE
    assert result.database_write is False


def test_policy_run_provenance_records_exact_full_live_context() -> None:
    session = MagicMock()
    policy = HeuristicMarketRankingPolicy(
        HeuristicRankingWeights(**custom_component_weights())
    )
    resolution = LiveRankingPolicyResolution(
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        mode=FULL_LIVE,
        ranking_policy=policy,
        baseline_policy_signature="baseline",
        effective_policy_signature="effective",
        full_live_activation_id=11,
        promotion_approval_id=21,
    )
    service = MarketUniverseService(
        session, settings=settings(), ranking_policy=None, policy_resolution=resolution
    )
    service._persist_policy_run(
        analysis_run=AnalysisRun(id=71, user_id=3),
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    row = session.add.call_args.args[0]
    assert isinstance(row, MarketUniversePolicyRun)
    assert row.analysis_run_id == 71
    assert row.mode == FULL_LIVE
    assert row.full_live_activation_id == 11
    assert row.promotion_approval_id == 21


def test_full_live_weights_change_actual_ranking_output() -> None:
    candidates = [
        {
            "market": "KRW-BTC",
            "quote_trade_value_24h": "200",
            "timeframes": {
                "15m": {
                    "data_quality": "SUFFICIENT",
                    "trend_label": "하락 우위",
                }
            },
            "orderbook": {"spread_rate": "0"},
        },
        {
            "market": "KRW-ETH",
            "quote_trade_value_24h": "100",
            "timeframes": {
                "15m": {
                    "data_quality": "SUFFICIENT",
                    "trend_label": "상승 우위",
                }
            },
            "orderbook": {"spread_rate": "0"},
        },
    ]
    baseline = HeuristicMarketRankingPolicy().rank(candidates)
    full_live = HeuristicMarketRankingPolicy(
        HeuristicRankingWeights(**custom_component_weights())
    ).rank(candidates)
    assert baseline[0]["market"] == "KRW-BTC"
    assert full_live[0]["market"] == "KRW-ETH"


def test_policy_run_provenance_records_baseline_without_activation() -> None:
    session = MagicMock()
    service = MarketUniverseService(session, settings=settings())
    service._persist_policy_run(
        analysis_run=AnalysisRun(id=72, user_id=4),
        user_id=4,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    row = session.add.call_args.args[0]
    assert isinstance(row, MarketUniversePolicyRun)
    assert row.mode == BASELINE
    assert row.full_live_activation_id is None
    assert row.promotion_approval_id is None
    assert row.baseline_policy_signature == row.effective_policy_signature


def test_full_live_context_is_isolated_by_user_and_quote() -> None:
    activation = activation_row(user_id=3)
    approval = approval_for(activation)

    active_session = sqlite_like_session()
    active_session.scalars.return_value = [activation]
    active_session.get.return_value = approval
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "crypto_trading_bot.services.live_ranking_policy_resolver.promotion_approval_signature",
            lambda *_: approval.approval_signature,
        )
        active = LiveRankingPolicyResolver(active_session, settings=settings()).inspect(
            user_id=3, exchange="UPBIT", quote_asset="KRW"
        )
    other_user = LiveRankingPolicyResolver(
        sqlite_like_session(), settings=settings()
    ).inspect(user_id=4, exchange="UPBIT", quote_asset="KRW")
    other_quote = LiveRankingPolicyResolver(
        sqlite_like_session(), settings=settings()
    ).inspect(user_id=3, exchange="UPBIT", quote_asset="BTC")

    assert active.mode == FULL_LIVE
    assert other_user.mode == BASELINE
    assert other_quote.mode == BASELINE


def test_postgresql_apply_acquires_context_transaction_lock_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = sqlite_like_session()
    session.get_bind.return_value.dialect.name = "postgresql"

    def invalid_approval(*_args):
        from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
            ShadowPolicyPromotionApprovalError,
        )

        raise ShadowPolicyPromotionApprovalError("invalid approval")

    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.load_and_validate_shadow_policy_promotion_approval",
        invalid_approval,
    )
    result = FullLivePolicyActivationService(session, settings=settings()).activate(
        promotion_approval_id=21,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        expected_approval_signature="human-approved-promotion-v1:" + "a" * 64,
    )

    assert result.activation_status == "INVALID_APPROVAL"
    statement = str(session.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in statement
    lock_key = session.execute.call_args.args[1]["lock_key"]
    assert isinstance(lock_key, int)
    assert -(2**63) <= lock_key <= 2**63 - 1


def test_different_approval_cannot_replace_active_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = activation_row(approval_id=21)
    current = settings()
    baseline = policy_signature(
        build_policy_data(current, HeuristicMarketRankingPolicy())
    )
    requested = SimpleNamespace(
        id=22,
        candidate_id=32,
        shadow_enrollment_id=42,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="replacement",
        scenario_definition_signature="replacement-signature",
        component_weights={
            key: str(value) for key, value in custom_component_weights().items()
        },
        baseline_policy_signature=baseline,
        effective_top_n=7,
        approval_signature="human-approved-promotion-v1:" + "b" * 64,
    )
    session = sqlite_like_session()
    session.scalars.return_value = [existing]
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.load_and_validate_shadow_policy_promotion_approval",
        lambda *_: SimpleNamespace(approval=requested),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.full_live_policy_activation_service.promotion_approval_signature",
        lambda *_: requested.approval_signature,
    )
    result = FullLivePolicyActivationService(session, settings=current).activate(
        promotion_approval_id=requested.id,
        user_id=requested.user_id,
        exchange=requested.exchange,
        quote_asset=requested.quote_asset,
        expected_approval_signature=requested.approval_signature,
    )
    assert result.activation_status == "ACTIVE_FULL_LIVE_EXISTS"
    assert result.database_write is False
    session.add.assert_not_called()


def test_stop_rejects_missing_wrong_context_and_wrong_signature() -> None:
    missing_session = sqlite_like_session()
    missing_session.get.return_value = None
    missing = FullLivePolicyTerminationService(missing_session).preview(
        activation_id=999,
        user_id=3,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    assert missing.termination_status == "NOT_FOUND"

    activation = activation_row()
    session = sqlite_like_session()
    session.get.return_value = activation
    service = FullLivePolicyTerminationService(session)
    wrong_context = service.preview(
        activation_id=activation.id,
        user_id=4,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    assert wrong_context.termination_status == "CONTEXT_MISMATCH"
    with pytest.raises(ValueError, match="expected activation signature"):
        service.terminate(
            activation_id=activation.id,
            user_id=activation.user_id,
            exchange=activation.exchange,
            quote_asset=activation.quote_asset,
            expected_activation_signature="invalid",
            reason="manual rollback",
        )
