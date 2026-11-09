"""Claim and deliver committed outbox rows with conservative failure handling."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    ShadowPolicyEnrollment,
    ShadowPolicyPromotionApproval,
    ShadowReviewNotification,
)
from crypto_trading_bot.notification.shadow_review_notification_message import (
    build_shadow_review_message,
)
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INVALID_REVIEW_DATA,
    SHADOW_REVIEW_GATE_V1,
    shadow_review_policy_signature,
)
from crypto_trading_bot.services.shadow_review_monitor_service import (
    ShadowReviewContext,
    ShadowReviewEligibility,
    ShadowReviewMonitorResult,
    read_outbox_counts,
    same_enrollment_identity,
)
from crypto_trading_bot.services.telegram_policy_review_tokens import (
    finalize_review_button,
    prepare_review_button,
)


CLAIM_STALE_AFTER = timedelta(minutes=15)
Row = ShadowReviewNotification


def _failure_status(error: Exception) -> tuple[str, str]:
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return "FAILED", "CONNECTION_NOT_ESTABLISHED"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in {400, 401, 403, 404, 429}:
            try:
                payload = error.response.json()
            except ValueError:
                payload = None
            if (
                isinstance(payload, dict)
                and payload.get("ok") is False
                and payload.get("error_code") == status
            ):
                return "FAILED", f"TELEGRAM_REJECTED_{status}"
    return "UNCERTAIN", "TELEGRAM_OUTCOME_UNKNOWN"


class ShadowReviewDeliveryService:
    def __init__(
        self,
        session_factory,
        *,
        settings: Settings,
        eligibility=None,
        telegram_factory=TelegramClient,
        now_fn=lambda: datetime.now(UTC),
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.eligibility = eligibility or ShadowReviewEligibility(settings)
        self.telegram_factory = telegram_factory
        self.now_fn = now_fn

    def process_due(self, result: ShadowReviewMonitorResult) -> None:
        if not (
            result.apply
            and self.settings.shadow_review_monitor_enabled
            and self.settings.shadow_review_telegram_notifications_enabled
        ):
            return
        destination = self.settings.shadow_review_destination
        if destination is None:
            raise ValueError("Telegram destination is required")
        now = self.now_fn()
        with self.session_factory() as session:
            context = ShadowReviewContext.load(session, self.settings)
            recovered = (
                session.execute(
                    update(Row)
                    .where(
                        *context.predicates(Row),
                        Row.delivery_status == "SENDING",
                        Row.claimed_at <= now - CLAIM_STALE_AFTER,
                    )
                    .values(
                        delivery_status="UNCERTAIN",
                        safe_failure_code="STALE_SENDING",
                        next_retry_at=None,
                        updated_at=now,
                    )
                    .returning(Row.id)
                )
                .scalars()
                .all()
            )
            if recovered:
                session.commit()
                result.database_write = True
                result.status = "PARTIAL"
                result.safe_reason = "stale delivery claims require review"

            ids = tuple(
                session.scalars(
                    select(Row.id)
                    .where(*self._due(context, destination, now))
                    .order_by(Row.next_retry_at.asc().nullsfirst(), Row.id)
                    .limit(self.settings.shadow_review_notification_max_per_cycle)
                )
            )

        try:
            if not ids:
                return
            # Construct only in delivery mode, before any row is claimed.
            telegram = self.telegram_factory(bot_token=self.settings.telegram_bot_token)
            for notification_id in ids:
                try:
                    claimed = self._claim(notification_id, context, destination, result)
                except SQLAlchemyError:
                    raise
                except Exception:
                    result.status = "PARTIAL"
                    result.safe_reason = "delivery revalidation failed"
                    result.execution_error_count += 1
                    continue
                if claimed is not None:
                    attempt, message, markup = claimed
                    self._send(
                        telegram,
                        notification_id,
                        attempt,
                        destination,
                        message,
                        result,
                        markup=markup,
                    )
        finally:
            with self.session_factory() as session:
                read_outbox_counts(session, context, result)

    def _due(self, context, destination, now):
        return (
            *context.predicates(Row),
            Row.notification_destination == destination,
            Row.delivery_attempt_count
            < 1 + self.settings.shadow_review_notification_max_retries,
            or_(
                Row.delivery_status == "PENDING",
                and_(Row.delivery_status == "FAILED", Row.next_retry_at <= now),
            ),
        )

    def _claim(self, notification_id, context, destination, result):
        with self.session_factory() as session:
            predicates = self._due(context, destination, self.now_fn())
            row = session.scalar(
                select(Row).where(Row.id == notification_id, *predicates)
            )
            if row is None:
                return None
            enrollment = session.get(ShadowPolicyEnrollment, row.shadow_enrollment_id)
            status, message = self._revalidate(session, row, enrollment, context)
            now = self.now_fn()
            statement = update(Row).where(Row.id == notification_id, *predicates)
            if message is None:
                suppressed = session.scalar(
                    statement.values(
                        delivery_status="SUPPRESSED",
                        safe_failure_code=status,
                        next_retry_at=None,
                        updated_at=now,
                    ).returning(Row.id)
                )
                if suppressed is not None:
                    session.commit()
                    result.database_write = True
                    if status == INVALID_REVIEW_DATA:
                        result.status = "PARTIAL"
                        result.safe_reason = "delivery provenance did not verify"
                return None

            # Atomic status transition under the row lock acquired by UPDATE.
            # Recheck approval in this statement after the potentially slow Gate.
            not_approved = (
                ~select(ShadowPolicyPromotionApproval.id)
                .where(ShadowPolicyPromotionApproval.candidate_id == Row.candidate_id)
                .exists()
            )
            attempt = session.scalar(
                statement.where(not_approved)
                .values(
                    delivery_status="SENDING",
                    delivery_attempt_count=Row.delivery_attempt_count + 1,
                    claimed_at=now,
                    next_retry_at=None,
                    safe_failure_code=None,
                    updated_at=now,
                )
                .returning(Row.delivery_attempt_count)
            )
            if attempt is None:
                return None
            markup = prepare_review_button(session, row, self.settings, now)
            session.commit()
            result.database_write = True
            return attempt, message, markup

    def _revalidate(self, session, row, enrollment, context):
        if (
            enrollment is None
            or not same_enrollment_identity(row, enrollment)
            or row.review_policy_schema_version != SHADOW_REVIEW_GATE_V1.schema_version
            or row.review_policy_signature
            != shadow_review_policy_signature(SHADOW_REVIEW_GATE_V1)
        ):
            return INVALID_REVIEW_DATA, None
        try:
            status, review = self.eligibility.evaluate(session, enrollment, context)
            if status == ELIGIBLE_FOR_PROMOTION_REVIEW and review is not None:
                return status, build_shadow_review_message(review)
            return status, None
        except ValueError, TypeError, KeyError, AttributeError:
            return INVALID_REVIEW_DATA, None

    def _send(
        self,
        telegram,
        notification_id,
        attempt,
        destination,
        message,
        result,
        *,
        markup,
    ):
        # The claim transaction has committed and its session is closed.
        result.external_calls = True
        message_id = None
        try:
            kwargs = {"reply_markup": markup} if markup is not None else {}
            reply = telegram.send_message(chat_id=destination, text=message, **kwargs)
            value = reply["message_id"]
            chat_id = reply["chat"]["id"]
            if (
                type(value) is not int
                or not 0 < value < 2**63
                or type(chat_id) is not int
                or str(chat_id) != destination
            ):
                raise ValueError("unverified Telegram message receipt")
            message_id = value
        except Exception as error:
            status, failure_code = _failure_status(error)
            result.status = "PARTIAL"
            result.safe_reason = "Telegram delivery did not confirm success"
        else:
            status, failure_code = "SENT", None
            result.telegram_messages_sent += 1

        now = self.now_fn()
        retry_at = None
        if (
            status == "FAILED"
            and attempt <= self.settings.shadow_review_notification_max_retries
        ):
            retry_at = now + timedelta(
                minutes=self.settings.shadow_review_retry_delays[attempt - 1]
            )
        # If this commit fails, never retry the HTTP call. SENDING remains
        # claimed until a subsequent recovery cycle marks it UNCERTAIN.
        with self.session_factory() as session:
            finalized = session.scalar(
                update(Row)
                .where(
                    Row.id == notification_id,
                    Row.delivery_status == "SENDING",
                    Row.delivery_attempt_count == attempt,
                )
                .values(
                    delivery_status=status,
                    telegram_message_id=message_id,
                    sent_at=now if message_id is not None else None,
                    next_retry_at=retry_at,
                    safe_failure_code=failure_code,
                    updated_at=now,
                )
                .returning(Row.id)
            )
            if finalized is None:
                raise RuntimeError("delivery claim changed before finalization")
            if markup is not None:
                finalize_review_button(
                    session,
                    notification_id=notification_id,
                    message_id=message_id,
                    retry_at=retry_at,
                    now=now,
                    settings=self.settings,
                )
            session.commit()
            result.database_write = True
