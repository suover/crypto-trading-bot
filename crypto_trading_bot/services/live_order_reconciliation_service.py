from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderOperationError,
)
from crypto_trading_bot.services.live_order_execution_service import (
    map_upbit_order_state,
    recommendation_status_for_live_order,
)


class LiveOrderReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class LiveOrderReconciliationResult:
    recommendation: TradeRecommendation
    order_log: OrderLog
    identifier: str
    upbit_response: dict[str, Any]

    @property
    def upbit_state(self) -> str:
        return str(self.upbit_response.get("state") or "")


class LiveOrderReconciliationService:
    def __init__(
        self, session: Session, upbit_client: UpbitClient | None = None
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def reconcile(
        self, recommendation_id: int, *, commit: bool = True
    ) -> LiveOrderReconciliationResult:
        recommendation = self.session.scalar(
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update()
        )
        if recommendation is None:
            raise LiveOrderReconciliationError(
                f"Recommendation was not found. recommendation_id={recommendation_id}"
            )
        order_log = self.session.scalar(
            select(OrderLog)
            .where(OrderLog.recommendation_id == recommendation_id)
            .with_for_update()
        )
        if order_log is None or order_log.trading_mode != "LIVE":
            raise LiveOrderReconciliationError(
                f"LIVE order log was not found. recommendation_id={recommendation_id}"
            )

        identifier = f"recommendation-{recommendation_id}"
        try:
            if order_log.exchange_order_id:
                response = self.upbit_client.get_order(uuid=order_log.exchange_order_id)
            else:
                response = self.upbit_client.get_order(identifier=identifier)
        except UpbitOrderOperationError as error:
            raise LiveOrderReconciliationError(str(error)) from None

        order_log.status = map_upbit_order_state(response.get("state"))
        uuid_value = response.get("uuid")
        if uuid_value is not None:
            order_log.exchange_order_id = str(uuid_value)
        order_log.error_message = None
        existing_audit = dict(order_log.raw_response or {})
        existing_audit.update(
            {
                "identifier": identifier,
                "order_status_response": response,
                "manual_reconciliation": True,
            }
        )
        order_log.raw_response = existing_audit
        recommendation.status = recommendation_status_for_live_order(order_log.status)
        self.session.flush()
        if commit:
            self.session.commit()
            self.session.refresh(order_log)
            self.session.refresh(recommendation)
        return LiveOrderReconciliationResult(
            recommendation=recommendation,
            order_log=order_log,
            identifier=identifier,
            upbit_response=response,
        )
