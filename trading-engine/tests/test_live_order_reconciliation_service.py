from decimal import Decimal

import pytest

from crypto_trading_bot.db.models import OrderFill, OrderLog, TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_WAIT_STATUS,
)
from crypto_trading_bot.services.live_order_reconciliation_service import (
    LiveOrderReconciliationService,
)


class FakeSession:
    def __init__(self, recommendation, order_log) -> None:
        self.values = [recommendation, order_log]
        self.added_objects = []
        self.committed = False

    def scalar(self, statement):
        return self.values.pop(0)

    def flush(self) -> None:
        pass

    def add_all(self, instances) -> None:
        self.added_objects.extend(instances)

    def scalars(self, statement):
        class EmptyScalarResult:
            @staticmethod
            def all():
                return []

        return EmptyScalarResult()

    def commit(self) -> None:
        self.committed = True

    def refresh(self, instance) -> None:
        pass


class FakeClient:
    def __init__(self, response) -> None:
        self.response = response
        self.lookups = []

    def get_order(self, **lookup):
        self.lookups.append(lookup)
        return self.response

    def create_market_buy_order(self, **kwargs):
        raise AssertionError("Reconciliation must never create an order")


def build_models(exchange_order_id: str | None = "uuid-1"):
    recommendation = TradeRecommendation(
        id=1,
        analysis_run_id=1,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
        confidence=Decimal("0.8"),
        recommended_amount_krw=Decimal("5000"),
        ai_response={},
        status="LIVE_EXECUTION_UNKNOWN",
    )
    order_log = OrderLog(
        id=2,
        recommendation_id=1,
        user_id=1,
        trading_mode="LIVE",
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        amount_krw=Decimal("5000"),
        status="LIVE_UNKNOWN",
        exchange_order_id=exchange_order_id,
        raw_response={"actual_order_executed": None},
    )
    return recommendation, order_log


@pytest.mark.parametrize(
    ("state", "expected", "recommendation_status"),
    [
        ("done", LIVE_ORDER_DONE_STATUS, "LIVE_EXECUTED"),
        ("wait", LIVE_ORDER_WAIT_STATUS, "LIVE_EXECUTION_PENDING"),
    ],
)
def test_reconcile_maps_state_and_updates_audit(
    state: str, expected: str, recommendation_status: str
) -> None:
    recommendation, order_log = build_models()
    session = FakeSession(recommendation, order_log)
    client = FakeClient(
        {
            "uuid": "uuid-1",
            "state": state,
            "executed_volume": "0.0001",
            "paid_fee": "2.5",
        }
    )
    result = LiveOrderReconciliationService(
        session=session,  # type: ignore[arg-type]
        upbit_client=client,  # type: ignore[arg-type]
    ).reconcile(1)
    assert client.lookups == [{"uuid": "uuid-1"}]
    assert result.order_log.status == expected
    assert result.recommendation.status == recommendation_status
    assert result.order_log.raw_response["reconciliation_source"] == "MANUAL"
    assert "manual_reconciliation" not in result.order_log.raw_response
    assert session.committed is True


def test_reconcile_uses_identifier_without_saved_uuid() -> None:
    recommendation, order_log = build_models(exchange_order_id=None)
    client = FakeClient({"uuid": "found-uuid", "state": "done"})
    result = LiveOrderReconciliationService(
        session=FakeSession(recommendation, order_log),  # type: ignore[arg-type]
        upbit_client=client,  # type: ignore[arg-type]
    ).reconcile(1)
    assert client.lookups == [{"identifier": "recommendation-1"}]
    assert result.order_log.exchange_order_id == "found-uuid"


def test_reconcile_marks_cancel_with_execution_as_confirmed_execution() -> None:
    recommendation, order_log = build_models()
    response = {
        "uuid": "uuid-1",
        "state": "cancel",
        "executed_volume": "0.00371471",
        "paid_fee": "4.99999966",
    }
    result = LiveOrderReconciliationService(
        session=FakeSession(recommendation, order_log),  # type: ignore[arg-type]
        upbit_client=FakeClient(response),  # type: ignore[arg-type]
    ).reconcile(1)

    assert result.order_log.status == LIVE_ORDER_EXECUTED_CANCELLED_STATUS
    assert result.recommendation.status == "LIVE_EXECUTED"
    assert result.order_log.raw_response["order_status_response"] == response


def test_reconcile_wait_then_done_updates_execution_summary_and_fills() -> None:
    recommendation, order_log = build_models()
    partial_response = {
        "uuid": "uuid-1",
        "state": "wait",
        "executed_volume": "1",
        "executed_funds": "100",
        "trades_count": 1,
        "trades": [
            {
                "uuid": "trade-a",
                "price": "100",
                "volume": "1",
                "funds": "100",
            }
        ],
    }
    partial_session = FakeSession(recommendation, order_log)
    LiveOrderReconciliationService(
        session=partial_session,  # type: ignore[arg-type]
        upbit_client=FakeClient(partial_response),  # type: ignore[arg-type]
    ).reconcile(1)

    assert order_log.status == LIVE_ORDER_WAIT_STATUS
    assert order_log.executed_quantity == Decimal("1")
    assert order_log.executed_funds_krw == Decimal("100")
    assert order_log.average_execution_price == Decimal("100")
    assert [
        item.exchange_trade_id
        for item in partial_session.added_objects
        if isinstance(item, OrderFill)
    ] == ["trade-a"]

    done_response = {
        "uuid": "uuid-1",
        "state": "done",
        "executed_volume": "2",
        "executed_funds": "300",
        "paid_fee": "0.15",
        "remaining_volume": "0",
        "trades_count": 2,
        "trades": [
            {
                "uuid": "trade-b",
                "price": "200",
                "volume": "1",
                "funds": "200",
            }
        ],
    }
    done_session = FakeSession(recommendation, order_log)
    LiveOrderReconciliationService(
        session=done_session,  # type: ignore[arg-type]
        upbit_client=FakeClient(done_response),  # type: ignore[arg-type]
    ).reconcile(1)

    assert order_log.status == LIVE_ORDER_DONE_STATUS
    assert recommendation.status == "LIVE_EXECUTED"
    assert order_log.executed_quantity == Decimal("2")
    assert order_log.executed_funds_krw == Decimal("300")
    assert order_log.average_execution_price == Decimal("150")
    assert order_log.paid_fee == Decimal("0.15")
    assert order_log.remaining_quantity == Decimal("0")
    assert order_log.trades_count == 2
