from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts import run_recommendation_outcome_worker as worker_script
from scripts.report_recommendation_outcomes import build_report, source_classification


def worker_settings(*, recommendation: bool, research: bool):
    return SimpleNamespace(
        recommendation_outcome_enabled=recommendation,
        recommendation_outcome_interval_seconds=60,
        recommendation_outcome_horizon_list=(60,),
        recommendation_outcome_batch_size=50,
        research_candidate_outcome_enabled=research,
        research_candidate_outcome_interval_seconds=300,
        research_candidate_outcome_horizon_list=(60, 240, 1440),
        research_candidate_outcome_batch_size=20,
    )


class FakeLock:
    def __init__(self, key):
        self.key = key
        self.released = False

    def acquire(self):
        return True

    def release(self):
        self.released = True


def configure_worker(monkeypatch, settings):
    monkeypatch.setattr(
        "crypto_trading_bot.config.settings.get_settings", lambda: settings
    )
    monkeypatch.setattr("crypto_trading_bot.db.database.SessionLocal", object())
    monkeypatch.setattr(
        "crypto_trading_bot.db.postgres_advisory_lock.PostgresAdvisoryLock", FakeLock
    )


def test_disabled_worker_initializes_neither_database_nor_provider(monkeypatch, capsys):
    monkeypatch.setattr(
        "crypto_trading_bot.config.settings.get_settings",
        lambda: SimpleNamespace(
            recommendation_outcome_enabled=False,
            recommendation_outcome_interval_seconds=300,
            research_candidate_outcome_enabled=False,
            research_candidate_outcome_interval_seconds=300,
        ),
    )
    worker_script.run_worker(once=True)
    assert "inactive (all analytics disabled)" in capsys.readouterr().out


def test_worker_lock_key_is_requested_unique_value():
    assert worker_script.RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY == 2026090501


def test_worker_runs_each_enabled_analytics_cycle_once(monkeypatch):
    for recommendation_enabled, research_enabled in (
        (True, False),
        (False, True),
        (True, True),
    ):
        configure_worker(
            monkeypatch,
            worker_settings(
                recommendation=recommendation_enabled, research=research_enabled
            ),
        )
        calls = []
        monkeypatch.setattr(
            worker_script,
            "run_cycle",
            lambda *args, **kwargs: (
                calls.append("recommendation")
                or SimpleNamespace(recommendation_count=1, due_outcome_count=1)
            ),
        )
        monkeypatch.setattr(
            worker_script,
            "run_research_cycle",
            lambda *args, **kwargs: (
                calls.append("research")
                or SimpleNamespace(candidate_count=1, due_outcome_count=1)
            ),
        )
        worker_script.run_worker(once=True)
        assert calls == [
            name
            for enabled, name in (
                (recommendation_enabled, "recommendation"),
                (research_enabled, "research"),
            )
            if enabled
        ]


def test_worker_cycle_failures_are_isolated(monkeypatch, capsys):
    configure_worker(monkeypatch, worker_settings(recommendation=True, research=True))
    calls = []

    def failed(*args, **kwargs):
        calls.append("recommendation")
        raise RuntimeError

    monkeypatch.setattr(worker_script, "run_cycle", failed)
    monkeypatch.setattr(
        worker_script,
        "run_research_cycle",
        lambda *args, **kwargs: (
            calls.append("research")
            or SimpleNamespace(candidate_count=1, due_outcome_count=1)
        ),
    )
    worker_script.run_worker(once=True)
    assert calls == ["recommendation", "research"]
    assert "Recommendation outcome cycle failed" in capsys.readouterr().out


def test_same_worker_tick_shares_one_exact_key_price_resolver(monkeypatch):
    configure_worker(monkeypatch, worker_settings(recommendation=True, research=True))
    resolvers = []

    def recommendation(*args, **kwargs):
        resolvers.append(kwargs["price_resolver"])
        return SimpleNamespace(recommendation_count=0, due_outcome_count=0)

    def research(*args, **kwargs):
        resolvers.append(kwargs["price_resolver"])
        return SimpleNamespace(candidate_count=0, due_outcome_count=0)

    monkeypatch.setattr(worker_script, "run_cycle", recommendation)
    monkeypatch.setattr(worker_script, "run_research_cycle", research)
    worker_script.run_worker(once=True)
    assert len(resolvers) == 2
    assert resolvers[0] is resolvers[1]


