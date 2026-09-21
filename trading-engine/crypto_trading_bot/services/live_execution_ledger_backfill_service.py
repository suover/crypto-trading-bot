from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import OrderLog
from crypto_trading_bot.services.live_execution_ledger_service import (
    LiveExecutionLedgerService,
    UpbitLiveExecutionNormalizer,
)


@dataclass(frozen=True)
class LiveExecutionLedgerBackfillSummary:
    processed_count: int
    eligible_count: int
    applied_count: int
    skipped_count: int
    error_count: int
    normalized_fill_count: int


class LiveExecutionLedgerBackfillService:
    """Normalize only stored order-status JSON. This service has no API client."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def run(
        self, *, apply: bool = False, limit: int | None = None
    ) -> LiveExecutionLedgerBackfillSummary:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than 0")
        statement = (
            select(OrderLog.id)
            .where(
                OrderLog.trading_mode == "LIVE",
                OrderLog.exchange == "UPBIT",
            )
            .order_by(OrderLog.id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self.session_factory() as session:
            order_log_ids = tuple(int(value) for value in session.scalars(statement))

        processed = eligible = applied = skipped = errors = fill_count = 0
        for order_log_id in order_log_ids:
            processed += 1
            try:
                with self.session_factory() as session:
                    order_statement = select(OrderLog).where(
                        OrderLog.id == order_log_id,
                        OrderLog.trading_mode == "LIVE",
                        OrderLog.exchange == "UPBIT",
                    )
                    if apply:
                        order_statement = order_statement.with_for_update()
                    order_log = session.scalar(order_statement)
                    response = self._stored_order_status_response(order_log)
                    if response is None:
                        skipped += 1
                        continue
                    normalized = UpbitLiveExecutionNormalizer.normalize(response)
                    if not normalized.has_data:
                        skipped += 1
                        continue
                    eligible += 1
                    fill_count += len(normalized.fills)
                    if apply:
                        LiveExecutionLedgerService(session).sync(order_log, response)
                        session.commit()
                        applied += 1
            except Exception:
                # Per-row failures are counted without printing payloads, DB URLs,
                # credentials, or raw exception messages.
                errors += 1
        return LiveExecutionLedgerBackfillSummary(
            processed_count=processed,
            eligible_count=eligible,
            applied_count=applied,
            skipped_count=skipped,
            error_count=errors,
            normalized_fill_count=fill_count,
        )

    @staticmethod
    def _stored_order_status_response(
        order_log: OrderLog | None,
    ) -> dict[str, object] | None:
        if order_log is None or not isinstance(order_log.raw_response, dict):
            return None
        response = order_log.raw_response.get("order_status_response")
        return response if isinstance(response, dict) else None
