"""Authenticate and persist policy callbacks, isolated from trading approval."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    ShadowPolicyEnrollment,
    ShadowReviewNotification,
    TelegramPolicyReviewRequest,
)
from crypto_trading_bot.notification.telegram_policy_review_message import (
    build_policy_review_detail,
    confirmation_keyboard,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ShadowPolicyPromotionApprovalService,
    load_and_validate_shadow_policy_promotion_approval,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    INVALID_REVIEW_DATA,
)
from crypto_trading_bot.services.shadow_review_monitor_service import (
    ShadowReviewContext,
    ShadowReviewEligibility,
    approval_exists,
    same_enrollment_identity,
)
from crypto_trading_bot.services.telegram_policy_review_provenance import (
    notification_matches,
    review_evidence_fingerprint,
)
from crypto_trading_bot.services.telegram_policy_review_tokens import (
    confirmation_token,
    parse_policy_callback,
    token_hash,
)


STALE_TEXT = "정책 검토 근거가 변경되었거나 현재 검토할 수 없습니다. 최신 Shadow Review 결과를 확인해 주세요."
EXPIRED_TEXT = (
    "정책 검토 요청이 만료되었습니다. 최신 Shadow Review 검증을 다시 수행해 주세요."
)
APPROVED_TEXT = "정책 승인 기록이 저장되었습니다. LIVE Ranking은 그대로이며 실제 적용은 별도 수동 절차입니다."
REJECTED_TEXT = "정책 승인이 거절되었습니다. LIVE Ranking은 그대로입니다."


@dataclass
class TelegramPolicyReviewResult:
    callback_type: str | None = None
    request_status: str = "DENIED"
    request_id: int | None = None
    candidate_id: int | None = None
    shadow_enrollment_id: int | None = None
    review_status: str | None = None
    review_signature_verified: bool = False
    evidence_unchanged: bool | None = None
    authorized: bool = False
    token_valid: bool = False
    token_expired: bool = False
    already_processed: bool = False
    approval_status: str | None = None
    promotion_approval_id: int | None = None
    approval_signature_verified: bool = False
    human_approval_recorded: bool = False
    database_write: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    live_orders_created: int = 0
    safe_reason: str | None = None
    answer: str = field(
        default="이 정책 요청을 처리할 권한이 없거나 유효하지 않습니다.", repr=False
    )
    message: str | None = field(default=None, repr=False)
    markup: dict | None = field(default=None, repr=False)


class TelegramPolicyReviewService:
    def __init__(
        self,
        session_factory,
        *,
        settings: Settings,
        eligibility=None,
        now_fn=lambda: datetime.now(UTC),
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.eligibility = eligibility or ShadowReviewEligibility(settings)
        self.now_fn = now_fn

    def process(self, callback: dict) -> TelegramPolicyReviewResult:
        result = TelegramPolicyReviewResult()
        actor = callback.get("from")
        message = callback.get("message")
        data = callback.get("data")
        if not (
            self.settings.policy_telegram_review_enabled
            and isinstance(actor, dict)
            and isinstance(message, dict)
            and isinstance(data, str)
            and message.get("date") != 0
        ):
            return result
        actor_id = actor.get("id")
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        message_id = message.get("message_id")
        if (
            type(actor_id) is not int
            or actor_id not in self.settings.policy_telegram_admin_ids
            or type(chat_id) is not int
            or chat.get("type") != "private"
            or chat_id != actor_id
            or str(chat_id) != self.settings.shadow_review_destination
            or type(message_id) is not int
            or not 0 < message_id < 2**63
        ):
            return result
        try:
            command, token = parse_policy_callback(data)
        except ValueError:
            return result
        result.callback_type = command
        result.authorized = True
        initial = command == "policy_review"
        if not initial and not self.settings.policy_telegram_approval_enabled:
            result.request_status = "DISABLED"
            result.answer = "Telegram 정책 확정 기능이 꺼져 있습니다."
            return result

        row = TelegramPolicyReviewRequest
        digest = token_hash("review" if initial else "decision", token)
        token_column = row.review_token_hash if initial else row.confirmation_token_hash
        with self.session_factory() as session:
            request = session.scalar(
                select(row).where(token_column == digest).with_for_update()
            )
            if request is None or (
                request.telegram_chat_id != chat_id
                or request.telegram_message_id != message_id
                or (
                    request.authorized_telegram_user_id is not None
                    and request.authorized_telegram_user_id != actor_id
                )
            ):
                return result
            notice = session.get(ShadowReviewNotification, request.notification_id)
            if not notification_matches(request, notice):
                return result

            context = ShadowReviewContext.load(session, self.settings)
            if (notice.user_id, notice.exchange, notice.quote_asset) != context.key:
                return result

            result.token_valid = True
            result.request_id = request.id
            result.candidate_id = request.candidate_id
            result.shadow_enrollment_id = request.shadow_enrollment_id
            result.request_status = request.status
            expected_status = "PENDING" if initial else "REVIEWED"
            if request.status != expected_status:
                self._already_processed(session, result, request, command)
                return result
            expires = (
                request.review_expires_at
                if initial
                else request.confirmation_expires_at
            )
            if expires is None:
                return result
            if self.now_fn() >= expires:
                self._close(session, request, result, "EXPIRED", EXPIRED_TEXT)
                return result

            enrollment = session.get(
                ShadowPolicyEnrollment, request.shadow_enrollment_id
            )
            review = None
            status = INVALID_REVIEW_DATA
            if enrollment is not None and same_enrollment_identity(notice, enrollment):
                try:
                    status, review = self.eligibility.evaluate(
                        session, enrollment, context
                    )
                except ValueError, AttributeError, KeyError, TypeError:
                    status = INVALID_REVIEW_DATA
            result.review_status = status
            if (
                status != ELIGIBLE_FOR_PROMOTION_REVIEW
                or review is None
                or review.review_policy_signature != request.review_policy_signature
            ):
                self._close(session, request, result, "STALE", STALE_TEXT)
                return result
            result.review_signature_verified = True

            if initial:
                self._review(session, request, result, review, actor_id, expires)
            elif command == "policy_reject":
                result.evidence_unchanged = (
                    review_evidence_fingerprint(session, review)
                    == request.evidence_fingerprint
                )
                if self.now_fn() >= expires:
                    self._close(session, request, result, "EXPIRED", EXPIRED_TEXT)
                elif not result.evidence_unchanged or approval_exists(
                    session, request.candidate_id
                ):
                    self._close(session, request, result, "STALE", STALE_TEXT)
                else:
                    self._close(session, request, result, "REJECTED", REJECTED_TEXT)
            else:
                self._approve(session, request, result, review, expires)
        return result

    def _review(self, session, request, result, review, actor_id, expires):
        fingerprint = review_evidence_fingerprint(session, review)
        can_decide = self.settings.policy_telegram_approval_enabled
        message = build_policy_review_detail(review, can_decide=can_decide)
        now = self.now_fn()
        if now >= expires:
            self._close(session, request, result, "EXPIRED", EXPIRED_TEXT)
            return
        request.authorized_telegram_user_id = actor_id
        request.reviewed_at = request.updated_at = now
        request.evidence_fingerprint = fingerprint
        request.status = "REVIEWED"
        markup = {"inline_keyboard": []}
        if can_decide:
            token, request.confirmation_token_hash = confirmation_token()
            request.confirmation_expires_at = now + timedelta(
                seconds=self.settings.policy_telegram_confirmation_ttl_seconds
            )
            markup = confirmation_keyboard(token)
        session.commit()
        result.request_status = "REVIEWED"
        result.answer = "정책 상세 검토를 표시합니다."
        result.message = message
        result.markup = markup
        result.database_write = True

    def _approve(self, session, request, result, review, expires):
        if self.now_fn() >= expires:
            self._close(session, request, result, "EXPIRED", EXPIRED_TEXT)
            return
        value = ShadowPolicyPromotionApprovalService(
            session, now_fn=self.now_fn
        ).approve_telegram(request=request, review=review)
        result.approval_status = value.approval_status
        if not value.promotion_approval_created:
            if self.now_fn() >= expires:
                self._close(session, request, result, "EXPIRED", EXPIRED_TEXT)
            else:
                self._close(session, request, result, "STALE", STALE_TEXT)
            return
        approval_id = value.approval.id
        session.flush()
        load_and_validate_shadow_policy_promotion_approval(session, approval_id)
        session.commit()
        result.promotion_approval_id = approval_id
        result.approval_signature_verified = True
        result.evidence_unchanged = True
        result.human_approval_recorded = True
        self._result_message(
            result, "APPROVED", f"{APPROVED_TEXT}\n승인 ID: {approval_id}"
        )
        result.database_write = True

    def _close(self, session, request, result, status, message):
        request.status = status
        request.decided_at = request.updated_at = self.now_fn()
        session.commit()
        self._result_message(result, status, message)
        result.token_expired = status == "EXPIRED"
        result.database_write = True

    @staticmethod
    def _result_message(result, status, message):
        result.request_status = status
        result.answer = message
        result.message = f"{message}\nCandidate ID: {result.candidate_id}"
        result.markup = {"inline_keyboard": []}

    @staticmethod
    def _already_processed(session, result, request, command):
        result.already_processed = True
        result.answer = "이미 검토했거나 처리된 정책 요청입니다."
        if request.status == "APPROVED" and command == "policy_confirm":
            load_and_validate_shadow_policy_promotion_approval(
                session, request.approval_id
            )
            TelegramPolicyReviewService._result_message(
                result, "APPROVED", f"{APPROVED_TEXT}\n승인 ID: {request.approval_id}"
            )
            result.promotion_approval_id = request.approval_id
            result.approval_signature_verified = True
            result.human_approval_recorded = True
        elif request.status == "REJECTED" and command == "policy_reject":
            TelegramPolicyReviewService._result_message(
                result, "REJECTED", REJECTED_TEXT
            )
        elif request.status == "EXPIRED":
            TelegramPolicyReviewService._result_message(result, "EXPIRED", EXPIRED_TEXT)
            result.token_expired = True
