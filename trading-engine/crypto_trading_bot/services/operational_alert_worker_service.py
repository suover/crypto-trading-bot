from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertDeliveryService,
    OperationalAlertService,
)


@dataclass(frozen=True)
class OperationalAlertWorkerResult:
    stale_order_count: int
    created_alert_count: int
    resolved_alert_count: int
    delivered_count: int
    failed_delivery_count: int


class OperationalAlertWorkerService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        telegram_chat_id: str,
        stale_after_seconds: int,
        max_retries: int,
        retry_delays_minutes: Sequence[int],
        telegram_client: TelegramClient | None = None,
        now_fn: Callable[[], datetime],
    ) -> None:
        self.session_factory = session_factory
        self.telegram_chat_id = telegram_chat_id
        self.stale_after_seconds = stale_after_seconds
        self.max_retries = max_retries
        self.retry_delays_minutes = retry_delays_minutes
        self.telegram_client = telegram_client
        self.now_fn = now_fn

    def run_cycle(self) -> OperationalAlertWorkerResult:
        now = self.now_fn()
        with self.session_factory() as session:
            service = OperationalAlertService(session, now_fn=self.now_fn)
            candidates = service.find_stale_live_orders(
                stale_after_seconds=self.stale_after_seconds, now=now
            )
            created = service.create_stale_alerts(candidates)
            resolved_count = service.resolve_terminal_stale_alerts(now=now)
            session.commit()
        delivery = OperationalAlertDeliveryService(
            self.session_factory,
            telegram_client=self.telegram_client,
            telegram_chat_id=self.telegram_chat_id,
            max_retries=self.max_retries,
            retry_delays_minutes=self.retry_delays_minutes,
            now_fn=self.now_fn,
        ).process_due()
        return OperationalAlertWorkerResult(
            stale_order_count=len(candidates),
            created_alert_count=len(created),
            resolved_alert_count=resolved_count,
            delivered_count=sum(
                result is not None and result.delivery_status == "SENT"
                for result in delivery
            ),
            failed_delivery_count=sum(
                result is not None and result.delivery_status == "FAILED"
                for result in delivery
            ),
        )
