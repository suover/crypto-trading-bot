from decimal import Decimal
from types import SimpleNamespace

from scripts import run_recommendation_outcome_worker as worker_script
from scripts.report_recommendation_outcomes import build_report, source_classification


def test_disabled_worker_initializes_neither_database_nor_provider(monkeypatch, capsys):
    monkeypatch.setattr(
        "crypto_trading_bot.config.settings.get_settings",
        lambda: SimpleNamespace(
            recommendation_outcome_enabled=False,
            recommendation_outcome_interval_seconds=300,
        ),
    )
    worker_script.run_worker(once=True)
    assert "inactive (disabled)" in capsys.readouterr().out


def test_worker_lock_key_is_requested_unique_value():
    assert worker_script.RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY == 2026090501


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
