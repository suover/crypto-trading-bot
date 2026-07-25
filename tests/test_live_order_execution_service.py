from decimal import Decimal
from typing import Any

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_STATUS,
    LiveOrderExecutionError,
    LiveOrderExecutionService,
)
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderNotFoundError,
    UpbitSafeError,
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
    monkeypatch.setenv(
        "UPBIT_SECRET_KEY",
        "test-secret-key-for-live-order-service-unit-test-0123456789abcdef",
    )
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
    def __init__(
        self,
        recommendation: TradeRecommendation | None,
        order_log: OrderLog | None = None,
        today_live_order_amount_krw: Decimal = Decimal("0"),
    ) -> None:
        self.recommendation = recommendation
        self.order_log = order_log
        self.today_live_order_amount_krw = today_live_order_amount_krw
        self.added_objects: list[object] = []
        self.committed = False
        self.flushed = False
        self.refreshed_objects: list[object] = []

    def get(
        self,
        model: type[TradeRecommendation],
        object_id: int,
    ) -> TradeRecommendation | None:
        assert model is TradeRecommendation
        assert object_id == 1

        return self.recommendation

    def scalar(
        self,
        statement: object,
    ) -> object:
        statement_text = str(statement)

        if "sum(order_logs.amount_krw)" in statement_text:
            return self.today_live_order_amount_krw

        if "order_logs" in statement_text:
            return self.order_log

        return self.recommendation

    def add(
        self,
        instance: object,
    ) -> None:
        self.added_objects.append(instance)

        if isinstance(instance, OrderLog):
            instance.id = 10
            self.order_log = instance

    def flush(self) -> None:
        self.flushed = True

    def commit(self) -> None:
        self.committed = True

    def refresh(
        self,
        instance: object,
    ) -> None:
        self.refreshed_objects.append(instance)


