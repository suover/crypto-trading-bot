from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    OrderExecutionAttempt,
    OrderRetryNotification,
)
from crypto_trading_bot.notification.telegram_client import (
    TelegramClient,
)
from crypto_trading_bot.services.mock_order_retry_notification_service import (
    MockOrderRetryNotificationService,
)
from crypto_trading_bot.services.mock_order_retry_service import (
    MockOrderRetryResult,
)


KST = ZoneInfo("Asia/Seoul")

NOTIFICATION_RETRY_DELAYS_MINUTES = (
    1,
    5,
    15,
    60,
)

MAX_NOTIFICATION_RETRIES = len(NOTIFICATION_RETRY_DELAYS_MINUTES)

PROCESSING_TIMEOUT_MINUTES = 5
MAX_DELIVERY_LIMIT = 100


DeliveryResultStatus = Literal[
    "SENT",
    "FAILED",
]


@dataclass(frozen=True)
class OrderRetryNotificationClaim:
    notification_id: int
    attempt_id: int
    retry_status: str


@dataclass(frozen=True)
class OrderRetryNotificationDeliveryResult:
    notification_id: int
    attempt_id: int
    retry_status: str
    delivery_status: DeliveryResultStatus
    retry_count: int
    next_retry_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class OrderRetryNotificationDeliverySummary:
    results: tuple[
        OrderRetryNotificationDeliveryResult,
        ...,
    ]

    @property
    def processed_count(self) -> int:
        return len(self.results)

    @property
    def sent_count(self) -> int:
        return sum(1 for result in self.results if result.delivery_status == "SENT")

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.delivery_status == "FAILED")


