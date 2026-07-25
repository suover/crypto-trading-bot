from decimal import Decimal

import pytest

from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_WAIT_STATUS,
)
from crypto_trading_bot.services.live_order_reconciliation_service import (
    LiveOrderReconciliationService,
)


class FakeSession:
    def __init__(self, recommendation, order_log) -> None:
        self.values = [recommendation, order_log]
        self.committed = False

    def scalar(self, statement):
        return self.values.pop(0)

    def flush(self) -> None:
        pass

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
    ("state", "expected"),
    [("done", LIVE_ORDER_DONE_STATUS), ("wait", LIVE_ORDER_WAIT_STATUS)],
)
def test_reconcile_maps_state_and_updates_audit(state: str, expected: str) -> None:
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
    assert result.recommendation.status == "LIVE_EXECUTED"
    assert result.order_log.raw_response["manual_reconciliation"] is True
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
