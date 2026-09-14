from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import AnalysisRun, OperationalAlert, OrderLog
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.operational.error_classifier import OperationalErrorEnvelope


KST = ZoneInfo("Asia/Seoul")
STALE_LIVE_ORDER_STATUSES = ("LIVE_PLACED", "LIVE_WAIT", "LIVE_UNKNOWN")


@dataclass(frozen=True)
class StaleLiveOrderCandidate:
    order_log_id: int
    recommendation_id: int
    user_id: int
    market: str
    side: str
    status: str
    created_at: datetime
    age_seconds: int


@dataclass(frozen=True)
class OperationalAlertDeliveryResult:
    alert_id: int
    delivery_status: str
    attempt_count: int
    next_retry_at: datetime | None


class OperationalAlertService:
    def __init__(self, session: Session, *, now_fn=lambda: datetime.now(KST)) -> None:
        self.session = session
        self.now_fn = now_fn

    def create_pipeline_failure(
        self,
        *,
        pipeline_run_id: str,
        step_module: str,
        envelope: OperationalErrorEnvelope,
        safe_message: str,
    ) -> OperationalAlert:
        dedup_key = f"PIPELINE_FAILURE:{pipeline_run_id}:{step_module}"
        existing = self.session.scalar(
            select(OperationalAlert).where(OperationalAlert.dedup_key == dedup_key)
        )
        if existing is not None:
            return existing
        analysis_run = self.session.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.pipeline_run_id == pipeline_run_id)
            .order_by(AnalysisRun.id.desc())
            .limit(1)
        )
        alert = OperationalAlert(
            alert_type="PIPELINE_FAILURE",
            severity="CRITICAL",
            user_id=analysis_run.user_id if analysis_run is not None else None,
            pipeline_run_id=pipeline_run_id,
            analysis_run_id=analysis_run.id if analysis_run is not None else None,
            error_category=envelope.category,
            error_code=envelope.code,
            http_status_code=envelope.http_status_code,
            safe_message=safe_message,
            dedup_key=dedup_key,
            delivery_status="PENDING",
            delivery_attempt_count=0,
        )
        self.session.add(alert)
        self.session.flush()
        return alert

    def create_canary_alert(
        self,
        *,
        alert_type: str,
        dedup_key: str,
        safe_message: str,
        user_id: int,
        recommendation_id: int | None = None,
    ) -> OperationalAlert:
        allowed = {
            "LIVE_CANARY_STARTED",
            "LIVE_CANARY_STOPPED",
            "LIVE_CANARY_BUY_LIMIT_BLOCKED",
            "LIVE_CANARY_PROVENANCE_INVALID",
        }
        if alert_type not in allowed:
            raise ValueError(f"Unsupported Canary alert type. alert_type={alert_type}")
        existing = self.session.scalar(
            select(OperationalAlert).where(OperationalAlert.dedup_key == dedup_key)
        )
        if existing is not None:
            return existing
        alert = OperationalAlert(
            alert_type=alert_type,
            severity="WARNING" if alert_type.endswith("STARTED") else "CRITICAL",
            user_id=user_id,
            recommendation_id=recommendation_id,
            safe_message=safe_message,
            dedup_key=dedup_key,
            delivery_status="PENDING",
            delivery_attempt_count=0,
        )
        self.session.add(alert)
        self.session.flush()
        return alert

    def find_stale_live_orders(
        self, *, stale_after_seconds: int, now: datetime | None = None
    ) -> tuple[StaleLiveOrderCandidate, ...]:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than 0")
        current = now or self.now_fn()
        threshold = current - timedelta(seconds=stale_after_seconds)
        with self.session.no_autoflush:
            rows = tuple(
                self.session.scalars(
                    select(OrderLog)
                    .where(
                        OrderLog.trading_mode == "LIVE",
                        OrderLog.exchange == "UPBIT",
                        OrderLog.status.in_(STALE_LIVE_ORDER_STATUSES),
                        OrderLog.created_at <= threshold,
                    )
                    .order_by(OrderLog.created_at, OrderLog.id)
                )
            )
        return tuple(
            StaleLiveOrderCandidate(
                order_log_id=row.id,
                recommendation_id=row.recommendation_id,
                user_id=row.user_id,
                market=row.market,
                side=row.side,
                status=row.status,
                created_at=row.created_at,
                age_seconds=max(
                    0,
                    int(
                        (
                            self._as_utc(current) - self._as_utc(row.created_at)
                        ).total_seconds()
                    ),
                ),
            )
            for row in rows
        )

    def create_stale_alerts(
        self, candidates: Sequence[StaleLiveOrderCandidate]
    ) -> tuple[OperationalAlert, ...]:
        created: list[OperationalAlert] = []
        for candidate in candidates:
            dedup_key = f"STALE_LIVE_ORDER:{candidate.order_log_id}"
            existing = self.session.scalar(
                select(OperationalAlert).where(OperationalAlert.dedup_key == dedup_key)
            )
            if existing is not None:
                continue
            alert = OperationalAlert(
                alert_type="STALE_LIVE_ORDER",
                severity="WARNING",
                user_id=candidate.user_id,
                order_log_id=candidate.order_log_id,
                recommendation_id=candidate.recommendation_id,
                safe_message=self.build_stale_message(candidate),
                dedup_key=dedup_key,
                delivery_status="PENDING",
                delivery_attempt_count=0,
            )
            self.session.add(alert)
            self.session.flush()
            created.append(alert)
        return tuple(created)

    def resolve_terminal_stale_alerts(self, *, now: datetime | None = None) -> int:
        current = now or self.now_fn()
        alerts = tuple(
            self.session.scalars(
                select(OperationalAlert)
                .join(OrderLog, OrderLog.id == OperationalAlert.order_log_id)
                .where(
                    OperationalAlert.alert_type == "STALE_LIVE_ORDER",
                    OperationalAlert.resolved_at.is_(None),
                    OrderLog.status.not_in(STALE_LIVE_ORDER_STATUSES),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for alert in alerts:
            alert.resolved_at = current
        return len(alerts)

    @staticmethod
    def build_stale_message(candidate: StaleLiveOrderCandidate) -> str:
        approximate_minutes = max(1, candidate.age_seconds // 60)
        return "\n".join(
            [
                "[LIVE 주문 상태 장기 미확정]",
                "",
                f"OrderLog ID: {candidate.order_log_id}",
                f"Recommendation ID: {candidate.recommendation_id}",
                f"마켓: {candidate.market}",
                f"방향: {candidate.side}",
                f"현재 상태: {candidate.status}",
                f"미확정 시간: 약 {approximate_minutes}분",
                "",
                "기존 주문의 최종 상태를 확인하지 못하고 있습니다.",
                "자동 재주문이나 자동 취소는 수행하지 않았습니다.",
                "Upbit 주문 내역과 reconciliation 로그를 확인해 주세요.",
            ]
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        from datetime import UTC

        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )


class OperationalAlertDeliveryService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        telegram_client: TelegramClient | None = None,
        telegram_chat_id: str,
        max_retries: int,
        retry_delays_minutes: Sequence[int],
        now_fn=lambda: datetime.now(KST),
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if len(retry_delays_minutes) < max_retries or any(
            delay <= 0 for delay in retry_delays_minutes
        ):
            raise ValueError("retry delays must cover max_retries and be positive")
        self.session_factory = session_factory
        self.telegram_client = telegram_client
        self.telegram_chat_id = telegram_chat_id
        self.max_retries = max_retries
        self.retry_delays_minutes = tuple(retry_delays_minutes)
        self.now_fn = now_fn

    def process_due(
        self, *, limit: int = 100
    ) -> tuple[OperationalAlertDeliveryResult, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        now = self.now_fn()
        with self.session_factory() as session:
            alert_ids = tuple(
                session.scalars(
                    select(OperationalAlert.id)
                    .where(
                        OperationalAlert.resolved_at.is_(None),
                        OperationalAlert.delivery_attempt_count < 1 + self.max_retries,
                        or_(
                            OperationalAlert.delivery_status == "PENDING",
                            and_(
                                OperationalAlert.delivery_status == "FAILED",
                                OperationalAlert.next_retry_at.is_not(None),
                                OperationalAlert.next_retry_at <= now,
                            ),
                        ),
                    )
                    .order_by(
                        OperationalAlert.next_retry_at.asc().nullsfirst(),
                        OperationalAlert.id,
                    )
                    .limit(limit)
                )
            )
        results = (self._deliver(alert_id) for alert_id in alert_ids)
        return tuple(result for result in results if result is not None)

    def deliver_alert(self, alert_id: int) -> OperationalAlertDeliveryResult | None:
        return self._deliver(alert_id)

    def _deliver(self, alert_id: int) -> OperationalAlertDeliveryResult | None:
        with self.session_factory() as session:
            alert = session.scalar(
                select(OperationalAlert)
                .where(OperationalAlert.id == alert_id)
                .with_for_update(skip_locked=True)
            )
            if alert is None or alert.delivery_status == "SENT":
                return None
            if alert.resolved_at is not None:
                return None
            now = self.now_fn()
            if alert.delivery_status == "FAILED" and (
                alert.next_retry_at is None
                or OperationalAlertService._as_utc(alert.next_retry_at)
                > OperationalAlertService._as_utc(now)
            ):
                return None
            if alert.delivery_attempt_count >= 1 + self.max_retries:
                return None
            alert.delivery_attempt_count += 1
            try:
                client = self.telegram_client or TelegramClient()
                if not self.telegram_chat_id:
                    raise ValueError("Telegram chat is not configured")
                client.send_message(
                    chat_id=self.telegram_chat_id, text=alert.safe_message
                )
            except Exception:
                alert.delivery_status = "FAILED"
                retry_index = alert.delivery_attempt_count - 1
                alert.next_retry_at = (
                    now + timedelta(minutes=self.retry_delays_minutes[retry_index])
                    if retry_index < self.max_retries
                    else None
                )
                alert.sent_at = None
            else:
                alert.delivery_status = "SENT"
                alert.next_retry_at = None
                alert.sent_at = now
            result = OperationalAlertDeliveryResult(
                alert.id,
                alert.delivery_status,
                alert.delivery_attempt_count,
                alert.next_retry_at,
            )
            session.commit()
            return result