class OrderRetryNotificationOutboxService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        telegram_client: TelegramClient | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.telegram_client = telegram_client

    def process_due(
        self,
        limit: int = 100,
    ) -> OrderRetryNotificationDeliverySummary:
        self._validate_limit(limit)

        claims = self._claim_due_notifications(
            limit=limit,
        )

        if not claims:
            return OrderRetryNotificationDeliverySummary(
                results=(),
            )

        try:
            resolved_telegram_client = self.telegram_client or TelegramClient()
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"

            results = tuple(
                self._record_claim_failure(
                    claim=claim,
                    error_message=error_message,
                )
                for claim in claims
            )

            return OrderRetryNotificationDeliverySummary(
                results=results,
            )

        delivery_results: list[OrderRetryNotificationDeliveryResult] = []

        for claim in claims:
            try:
                result = self._deliver_notification(
                    claim=claim,
                    telegram_client=(resolved_telegram_client),
                )
            except Exception as error:
                result = self._record_claim_failure(
                    claim=claim,
                    error_message=(f"{type(error).__name__}: {error}"),
                )

            delivery_results.append(result)

        return OrderRetryNotificationDeliverySummary(
            results=tuple(delivery_results),
        )

    def _claim_due_notifications(
        self,
        limit: int,
    ) -> tuple[
        OrderRetryNotificationClaim,
        ...,
    ]:
        now = datetime.now(KST)

        processing_timeout_at = now - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)

        statement = (
            select(OrderRetryNotification)
            .where(
                or_(
                    OrderRetryNotification.delivery_status == "PENDING",
                    and_(
                        OrderRetryNotification.delivery_status == "FAILED",
                        OrderRetryNotification.next_retry_at.is_not(None),
                        OrderRetryNotification.next_retry_at <= now,
                        OrderRetryNotification.retry_count <= MAX_NOTIFICATION_RETRIES,
                    ),
                    and_(
                        OrderRetryNotification.delivery_status == "PROCESSING",
                        OrderRetryNotification.updated_at <= processing_timeout_at,
                        OrderRetryNotification.retry_count <= MAX_NOTIFICATION_RETRIES,
                    ),
                )
            )
            .order_by(
                OrderRetryNotification.next_retry_at.asc().nullsfirst(),
                OrderRetryNotification.id.asc(),
            )
            .limit(limit)
            .with_for_update(
                skip_locked=True,
            )
        )

        with self.session_factory() as session:
            notifications = session.scalars(statement).all()

            claims = tuple(
                OrderRetryNotificationClaim(
                    notification_id=notification.id,
                    attempt_id=notification.attempt_id,
                    retry_status=notification.retry_status,
                )
                for notification in notifications
            )

            for notification in notifications:
                notification.delivery_status = "PROCESSING"
                notification.next_retry_at = None
                notification.error_message = None
                notification.sent_at = None

            session.commit()

        return claims

    def _deliver_notification(
        self,
        claim: OrderRetryNotificationClaim,
        telegram_client: TelegramClient,
    ) -> OrderRetryNotificationDeliveryResult:
        with self.session_factory() as session:
            notification = session.get(
                OrderRetryNotification,
                claim.notification_id,
            )

            if notification is None:
                return self._build_missing_result(
                    claim=claim,
                    error_message=("Order retry notification was not found"),
                )

            if notification.delivery_status != "PROCESSING":
                return OrderRetryNotificationDeliveryResult(
                    notification_id=notification.id,
                    attempt_id=notification.attempt_id,
                    retry_status=(notification.retry_status),
                    delivery_status="FAILED",
                    retry_count=(notification.retry_count),
                    next_retry_at=(notification.next_retry_at),
                    error_message=(
                        "Order retry notification "
                        "is not in PROCESSING status. "
                        f"status="
                        f"{notification.delivery_status}"
                    ),
                )

            attempt = session.get(
                OrderExecutionAttempt,
                notification.attempt_id,
            )

            if attempt is None:
                return self._mark_failed(
                    session=session,
                    notification=notification,
                    error_message=(
                        "Order execution attempt "
                        "was not found. "
                        f"attempt_id="
                        f"{notification.attempt_id}"
                    ),
                )

            context_error = self._validate_delivery_context(
                notification=notification,
                attempt=attempt,
            )

            if context_error is not None:
                return self._mark_failed(
                    session=session,
                    notification=notification,
                    error_message=context_error,
                )

            retry_result = MockOrderRetryResult(
                recommendation_id=(attempt.recommendation_id),
                approval_request_id=(attempt.approval_request_id),
                attempt_id=attempt.id,
                attempt_number=(attempt.attempt_number),
                status=attempt.status,
                order_log_id=attempt.order_log_id,
                error_code=attempt.error_code,
                error_message=attempt.error_message,
                next_retry_at=attempt.next_retry_at,
            )

            notification_service = MockOrderRetryNotificationService(
                session=session,
                telegram_client=telegram_client,
            )

            send_result = notification_service.notify(
                retry_result=retry_result,
                telegram_chat_id=(notification.telegram_chat_id),
            )

            if send_result.notification_status == "SENT":
                return self._mark_sent(
                    session=session,
                    notification=notification,
                )

            failure_message = send_result.error_message or (
                "Telegram notification was not sent. "
                f"notification_status="
                f"{send_result.notification_status}"
            )

            return self._mark_failed(
                session=session,
                notification=notification,
                error_message=failure_message,
            )

    def _record_claim_failure(
        self,
        claim: OrderRetryNotificationClaim,
        error_message: str,
    ) -> OrderRetryNotificationDeliveryResult:
        with self.session_factory() as session:
            notification = session.get(
                OrderRetryNotification,
                claim.notification_id,
            )

            if notification is None:
                return self._build_missing_result(
                    claim=claim,
                    error_message=error_message,
                )

            if notification.delivery_status != "PROCESSING":
                return OrderRetryNotificationDeliveryResult(
                    notification_id=notification.id,
                    attempt_id=notification.attempt_id,
                    retry_status=(notification.retry_status),
                    delivery_status="FAILED",
                    retry_count=(notification.retry_count),
                    next_retry_at=(notification.next_retry_at),
                    error_message=(
                        "Could not record delivery "
                        "failure because notification "
                        "is not in PROCESSING status. "
                        f"status="
                        f"{notification.delivery_status}; "
                        f"original_error="
                        f"{error_message}"
                    ),
                )

            return self._mark_failed(
                session=session,
                notification=notification,
                error_message=error_message,
            )

    @staticmethod
    def _validate_delivery_context(
        notification: OrderRetryNotification,
        attempt: OrderExecutionAttempt,
    ) -> str | None:
        if attempt.recommendation_id != notification.recommendation_id:
            return (
                "Notification recommendation ID "
                "does not match execution attempt. "
                f"notification_recommendation_id="
                f"{notification.recommendation_id}, "
                f"attempt_recommendation_id="
                f"{attempt.recommendation_id}"
            )

        if attempt.approval_request_id != notification.approval_request_id:
            return (
                "Notification approval request ID "
                "does not match execution attempt. "
                f"notification_approval_request_id="
                f"{notification.approval_request_id}, "
                f"attempt_approval_request_id="
                f"{attempt.approval_request_id}"
            )

        if attempt.status != notification.retry_status:
            return (
                "Notification retry status "
                "does not match execution attempt. "
                f"notification_retry_status="
                f"{notification.retry_status}, "
                f"attempt_status={attempt.status}"
            )

        return None

    @staticmethod
    def _mark_sent(
        session: Session,
        notification: OrderRetryNotification,
    ) -> OrderRetryNotificationDeliveryResult:
        sent_at = datetime.now(KST)

        notification.delivery_status = "SENT"
        notification.next_retry_at = None
        notification.error_message = None
        notification.sent_at = sent_at

        session.commit()

        return OrderRetryNotificationDeliveryResult(
            notification_id=notification.id,
            attempt_id=notification.attempt_id,
            retry_status=notification.retry_status,
            delivery_status="SENT",
            retry_count=notification.retry_count,
            next_retry_at=None,
            error_message=None,
        )

    @staticmethod
    def _mark_failed(
        session: Session,
        notification: OrderRetryNotification,
        error_message: str,
    ) -> OrderRetryNotificationDeliveryResult:
        now = datetime.now(KST)

        new_retry_count = notification.retry_count + 1

        notification.delivery_status = "FAILED"
        notification.retry_count = new_retry_count
        notification.error_message = error_message
        notification.sent_at = None

        if new_retry_count <= MAX_NOTIFICATION_RETRIES:
            delay_minutes = NOTIFICATION_RETRY_DELAYS_MINUTES[new_retry_count - 1]

            notification.next_retry_at = now + timedelta(minutes=delay_minutes)
        else:
            notification.next_retry_at = None

        session.commit()

        return OrderRetryNotificationDeliveryResult(
            notification_id=notification.id,
            attempt_id=notification.attempt_id,
            retry_status=notification.retry_status,
            delivery_status="FAILED",
            retry_count=new_retry_count,
            next_retry_at=notification.next_retry_at,
            error_message=error_message,
        )

    @staticmethod
    def _build_missing_result(
        claim: OrderRetryNotificationClaim,
        error_message: str,
    ) -> OrderRetryNotificationDeliveryResult:
        return OrderRetryNotificationDeliveryResult(
            notification_id=claim.notification_id,
            attempt_id=claim.attempt_id,
            retry_status=claim.retry_status,
            delivery_status="FAILED",
            retry_count=0,
            next_retry_at=None,
            error_message=error_message,
        )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit <= 0:
            raise ValueError(f"limit must be greater than 0. limit={limit}")

        if limit > MAX_DELIVERY_LIMIT:
            raise ValueError(
                f"limit must not exceed {MAX_DELIVERY_LIMIT}. limit={limit}"
            )
