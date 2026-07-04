from decimal import Decimal

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    LiveOrderExecutionError,
    LiveOrderExecutionService,
)
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
    LiveOrderSafetyError,
)


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def set_live_order_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: str = "true",
    confirmation: str = LIVE_ORDER_CONFIRMATION_TEXT,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "secret")
    monkeypatch.setenv("ALLOWED_MARKETS", "KRW-BTC,KRW-ETH")
    monkeypatch.setenv("MAX_ORDER_AMOUNT_KRW", "10000")
    monkeypatch.setenv("DAILY_MAX_ORDER_AMOUNT_KRW", "30000")
    monkeypatch.setenv("LIVE_ORDER_ENABLED", enabled)
    monkeypatch.setenv("LIVE_ORDER_CONFIRMATION", confirmation)

    clear_settings_cache()


def build_recommendation(
    *,
    recommendation_id: int = 1,
    exchange: str = "UPBIT",
    market: str = "KRW-BTC",
    action: str = "BUY",
    status: str = "APPROVED",
    recommended_amount_krw: Decimal | None = Decimal("5000"),
    recommended_quantity: Decimal | None = None,
) -> TradeRecommendation:
    return TradeRecommendation(
        id=recommendation_id,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange=exchange,
        market=market,
        action=action,
        confidence=Decimal("0.7500"),
        reason="test recommendation",
        recommended_amount_krw=recommended_amount_krw,
        recommended_quantity=recommended_quantity,
        ai_model="TEST",
        ai_response={},
        status=status,
    )


class FakeSession:
    def __init__(self, recommendation: TradeRecommendation | None) -> None:
        self.recommendation = recommendation

    def get(
        self,
        model: type[TradeRecommendation],
        object_id: int,
    ) -> TradeRecommendation | None:
        assert model is TradeRecommendation
        assert object_id == 1

        return self.recommendation


def test_build_execution_plan_allows_valid_live_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation()),  # type: ignore[arg-type]
    )

    plan = service.build_execution_plan(
        recommendation_id=1,
    )

    assert plan.recommendation_id == 1
    assert plan.exchange == "UPBIT"
    assert plan.market == "KRW-BTC"
    assert plan.action == "BUY"
    assert plan.amount_krw == Decimal("5000")
    assert plan.quantity is None
    assert plan.ready_to_execute is True


def test_build_execution_plan_blocks_when_live_order_safety_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(
        monkeypatch,
        enabled="false",
    )

    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation()),  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderSafetyError,
        match="live_order_enabled is false",
    ):
        service.build_execution_plan(
            recommendation_id=1,
        )


def test_build_execution_plan_blocks_unapproved_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation(status="CREATED")),  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderExecutionError,
        match="must be APPROVED",
    ):
        service.build_execution_plan(
            recommendation_id=1,
        )


def test_build_execution_plan_blocks_unsupported_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation(exchange="BINANCE")),  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderExecutionError,
        match="only supports UPBIT",
    ):
        service.build_execution_plan(
            recommendation_id=1,
        )


def test_build_execution_plan_allows_valid_live_sell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )

    service = LiveOrderExecutionService(
        session=FakeSession(recommendation),  # type: ignore[arg-type]
    )

    plan = service.build_execution_plan(
        recommendation_id=1,
    )

    assert plan.action == "SELL"
    assert plan.amount_krw is None
    assert plan.quantity == Decimal("0.0001")
    assert plan.ready_to_execute is True


def test_execute_is_intentionally_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation()),  # type: ignore[arg-type]
    )

    with pytest.raises(
        NotImplementedError,
        match="intentionally not implemented",
    ):
        service.execute(
            recommendation_id=1,
        )


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
