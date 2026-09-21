from collections.abc import Callable
from dataclasses import dataclass
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import OrderLog
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_reconciliation_service import (
    PENDING_LIVE_ORDER_STATUSES,
    LiveOrderReconciliationError,
    LiveOrderReconciliationService,
)


@dataclass(frozen=True)
class LiveOrderReconciliationCandidate:
    order_log_id: int
    recommendation_id: int


@dataclass(frozen=True)
class LiveOrderReconciliationWorkerResult:
    order_log_id: int
    recommendation_id: int
    outcome: str
    status: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class LiveOrderReconciliationWorkerService:
    """Poll existing orders only. Never call the order execution service."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        upbit_client: UpbitClient | None = None,
        *,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.session_factory = session_factory
        self.upbit_client = upbit_client
        self.sleep_fn = sleep_fn
        self._last_order_log_id = 0

    def get_candidates(
        self, limit: int = 20
    ) -> tuple[LiveOrderReconciliationCandidate, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        statement = (
            select(OrderLog.id, OrderLog.recommendation_id)
            .where(
                OrderLog.trading_mode == "LIVE",
                OrderLog.exchange == "UPBIT",
                OrderLog.status.in_(PENDING_LIVE_ORDER_STATUSES),
            )
            .order_by(OrderLog.id)
        )
        # Round-robin across cycles: old UNKNOWN orders must not starve later
        # orders. The cursor is process-local; no extra schema/audit writes needed.
        with self.session_factory() as session:
            rows = list(
                session.execute(
                    statement.where(OrderLog.id > self._last_order_log_id).limit(limit)
                ).all()
            )
            if len(rows) < limit and self._last_order_log_id:
                rows.extend(
                    session.execute(
                        statement.where(OrderLog.id <= self._last_order_log_id).limit(
                            limit - len(rows)
                        )
                    ).all()
                )
        return tuple(
            LiveOrderReconciliationCandidate(int(row[0]), int(row[1])) for row in rows
        )

    def reconcile_pending(
        self, limit: int = 20
    ) -> tuple[LiveOrderReconciliationWorkerResult, ...]:
        candidates = self.get_candidates(limit)
        results: list[LiveOrderReconciliationWorkerResult] = []
        for index, candidate in enumerate(candidates):
            if index:
                # Sequential GETs, capped near five/second even with a fast API.
                self.sleep_fn(0.2)
            with self.session_factory() as session:
                try:
                    result = LiveOrderReconciliationService(
                        session, upbit_client=self.upbit_client
                    ).reconcile(
                        candidate.recommendation_id, source="WORKER", commit=False
                    )
                except LiveOrderReconciliationError as error:
                    session.rollback()
                    safe = error.safe_error
                    results.append(
                        LiveOrderReconciliationWorkerResult(
                            candidate.order_log_id,
                            candidate.recommendation_id,
                            "UNRESOLVED",
                            error_type=type(error).__name__,
                            status_code=safe.status_code if safe else None,
                        )
                    )
                else:
                    if result is None:
                        session.rollback()
                        results.append(
                            LiveOrderReconciliationWorkerResult(
                                candidate.order_log_id,
                                candidate.recommendation_id,
                                "SKIPPED",
                            )
                        )
                    else:
                        status = result.order_log.status
                        session.commit()
                        results.append(
                            LiveOrderReconciliationWorkerResult(
                                candidate.order_log_id,
                                candidate.recommendation_id,
                                "SYNCED",
                                status=status,
                            )
                        )
            # Database/programming failures propagate. Only expected lookup or
            # identity errors are isolated per order, without logging raw errors.
            self._last_order_log_id = candidate.order_log_id
        return tuple(results)
