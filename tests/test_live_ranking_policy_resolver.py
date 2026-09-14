from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.services.live_policy_canary_service import (
    BASELINE_EXHAUSTED,
    BASELINE_EXPIRED,
    BASELINE_INVALID_CANARY,
    BASELINE_NO_CANARY,
    CANARY,
    LivePolicyCanaryError,
)
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    CanaryRuntimeBusyError,
    LiveRankingPolicyResolver,
)


NOW = datetime(2026, 9, 14, tzinfo=UTC)


def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=7,
        market_universe_prefilter_n=20,
    )


class FakeLock:
    def __init__(self, result=True):
        self.result = result
        self.acquired = False
        self.released = False

    def acquire(self):
        self.acquired = self.result
        return self.result

    def release(self):
        self.released = True
        self.acquired = False


def activation(*, expires_at=None):
    return SimpleNamespace(
        id=1,
        user_id=7,
        exchange="UPBIT",
        quote_asset="KRW",
        started_at=NOW - timedelta(hours=1),
        expires_at=expires_at or NOW + timedelta(hours=47),
        max_analysis_runs=6,
        canary_policy_signature="strategy-replay-v1:canary",
        activation_signature="limited-live-canary-v1a:activation",
        promotion_approval_id=2,
        promotion_approval_signature="human-approved-promotion-v1:approval",
        candidate_id=3,
        baseline_policy_signature="strategy-replay-v1:baseline",
    )


def resolver(monkeypatch, rows, *, used=0, lock=None):
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=7, name="Minsu")
    session.scalars.return_value.all.return_value = rows
    candidate_policy = MagicMock()
    approval = SimpleNamespace(id=2, candidate_id=3)
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.validate_stored_canary_activation",
        lambda *_: (SimpleNamespace(row=approval), MagicMock(), candidate_policy),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.canary_run_count",
        lambda *_: used,
    )
    lock = lock or FakeLock()
    value = LiveRankingPolicyResolver(
        session,
        settings=settings(),
        now_fn=lambda: NOW,
        lock_factory=lambda _: lock,
    )
    return value, session, candidate_policy, lock


def test_no_canary_returns_exact_baseline_without_lock(monkeypatch):
    value, _, _, lock = resolver(monkeypatch, [])
    result = value.inspect()
    assert result.mode == BASELINE_NO_CANARY
    assert result.canary_policy_selected is False
    assert result.active_canary_count == 0
    assert result.ranking_policy.weights.__class__.__name__ == "HeuristicRankingWeights"
    features = [{"market": "KRW-A", "quote_trade_value_24h": "1"}]
    assert result.ranking_policy.rank(features) == HeuristicMarketRankingPolicy().rank(
        features
    )
    assert lock.acquired is False


def test_valid_active_canary_returns_locked_policy(monkeypatch):
    row = activation()
    value, _, candidate_policy, lock = resolver(monkeypatch, [row])
    with value.resolve() as lease:
        assert lease.resolution.mode == CANARY
        assert lease.ranking_policy is candidate_policy
        assert lock.acquired is True
    assert lock.released is True


@pytest.mark.parametrize(
    ("row", "used", "mode"),
    (
        (activation(expires_at=NOW), 0, BASELINE_EXPIRED),
        (activation(), 6, BASELINE_EXHAUSTED),
    ),
)
def test_expired_and_exhausted_use_baseline(monkeypatch, row, used, mode):
    value, _, _, lock = resolver(monkeypatch, [row], used=used)
    result = value.inspect()
    assert result.mode == mode
    assert result.canary_policy_selected is False
    assert lock.acquired is False


def test_invalid_and_multiple_canaries_fail_closed(monkeypatch):
    value, _, _, _ = resolver(monkeypatch, [activation()])
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_ranking_policy_resolver.validate_stored_canary_activation",
        MagicMock(side_effect=LivePolicyCanaryError("corrupt")),
    )
    result = value.inspect()
    assert result.mode == BASELINE_INVALID_CANARY
    assert result.active_canary_count == 0

    value, _, _, _ = resolver(monkeypatch, [activation(), activation()])
    result = value.inspect()
    assert result.mode == BASELINE_INVALID_CANARY
    assert result.active_canary_count == 2


def test_database_failure_propagates(monkeypatch):
    value, session, _, _ = resolver(monkeypatch, [])
    session.scalars.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        value.inspect()


def test_runtime_lock_busy_fails_pipeline(monkeypatch):
    value, _, _, _ = resolver(monkeypatch, [activation()], lock=FakeLock(result=False))
    with pytest.raises(CanaryRuntimeBusyError, match="CANARY_RUNTIME_BUSY"):
        value.resolve()


@pytest.mark.parametrize(("used", "expected_ordinal"), ((0, 1), (5, 6)))
def test_run_is_reserved_and_committed_before_ranking(
    monkeypatch, used, expected_ordinal
):
    row = activation()
    value, session, _, lock = resolver(monkeypatch, [row], used=used)
    with value.resolve() as lease:
        run = lease.reserve_run(
            SimpleNamespace(
                id=31,
                user_id=7,
                run_type="MARKET_UNIVERSE",
                pipeline_run_id="00000000-0000-0000-0000-000000000001",
            )
        )
        assert run.run_ordinal == expected_ordinal
        assert run.used_canary_policy is True
        assert run.run_signature
        session.add.assert_called_once_with(run)
        session.flush.assert_called_once()
        session.commit.assert_called_once()
    assert lock.released is True


def test_lock_releases_when_pipeline_raises(monkeypatch):
    value, _, _, lock = resolver(monkeypatch, [activation()])
    with pytest.raises(RuntimeError):
        with value.resolve():
            raise RuntimeError("ranking failed")
    assert lock.released is True
