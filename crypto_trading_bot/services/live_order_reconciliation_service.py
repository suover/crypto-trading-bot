from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderOperationError,
    UpbitSafeError,
)
from crypto_trading_bot.services.live_order_execution_service import (
    map_upbit_order_state,
    recommendation_status_for_live_order,
)


PENDING_LIVE_ORDER_STATUSES = ("LIVE_PLACED", "LIVE_WAIT", "LIVE_UNKNOWN")


class LiveOrderReconciliationError(ValueError):
    def __init__(
        self, message: str, *, safe_error: UpbitSafeError | None = None
    ) -> None:
        super().__init__(message)
        self.safe_error = safe_error


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
        self,
        recommendation_id: int,
        *,
        commit: bool = True,
        source: Literal["MANUAL", "WORKER"] = "MANUAL",
    ) -> LiveOrderReconciliationResult | None:
        if source not in {"MANUAL", "WORKER"}:
            raise ValueError("Unsupported reconciliation source")
        # Match execution's lock order. Workers skip busy rows and recheck the
        # status under lock; manual reconciliation remains explicitly available.
        worker = source == "WORKER"
        recommendation = self.session.scalar(
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update(skip_locked=worker)
        )
        if recommendation is None:
            if worker:
                return None
            raise LiveOrderReconciliationError(
                f"Recommendation was not found. recommendation_id={recommendation_id}"
            )
        order_log = self.session.scalar(
            select(OrderLog)
            .where(OrderLog.recommendation_id == recommendation_id)
            .with_for_update(skip_locked=worker)
        )
        if worker and order_log is None:
            return None
        if order_log is None or order_log.trading_mode != "LIVE":
            raise LiveOrderReconciliationError(
                f"LIVE order log was not found. recommendation_id={recommendation_id}"
            )
        if (
            order_log.exchange != "UPBIT"
            or recommendation.exchange != "UPBIT"
            or order_log.user_id != recommendation.user_id
            or order_log.market != recommendation.market
        ):
            raise LiveOrderReconciliationError(
                "LIVE order identity does not match UPBIT recommendation"
            )
        if worker and order_log.status not in PENDING_LIVE_ORDER_STATUSES:
            return None

        identifier = f"recommendation-{recommendation_id}"
        try:
            if order_log.exchange_order_id:
                response = self.upbit_client.get_order(uuid=order_log.exchange_order_id)
            else:
                response = self.upbit_client.get_order(identifier=identifier)
        except UpbitOrderOperationError as error:
            raise LiveOrderReconciliationError(
                "Existing order lookup was not confirmed", safe_error=error.safe_error
            ) from None

        order_log.status = map_upbit_order_state(
            response.get("state"), response.get("executed_volume")
        )
        uuid_value = response.get("uuid")
        if uuid_value is not None:
            order_log.exchange_order_id = str(uuid_value)
        order_log.error_message = None
        existing_audit = dict(order_log.raw_response or {})
        existing_audit.pop("manual_reconciliation", None)
        existing_audit.update(
            {
                "identifier": identifier,
                "order_status_response": response,
                "reconciliation_source": source,
                "reconciled_at": datetime.now(UTC).isoformat(),
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
