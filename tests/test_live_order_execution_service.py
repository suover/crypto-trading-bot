from decimal import Decimal
from typing import Any

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderFill, OrderLog, TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    COUNTED_DAILY_LIVE_ORDER_STATUSES,
    LIVE_ORDER_CANCELLED_STATUS,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_PLACED_STATUS,
    LIVE_ORDER_STATUS,
    LIVE_ORDER_WAIT_STATUS,
    LiveOrderExecutionError,
    LiveOrderExecutionService,
    map_upbit_order_state,
    outcome_for_live_order_status,
    recommendation_status_for_live_order,
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
    trade_ratio: object = Decimal("1"),
) -> TradeRecommendation:
    return TradeRecommendation(
        id=recommendation_id,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange=exchange,
        market=market,
        action=action,
        trade_ratio=trade_ratio,
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
        self.daily_amount_statement: object | None = None

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
            self.daily_amount_statement = statement
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

    def add_all(self, instances: list[object]) -> None:
        self.added_objects.extend(instances)

    def scalars(self, statement: object) -> object:
        class EmptyScalarResult:
            @staticmethod
            def all() -> list[str]:
                return []

        return EmptyScalarResult()

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
        tickers: list[dict[str, Any]] | None = None,
        remote_order: dict[str, Any] | None = None,
        created_order_response: dict[str, Any] | None = None,
    ) -> None:
        self.buy_orders: list[dict[str, Any]] = []
        self.sell_orders: list[dict[str, Any]] = []
        self.accounts = accounts or []
        self.remote_order = remote_order
        self.created_order_response = created_order_response
        self.account_calls = 0
        self.tickers = (
            tickers
            if tickers is not None
            else [{"market": "KRW-BTC", "trade_price": "100000000"}]
        )
        self.ticker_calls: list[list[str]] = []
        self.lookup_calls: list[dict[str, str | None]] = []

    def get_accounts(self) -> list[dict[str, Any]]:
        self.account_calls += 1
        return self.accounts

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        self.ticker_calls.append(markets)
        return self.tickers

    def get_order(
        self,
        *,
        uuid: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        self.lookup_calls.append({"uuid": uuid, "identifier": identifier})
        if identifier is not None and self.remote_order is not None:
            return self.remote_order
        if identifier is not None and not self.buy_orders and not self.sell_orders:
            raise UpbitOrderNotFoundError(
                UpbitSafeError(
                    error_type="HTTPStatusError",
                    operation="get_order",
                    status_code=404,
                    message="Order not found",
                )
            )
        if uuid is not None and self.created_order_response is not None:
            return self.created_order_response
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


@pytest.mark.parametrize(
    ("state", "executed_volume", "expected"),
    [
        ("done", None, LIVE_ORDER_DONE_STATUS),
        ("wait", None, LIVE_ORDER_WAIT_STATUS),
        ("watch", None, LIVE_ORDER_WAIT_STATUS),
        ("cancel", 0, LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "0", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", None, LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "invalid", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "NaN", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "Infinity", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "-Infinity", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "-1", LIVE_ORDER_CANCELLED_STATUS),
        ("cancel", "0.00371471", LIVE_ORDER_EXECUTED_CANCELLED_STATUS),
        ("unexpected", "1", LIVE_ORDER_PLACED_STATUS),
    ],
)
def test_map_upbit_order_state_uses_confirmed_execution_volume(
    state: object, executed_volume: object, expected: str
) -> None:
    assert map_upbit_order_state(state, executed_volume) == expected


def test_executed_cancelled_is_confirmed_and_recommendation_is_executed() -> None:
    assert (
        recommendation_status_for_live_order(LIVE_ORDER_EXECUTED_CANCELLED_STATUS)
        == "LIVE_EXECUTED"
    )
    assert outcome_for_live_order_status(LIVE_ORDER_EXECUTED_CANCELLED_STATUS) == (
        "CONFIRMED"
    )


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
    assert recommendation.status == "LIVE_EXECUTION_PENDING"
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


def test_execute_records_executed_cancel_without_duplicate_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation()
    session = FakeSession(recommendation)
    order_response = {
        "uuid": "partially-filled-order-uuid",
        "state": "cancel",
        "executed_volume": "0.00371471",
        "executed_funds": "9999.99932",
        "paid_fee": "4.99999966",
        "remaining_volume": "0.001",
        "trades_count": 1,
        "trades": [
            {
                "uuid": "trade-1",
                "price": "2691980",
                "volume": "0.00371471",
                "funds": "9999.99932",
                "side": "bid",
            }
        ],
    }
    client = FakeUpbitClient(created_order_response=order_response)
    service = LiveOrderExecutionService(
        session=session,  # type: ignore[arg-type]
        upbit_client=client,  # type: ignore[arg-type]
    )

    result = service.execute(1, approval_request_id=20)
    repeated = service.execute(1, approval_request_id=20)

    assert result.order_log.status == LIVE_ORDER_EXECUTED_CANCELLED_STATUS
    assert result.recommendation.status == "LIVE_EXECUTED"
    assert result.confirmed is True
    assert result.failed is False
    assert result.unknown is False
    assert result.pending is False
    assert result.order_log.raw_response["order_status_response"] == order_response
    assert result.order_log.amount_krw == Decimal("5000")
    assert result.order_log.executed_quantity == Decimal("0.00371471")
    assert result.order_log.executed_funds_krw == Decimal("9999.99932")
    assert result.order_log.average_execution_price == (
        Decimal("9999.99932") / Decimal("0.00371471")
    )
    assert result.order_log.paid_fee == Decimal("4.99999966")
    assert result.order_log.remaining_quantity == Decimal("0.001")
    assert result.order_log.trades_count == 1
    assert result.order_log.execution_synced_at is not None
    (fill,) = [item for item in session.added_objects if isinstance(item, OrderFill)]
    assert fill.exchange_trade_id == "trade-1"
    assert fill.raw_data == order_response["trades"][0]
    assert repeated.already_executed is True
    assert repeated.order_log.status == LIVE_ORDER_EXECUTED_CANCELLED_STATUS
    assert repeated.confirmed is True
    assert repeated.pending is False
    assert len(client.buy_orders) == 1


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
    assert recommendation.status == "LIVE_EXECUTION_PENDING"
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
    assert order_log.amount_krw == Decimal("10000.0000")
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
    assert fake_session.daily_amount_statement is not None
    compiled = fake_session.daily_amount_statement.compile()
    assert "BUY" in compiled.params.values()


def test_daily_live_order_total_query_includes_only_buy_logs() -> None:
    fake_session = FakeSession(
        build_recommendation(),
        today_live_order_amount_krw=Decimal("5000"),
    )
    service = LiveOrderExecutionService(session=fake_session)  # type: ignore[arg-type]

    total = service._get_today_live_order_amount_krw(user_id=1)

    assert total == Decimal("5000")
    assert fake_session.daily_amount_statement is not None
    compiled = fake_session.daily_amount_statement.compile()
    assert "order_logs.side" in str(compiled)
    assert "BUY" in compiled.params.values()
    assert LIVE_ORDER_EXECUTED_CANCELLED_STATUS in COUNTED_DAILY_LIVE_ORDER_STATUSES


def test_buy_is_not_blocked_by_same_day_sell_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(recommended_amount_krw=Decimal("5000"))
    fake_session = FakeSession(
        recommendation,
        today_live_order_amount_krw=Decimal("0"),
    )
    client = FakeUpbitClient()
    service = LiveOrderExecutionService(
        session=fake_session,  # type: ignore[arg-type]
        upbit_client=client,  # type: ignore[arg-type]
    )

    result = service.execute(1, approval_request_id=20)

    assert result.order_log.side == "BUY"
    assert client.buy_orders[0]["amount_krw"] == Decimal("5000")
    assert fake_session.daily_amount_statement is not None
    compiled = fake_session.daily_amount_statement.compile()
    assert "BUY" in compiled.params.values()


@pytest.mark.parametrize(
    "tickers",
    [
        [],
        [{"market": "KRW-ETH", "trade_price": "100000000"}],
        [
            {"market": "KRW-BTC", "trade_price": "100000000"},
            {"market": "KRW-BTC", "trade_price": "100000000"},
        ],
    ],
)
def test_execute_sell_requires_exactly_one_matching_ticker(
    monkeypatch: pytest.MonkeyPatch,
    tickers: list[dict[str, Any]],
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    client = FakeUpbitClient(
        accounts=[{"currency": "BTC", "balance": "1"}], tickers=tickers
    )
    service = LiveOrderExecutionService(
        session=FakeSession(recommendation),
        upbit_client=client,  # type: ignore[arg-type]
    )
    with pytest.raises(LiveOrderExecutionError, match="exactly one matching ticker"):
        service.execute(1, approval_request_id=20)
    assert client.ticker_calls == [["KRW-BTC"]]
    assert client.sell_orders == []


@pytest.mark.parametrize("price", [None, "bad", "0", "-1", "NaN", "Infinity"])
def test_execute_sell_rejects_invalid_current_price(
    monkeypatch: pytest.MonkeyPatch, price: object
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    client = FakeUpbitClient(
        accounts=[{"currency": "BTC", "balance": "1"}],
        tickers=[{"market": "KRW-BTC", "trade_price": price}],
    )
    service = LiveOrderExecutionService(
        session=FakeSession(recommendation),
        upbit_client=client,  # type: ignore[arg-type]
    )
    with pytest.raises(LiveOrderExecutionError, match="ticker price"):
        service.execute(1, approval_request_id=20)
    assert client.sell_orders == []


def test_sell_is_not_blocked_by_daily_buy_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    client = FakeUpbitClient(accounts=[{"currency": "BTC", "balance": "1"}])
    service = LiveOrderExecutionService(
        session=FakeSession(
            recommendation, today_live_order_amount_krw=Decimal("30000")
        ),
        upbit_client=client,  # type: ignore[arg-type]
    )
    result = service.execute(1, approval_request_id=20)
    assert result.order_log.amount_krw == Decimal("10000")
    assert client.sell_orders[0]["quantity"] == Decimal("0.0001")


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()


@pytest.mark.parametrize("balance", ["NaN", "Infinity", "-Infinity", "-1"])
def test_execute_blocks_live_sell_when_balance_is_non_finite_or_negative(
    monkeypatch: pytest.MonkeyPatch, balance: str
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action="SELL",
        recommended_amount_krw=None,
        recommended_quantity=Decimal("0.0001"),
    )
    client = FakeUpbitClient(accounts=[{"currency": "BTC", "balance": balance}])
    service = LiveOrderExecutionService(
        session=FakeSession(recommendation), upbit_client=client
    )
    with pytest.raises(LiveOrderExecutionError, match="balance is invalid"):
        service.execute(1, approval_request_id=20)
    assert client.sell_orders == []


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_live_null_trade_ratio_is_rejected_without_order_or_external_call(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action=action,
        recommended_amount_krw=Decimal("5000") if action == "BUY" else None,
        recommended_quantity=Decimal("0.0001") if action == "SELL" else None,
        trade_ratio=None,
    )
    session = FakeSession(recommendation)
    client = FakeUpbitClient(accounts=[{"currency": "BTC", "balance": "1"}])
    service = LiveOrderExecutionService(session=session, upbit_client=client)
    with pytest.raises(LiveOrderExecutionError, match="trade_ratio"):
        service.execute(1, approval_request_id=20)
    assert session.added_objects == []
    assert client.buy_orders == []
    assert client.sell_orders == []
    assert client.lookup_calls == [{"uuid": None, "identifier": "recommendation-1"}]
    assert client.ticker_calls == []


@pytest.mark.parametrize(
    "ratio",
    [
        "bad",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("-0.1"),
        Decimal("1.1"),
    ],
)
def test_live_invalid_trade_ratio_is_rejected_without_order_or_external_call(
    monkeypatch: pytest.MonkeyPatch, ratio: object
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(trade_ratio=ratio)
    session = FakeSession(recommendation)
    client = FakeUpbitClient()
    service = LiveOrderExecutionService(session=session, upbit_client=client)
    with pytest.raises(LiveOrderExecutionError, match="trade_ratio"):
        service.execute(1, approval_request_id=20)
    assert session.added_objects == []
    assert client.buy_orders == []
    assert client.sell_orders == []
    assert client.lookup_calls == [{"uuid": None, "identifier": "recommendation-1"}]


@pytest.mark.parametrize("ratio", [Decimal("0.1"), Decimal("1")])
def test_live_valid_trade_ratio_preserves_execution_plan(
    monkeypatch: pytest.MonkeyPatch, ratio: Decimal
) -> None:
    set_live_order_env(monkeypatch)
    service = LiveOrderExecutionService(
        session=FakeSession(build_recommendation(trade_ratio=ratio))
    )
    assert service.build_execution_plan(1).ready_to_execute is True


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_remote_order_recovery_bypasses_new_order_ratio_and_market_state_checks(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(
        action=action,
        recommended_amount_krw=Decimal("5000") if action == "BUY" else Decimal("10000"),
        recommended_quantity=Decimal("0.0001") if action == "SELL" else None,
        trade_ratio=None,
    )
    session = FakeSession(recommendation)
    client = FakeUpbitClient(
        accounts=[{"currency": "BTC", "balance": "NaN"}],
        tickers=[{"market": "KRW-BTC", "trade_price": "NaN"}],
        remote_order={
            "uuid": "existing-remote-order-uuid",
            "identifier": "recommendation-1",
            "state": "done",
        },
    )
    service = LiveOrderExecutionService(session=session, upbit_client=client)

    result = service.execute(1, approval_request_id=20)

    assert result.recovered is True
    assert result.already_executed is False
    assert result.order_log.exchange_order_id == "existing-remote-order-uuid"
    assert result.order_log.raw_response["recovered_by_identifier"] is True
    assert result.order_log.amount_krw == recommendation.recommended_amount_krw
    assert result.order_log.quantity == recommendation.recommended_quantity
    assert client.buy_orders == []
    assert client.sell_orders == []
    assert client.ticker_calls == []
    assert client.account_calls == 0
    assert client.lookup_calls == [{"uuid": None, "identifier": "recommendation-1"}]


def test_valid_ratio_remote_order_is_recovered_without_duplicate_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_order_env(monkeypatch)
    recommendation = build_recommendation(trade_ratio=Decimal("0.5"))
    session = FakeSession(recommendation)
    client = FakeUpbitClient(
        remote_order={
            "uuid": "existing-remote-order-uuid",
            "identifier": "recommendation-1",
            "state": "wait",
        }
    )
    service = LiveOrderExecutionService(session=session, upbit_client=client)

    result = service.execute(1, approval_request_id=20)

    assert result.recovered is True
    assert result.order_log.exchange_order_id == "existing-remote-order-uuid"
    assert client.buy_orders == []
    assert client.sell_orders == []
    assert client.lookup_calls == [{"uuid": None, "identifier": "recommendation-1"}]