class FakeUpbitClient:
    def __init__(
        self,
        accounts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.buy_orders: list[dict[str, Any]] = []
        self.sell_orders: list[dict[str, Any]] = []
        self.accounts = accounts or []
        self.lookup_calls: list[dict[str, str | None]] = []

    def get_accounts(self) -> list[dict[str, Any]]:
        return self.accounts

    def get_order(
        self,
        *,
        uuid: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        self.lookup_calls.append({"uuid": uuid, "identifier": identifier})
        if identifier is not None and not self.buy_orders and not self.sell_orders:
            raise UpbitOrderNotFoundError(
                UpbitSafeError(
                    error_type="HTTPStatusError",
                    operation="get_order",
                    status_code=404,
                    message="Order not found",
                )
            )
        return {
            "uuid": uuid or "recovered-order-uuid",
            "identifier": identifier,
        }

    def create_market_buy_order(
        self,
        market: str,
        amount_krw: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        order = {
            "market": market,
            "amount_krw": amount_krw,
            "identifier": identifier,
        }
        self.buy_orders.append(order)

        return {
            "uuid": "live-buy-order-uuid",
            "market": market,
            "side": "bid",
            "ord_type": "price",
            "price": str(amount_krw),
            "identifier": identifier,
        }

    def create_market_sell_order(
        self,
        market: str,
        quantity: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        order = {
            "market": market,
            "quantity": quantity,
            "identifier": identifier,
        }
        self.sell_orders.append(order)

        return {
            "uuid": "live-sell-order-uuid",
            "market": market,
            "side": "ask",
            "ord_type": "market",
            "volume": str(quantity),
            "identifier": identifier,
        }


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


def test_execute_places_live_buy_order_and_records_order_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation()
    fake_session = FakeSession(
        recommendation,
        today_live_order_amount_krw=Decimal("25000"),
    )
    fake_upbit_client = FakeUpbitClient()

    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.already_executed is False
    assert recommendation.status == "LIVE_EXECUTED"
    assert fake_session.flushed is True
    assert fake_session.committed is True
    assert len(fake_upbit_client.buy_orders) == 1
    assert fake_upbit_client.buy_orders[0] == {
        "market": "KRW-BTC",
        "amount_krw": Decimal("5000"),
        "identifier": "recommendation-1",
    }

    order_log = result.order_log

    assert order_log.trading_mode == "LIVE"
    assert order_log.approval_request_id == 20
    assert order_log.side == "BUY"
    assert order_log.order_type == "MARKET"
    assert order_log.amount_krw == Decimal("5000")
    assert order_log.quantity is None
    assert order_log.status == LIVE_ORDER_STATUS
    assert order_log.exchange_order_id == "live-buy-order-uuid"
    assert order_log.raw_response["actual_order_executed"] is True


def test_execute_places_live_sell_order_and_records_order_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    fake_session = FakeSession(recommendation)
    fake_upbit_client = FakeUpbitClient(
        accounts=[
            {
                "currency": "BTC",
                "balance": "0.0002",
                "locked": "0",
            }
        ]
    )

    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.already_executed is False
    assert recommendation.status == "LIVE_EXECUTED"
    assert len(fake_upbit_client.sell_orders) == 1
    assert fake_upbit_client.sell_orders[0] == {
        "market": "KRW-BTC",
        "quantity": Decimal("0.0001"),
        "identifier": "recommendation-1",
    }

    order_log = result.order_log

    assert order_log.trading_mode == "LIVE"
    assert order_log.approval_request_id == 20
    assert order_log.side == "SELL"
    assert order_log.order_type == "MARKET"
    assert order_log.amount_krw is None
    assert order_log.quantity == Decimal("0.0001")
    assert order_log.status == LIVE_ORDER_STATUS
    assert order_log.exchange_order_id == "live-sell-order-uuid"


def test_execute_blocks_live_sell_when_available_balance_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0002"),
    )
    fake_session = FakeSession(recommendation)
    fake_upbit_client = FakeUpbitClient(
        accounts=[
            {
                "currency": "BTC",
                "balance": "0.0001",
                "locked": "0",
            }
        ]
    )

    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderExecutionError,
        match="Insufficient available balance",
    ):
        service.execute(
            recommendation_id=1,
            approval_request_id=20,
        )

    assert fake_upbit_client.sell_orders == []
    assert fake_upbit_client.buy_orders == []
    assert fake_session.added_objects == []
    assert fake_session.committed is False
    assert fake_session.flushed is False
    assert recommendation.status == "APPROVED"


def test_execute_blocks_live_sell_when_account_balance_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    fake_session = FakeSession(recommendation)
    fake_upbit_client = FakeUpbitClient(
        accounts=[
            {
                "currency": "ETH",
                "balance": "1",
                "locked": "0",
            }
        ]
    )

    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderExecutionError,
        match="account balance was not found",
    ):
        service.execute(
            recommendation_id=1,
            approval_request_id=20,
        )

    assert fake_upbit_client.sell_orders == []
    assert fake_upbit_client.buy_orders == []
    assert fake_session.added_objects == []
    assert fake_session.committed is False
    assert fake_session.flushed is False


def test_execute_returns_existing_order_log_without_duplicate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation()
    existing_order_log = OrderLog(
        id=99,
        recommendation_id=1,
        approval_request_id=20,
        user_id=1,
        trading_mode="LIVE",
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        amount_krw=Decimal("5000"),
        quantity=None,
        price=None,
        status=LIVE_ORDER_STATUS,
        exchange_order_id="existing-order-id",
        error_message=None,
        raw_response={},
    )
    fake_upbit_client = FakeUpbitClient()

    service = LiveOrderExecutionService(
        session=FakeSession(recommendation, existing_order_log),  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.already_executed is True
    assert result.order_log is existing_order_log
    assert fake_upbit_client.buy_orders == []
    assert fake_upbit_client.sell_orders == []


def test_execute_blocks_when_daily_live_order_amount_limit_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)

    recommendation = build_recommendation(
        recommended_amount_krw=Decimal("6000"),
    )
    fake_session = FakeSession(
        recommendation,
        today_live_order_amount_krw=Decimal("25000"),
    )
    fake_upbit_client = FakeUpbitClient()

    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=fake_upbit_client,  # type: ignore[arg-type]
    )

    with pytest.raises(
        LiveOrderExecutionError,
        match="Daily live order amount limit exceeded",
    ):
        service.execute(
            recommendation_id=1,
            approval_request_id=20,
        )

    assert fake_upbit_client.buy_orders == []
    assert fake_upbit_client.sell_orders == []
    assert fake_session.added_objects == []
    assert fake_session.committed is False
    assert fake_session.flushed is False


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
