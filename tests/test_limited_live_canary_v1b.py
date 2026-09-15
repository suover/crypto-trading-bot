from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from crypto_trading_bot.services.live_order_execution_service import (
    canary_buy_budget_lock_key,
)
from crypto_trading_bot.db.models import AnalysisRun, MarketUniverseCandidate
from crypto_trading_bot.services.canary_trade_provenance_service import (
    BASELINE_RECOMMENDATION,
    CANARY_RECOMMENDATION,
    INVALID_CANARY_PROVENANCE,
    CanaryTradeProvenanceService,
)
from crypto_trading_bot.services.live_policy_canary_service import (
    LIMITED_LIVE_CANARY_ORDER_SAFETY_V1B,
    LimitedLiveCanaryOrderSafetyPolicy,
    LivePolicyCanaryError,
    build_canary_safety_binding,
    canary_order_safety_policy_signature,
    canary_safety_binding_signature,
    validate_canary_order_safety_policy,
)
from crypto_trading_bot.services.live_policy_canary_termination_service import (
    DRY_RUN,
    STOPPED,
    LivePolicyCanaryTerminationService,
    canary_termination_signature,
)


def test_canary_order_safety_policy_is_code_frozen_and_signed():
    policy = LIMITED_LIVE_CANARY_ORDER_SAFETY_V1B
    assert policy.schema_version == "limited-live-canary-order-safety-v1b"
    assert policy.max_buy_order_amount_krw == 10_000
    assert policy.daily_max_buy_amount_krw == 30_000
    assert canary_order_safety_policy_signature() == (
        canary_order_safety_policy_signature()
    )


@pytest.mark.parametrize(
    "policy",
    (
        LimitedLiveCanaryOrderSafetyPolicy(
            "limited-live-canary-order-safety-v1b", 0, 30_000
        ),
        LimitedLiveCanaryOrderSafetyPolicy(
            "limited-live-canary-order-safety-v1b", 10_000, 9_999
        ),
        LimitedLiveCanaryOrderSafetyPolicy("wrong", 10_000, 30_000),
    ),
)
def test_canary_order_safety_policy_rejects_invalid_values(policy):
    with pytest.raises(LivePolicyCanaryError):
        validate_canary_order_safety_policy(policy)


def test_canary_policy_mutation_changes_signature():
    changed = LimitedLiveCanaryOrderSafetyPolicy(
        "limited-live-canary-order-safety-v1b", 9_000, 30_000
    )
    assert canary_order_safety_policy_signature(changed) != (
        canary_order_safety_policy_signature()
    )


def test_safety_binding_is_deterministic_and_covers_activation_identity():
    activation = SimpleNamespace(
        id=11,
        activation_signature="limited-live-canary-v1a:" + "a" * 64,
        promotion_approval_id=12,
        promotion_approval_signature="human-approved-promotion-v1:" + "b" * 64,
        candidate_id=13,
        user_id=14,
        exchange="UPBIT",
        quote_asset="KRW",
    )
    bound_at = datetime(2026, 9, 14, tzinfo=UTC)
    binding = build_canary_safety_binding(activation, bound_at=bound_at)
    assert binding.canary_activation_id == 11
    assert binding.max_buy_order_amount_krw == Decimal("10000")
    assert binding.daily_max_buy_amount_krw == Decimal("30000")
    assert binding.binding_signature == canary_safety_binding_signature(binding)
    previous = binding.binding_signature
    binding.candidate_id = 99
    assert canary_safety_binding_signature(binding) != previous


def test_canary_budget_lock_key_is_deterministic_signed_bigint_and_day_scoped():
    first = canary_buy_budget_lock_key(11, date(2026, 9, 14).isoformat())
    assert first == canary_buy_budget_lock_key(11, "2026-09-14")
    assert first != canary_buy_budget_lock_key(11, "2026-09-15")
    assert first != canary_buy_budget_lock_key(12, "2026-09-14")
    assert -(2**63) <= first <= 2**63 - 1


def test_missing_persisted_activation_id_cannot_create_binding():
    activation = MagicMock(id=None)
    with pytest.raises(LivePolicyCanaryError, match="persisted"):
        build_canary_safety_binding(
            activation, bound_at=datetime(2026, 9, 14, tzinfo=UTC)
        )


class TerminationSession:
    def __init__(self, activation):
        self.activation = activation
        self.new = set()
        self.dirty = set()
        self.deleted = set()
        self.added = []
        self.commits = 0

    def get(self, _model, object_id):
        return self.activation if object_id == self.activation.id else None

    def scalar(self, _statement):
        return None

    def add(self, value):
        self.added.append(value)
        if len(self.added) == 1:
            value.id = 71

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def expire_all(self):
        return None