def test_research_failure_does_not_undo_completed_recommendation_cycle(
    monkeypatch, capsys
):
    configure_worker(monkeypatch, worker_settings(recommendation=True, research=True))
    calls = []
    monkeypatch.setattr(
        worker_script,
        "run_cycle",
        lambda *args, **kwargs: (
            calls.append("recommendation")
            or SimpleNamespace(recommendation_count=1, due_outcome_count=1)
        ),
    )

    def failed(*args, **kwargs):
        calls.append("research")
        raise RuntimeError

    monkeypatch.setattr(worker_script, "run_research_cycle", failed)
    worker_script.run_worker(once=True)
    assert calls == ["recommendation", "research"]
    assert "Research candidate outcome cycle failed" in capsys.readouterr().out


def test_worker_intervals_remain_independent(monkeypatch):
    configure_worker(monkeypatch, worker_settings(recommendation=True, research=True))
    monkeypatch.setattr(
        worker_script,
        "run_cycle",
        lambda *args, **kwargs: SimpleNamespace(
            recommendation_count=0, due_outcome_count=0
        ),
    )
    monkeypatch.setattr(
        worker_script,
        "run_research_cycle",
        lambda *args, **kwargs: SimpleNamespace(candidate_count=0, due_outcome_count=0),
    )
    times = iter((0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(worker_script.time, "monotonic", lambda: next(times))
    delays = []

    def stop_after_sleep(delay):
        delays.append(delay)
        raise RuntimeError("stop")

    monkeypatch.setattr(worker_script.time, "sleep", stop_after_sleep)
    with pytest.raises(RuntimeError, match="stop"):
        worker_script.run_worker()
    assert delays == [60]


def test_analytics_cycle_helpers_use_separate_sessions_and_transactions(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    def factory():
        session = FakeSession()
        sessions.append(session)
        return session

    class FakeService:
        def __init__(self, session, **kwargs):
            self.session = session

        def evaluate_due(self, **kwargs):
            return SimpleNamespace()

    monkeypatch.setattr(
        "crypto_trading_bot.services.recommendation_outcome_service.RecommendationOutcomeService",
        FakeService,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.research_candidate_outcome_service.ResearchCandidateOutcomeService",
        FakeService,
    )
    worker_script.run_cycle(factory, horizons=(60,), batch_size=1)
    worker_script.run_research_cycle(factory, horizons=(60,), batch_size=1)
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert [session.commits for session in sessions] == [1, 1]
    assert [session.rollbacks for session in sessions] == [0, 0]


def test_recommendation_source_classification_is_safe_for_legacy_rows():
    assert source_classification(None) == "UNKNOWN"
    assert source_classification({"source": "system_guard"}) == "SYSTEM_GUARD"
    assert source_classification({"source": "openai"}) == "OPENAI_NO_SAFETY_OVERRIDE"
    assert (
        source_classification({"source": "openai", "safety_override": {"reason": "x"}})
        == "OPENAI_SAFETY_OVERRIDE"
    )


def test_report_selected_candidate_rank_gap_and_confidence_are_descriptive():
    recommendation = SimpleNamespace(
        id=1,
        action="BUY",
        confidence="0.8",
        ai_response={"source": "openai"},
    )
    selected_outcome = SimpleNamespace(
        horizon_minutes=60,
        evaluation_status="COMPLETE",
        market_return_percentage="2",
        action_aligned_return_percentage="2",
        directional_result="WIN",
    )

    def outcome(market, value, selected=False):
        return SimpleNamespace(
            recommendation_id=1,
            horizon_minutes=60,
            evaluation_status="COMPLETE",
            buy_eligible=True,
            market=market,
            market_return_percentage=Decimal(value),
            is_selected=selected,
        )

    class FakeSession:
        def execute(self, query):
            return [(selected_outcome, recommendation)]

        def scalars(self, query):
            return [
                outcome("KRW-BTC", "2", True),
                outcome("KRW-ETH", "5"),
                outcome("KRW-XRP", "-3"),
            ]

    lines = build_report(FakeSession())
    selection = next(line for line in lines if "selection_quality" in line)
    assert "selected_market=KRW-BTC" in selection
    assert "best_market=KRW-ETH" in selection
    assert "selected_realized_rank=2" in selection
    assert "gap_to_best=-3" in selection
    assert any("confidence_diagnostic_count=1" in line for line in lines)
