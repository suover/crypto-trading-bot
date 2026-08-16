from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    MarketUniverseCandidate,
    TradeRecommendation,
)
from crypto_trading_bot.services.live_order_execution_service import (
    LiveOrderExecutionService,
)
from crypto_trading_bot.services.live_order_safety import LiveOrderSafetyError


def configure_dynamic_live(
    monkeypatch: pytest.MonkeyPatch, *, dynamic_enabled: bool
) -> None:
    values = {
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "UPBIT_ACCESS_KEY": "access",
        "UPBIT_SECRET_KEY": "secret",
        "LIVE_ORDER_ENABLED": "true",
        "LIVE_ORDER_CONFIRMATION": "ENABLE_LIVE_UPBIT_ORDERS",
        "MARKET_UNIVERSE_MODE": "DYNAMIC",
        "LIVE_DYNAMIC_MARKET_ENABLED": str(dynamic_enabled).lower(),
        "MAX_ORDER_AMOUNT_KRW": "10000",
        "DAILY_MAX_ORDER_AMOUNT_KRW": "30000",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def configure_static_live(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "UPBIT_ACCESS_KEY": "access",
        "UPBIT_SECRET_KEY": "secret",
        "LIVE_ORDER_ENABLED": "true",
        "LIVE_ORDER_CONFIRMATION": "ENABLE_LIVE_UPBIT_ORDERS",
        "MARKET_UNIVERSE_MODE": "STATIC",
        "ALLOWED_MARKETS": "KRW-SOL",
        "MAX_ORDER_AMOUNT_KRW": "10000",
        "DAILY_MAX_ORDER_AMOUNT_KRW": "30000",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def recommendation(candidate_id: int | None = 10) -> TradeRecommendation:
    return TradeRecommendation(
        id=1,
        analysis_run_id=2,
        universe_candidate_id=candidate_id,
        user_id=3,
        exchange="UPBIT",
        market="KRW-SOL",
        action="BUY",
        trade_ratio=Decimal("0.5"),
        recommended_amount_krw=Decimal("5000"),
        status="APPROVED",
    )


def build_session(row: TradeRecommendation, *, buy_eligible: bool = True) -> MagicMock:
    pipeline_id = "11111111-1111-1111-1111-111111111111"
    recommendation_run = AnalysisRun(
        id=2,
        user_id=3,
        pipeline_run_id=pipeline_id,
        run_type="AI_RECOMMENDATION",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    universe_run = AnalysisRun(
        id=4,
        user_id=3,
        pipeline_run_id=pipeline_id,
        run_type="MARKET_UNIVERSE",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    candidate = MarketUniverseCandidate(
        id=10,
        analysis_run_id=4,
        user_id=3,
        exchange="UPBIT",
        market="KRW-SOL",
        base_asset="SOL",
        quote_asset="KRW",
        selection_source="RANKED",
        buy_eligible=buy_eligible,
        sell_eligible=False,
        feature_data={},
    )
    session = MagicMock()

    def get(model: type[object], object_id: int) -> object | None:
        return {
            (TradeRecommendation, 1): row,
            (MarketUniverseCandidate, 10): candidate,
            (AnalysisRun, 2): recommendation_run,
            (AnalysisRun, 4): universe_run,
        }.get((model, object_id))

    session.get.side_effect = get
    return session


def test_dynamic_live_requires_successful_universe_pipeline_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dynamic_live(monkeypatch, dynamic_enabled=True)
    row = recommendation()
    session = build_session(row)
    invalid_run = AnalysisRun(
        id=4,
        user_id=3,
        pipeline_run_id="11111111-1111-1111-1111-111111111111",
        run_type="MANUAL",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    original_get = session.get.side_effect
    session.get.side_effect = lambda model, object_id: (
        invalid_run
        if (model, object_id) == (AnalysisRun, 4)
        else original_get(model, object_id)
    )

    with pytest.raises(LiveOrderSafetyError, match="valid successful pipeline stage"):
        LiveOrderExecutionService(session).build_execution_plan(row.id)


def test_dynamic_recommendation_cannot_bypass_checks_after_switch_to_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_static_live(monkeypatch)
    row = recommendation()

    with pytest.raises(LiveOrderSafetyError, match="runtime mode"):
        LiveOrderExecutionService(build_session(row)).build_execution_plan(row.id)


def test_dynamic_live_is_blocked_by_default_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dynamic_live(monkeypatch, dynamic_enabled=False)
    row = recommendation()

    with pytest.raises(LiveOrderSafetyError, match="disabled"):
        LiveOrderExecutionService(build_session(row)).build_execution_plan(row.id)


def test_dynamic_live_requires_persisted_eligible_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dynamic_live(monkeypatch, dynamic_enabled=True)
    row = recommendation()

    plan = LiveOrderExecutionService(build_session(row)).build_execution_plan(row.id)

    assert plan.market == "KRW-SOL"
    assert plan.ready_to_execute is True

    with pytest.raises(LiveOrderSafetyError, match="not BUY eligible"):
        LiveOrderExecutionService(
            build_session(row, buy_eligible=False)
        ).build_execution_plan(row.id)


def test_dynamic_live_rejects_recommendation_without_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_dynamic_live(monkeypatch, dynamic_enabled=True)
    row = recommendation(candidate_id=None)

    with pytest.raises(LiveOrderSafetyError, match="no persisted"):
        LiveOrderExecutionService(build_session(row)).build_execution_plan(row.id)


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    get_settings.cache_clear()