class TerminationLock:
    def __init__(self):
        self.released = False

    def acquire(self):
        return True

    def release(self):
        self.released = True


def test_manual_stop_preview_is_read_only_and_apply_creates_immutable_event(
    monkeypatch,
):
    now = datetime(2026, 9, 14, tzinfo=UTC)
    activation = SimpleNamespace(
        id=61,
        activation_signature="limited-live-canary-v1a:" + "a" * 64,
        promotion_approval_id=62,
        promotion_approval_signature="human-approved-promotion-v1:" + "b" * 64,
        candidate_id=63,
        user_id=64,
        exchange="UPBIT",
        quote_asset="KRW",
        started_at=datetime(2026, 9, 13, tzinfo=UTC),
        expires_at=datetime(2026, 9, 15, tzinfo=UTC),
        max_analysis_runs=6,
    )
    binding = SimpleNamespace(
        id=65, binding_signature="limited-live-canary-safety-binding-v1b:" + "c" * 64
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_policy_canary_termination_service.validate_stored_canary_activation",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_policy_canary_termination_service.load_and_validate_canary_safety_binding",
        lambda *_: binding,
    )
    session = TerminationSession(activation)
    lock = TerminationLock()
    service = LivePolicyCanaryTerminationService(
        session,
        now_fn=lambda: now,
        lock_factory=lambda _: lock,
    )
    preview = service.preview(canary_activation_id=activation.id)
    assert preview.termination_status == DRY_RUN
    assert session.added == []
    stopped = service.stop(
        canary_activation_id=activation.id,
        expected_activation_signature=activation.activation_signature,
    )
    assert stopped.termination_status == STOPPED
    assert stopped.termination_event.canary_activation_id == activation.id
    assert stopped.termination_event.termination_signature == (
        canary_termination_signature(stopped.termination_event)
    )
    assert session.added[1].error_code == "MANUAL_STOP"
    assert session.commits == 1
    assert lock.released is True


def test_recommendation_provenance_is_generation_lineage_not_current_status(
    monkeypatch,
):
    pipeline_id = uuid4()
    recommendation = SimpleNamespace(
        id=1,
        universe_candidate_id=2,
        analysis_run_id=3,
        user_id=4,
        exchange="UPBIT",
        market="KRW-BTC",
    )
    candidate = SimpleNamespace(
        id=2,
        analysis_run_id=5,
        user_id=4,
        exchange="UPBIT",
        market="KRW-BTC",
    )
    market_run = SimpleNamespace(
        id=5,
        pipeline_run_id=pipeline_id,
        user_id=4,
        run_type="MARKET_UNIVERSE",
    )
    ai_run = SimpleNamespace(
        id=3,
        pipeline_run_id=pipeline_id,
        user_id=4,
        run_type="AI_RECOMMENDATION",
    )
    run = SimpleNamespace(
        id=6,
        analysis_run_id=5,
        pipeline_run_id=pipeline_id,
        user_id=4,
        exchange="UPBIT",
    )
    activation = SimpleNamespace(
        id=7,
        user_id=4,
        exchange="UPBIT",
        activation_signature="activation",
    )
    binding = SimpleNamespace(
        id=8,
        binding_signature="binding",
        max_buy_order_amount_krw=Decimal("10000"),
        daily_max_buy_amount_krw=Decimal("30000"),
    )
    approval = SimpleNamespace(id=9)
    session = MagicMock()
    session.scalar.return_value = run
    session.get.side_effect = lambda model, object_id: (
        candidate
        if model is MarketUniverseCandidate
        else market_run
        if model is AnalysisRun and object_id == 5
        else ai_run
        if model is AnalysisRun and object_id == 3
        else None
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.canary_trade_provenance_service.load_and_validate_live_policy_canary_run",
        lambda *_: (run, activation, binding, approval),
    )
    result = CanaryTradeProvenanceService(session).resolve(recommendation)
    assert result.mode == CANARY_RECOMMENDATION
    assert result.per_order_buy_cap == Decimal("10000")
    assert result.daily_buy_cap == Decimal("30000")

    ai_run.pipeline_run_id = uuid4()
    invalid = CanaryTradeProvenanceService(session).resolve(recommendation)
    assert invalid.mode == INVALID_CANARY_PROVENANCE


def test_recommendation_without_persisted_canary_run_is_baseline():
    recommendation = SimpleNamespace(
        id=1,
        universe_candidate_id=None,
        analysis_run_id=3,
        user_id=4,
        exchange="UPBIT",
        market="KRW-BTC",
    )
    result = CanaryTradeProvenanceService(MagicMock()).resolve(recommendation)
    assert result.mode == BASELINE_RECOMMENDATION
    assert result.per_order_buy_cap is None
