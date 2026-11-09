"""Short-lived Telegram policy tokens tied to a verified notification receipt."""

from datetime import datetime, timedelta
from hashlib import sha256
import re
from secrets import token_urlsafe

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    ShadowReviewNotification,
    TelegramPolicyReviewRequest,
)


POLICY_COMMANDS = frozenset({"policy_review", "policy_confirm", "policy_reject"})


def token_hash(stage: str, token: str) -> str:
    return sha256(f"{stage}:{token}".encode("ascii")).hexdigest()


def parse_policy_callback(data: str) -> tuple[str, str]:
    command, _, token = data.partition(":")
    if command not in POLICY_COMMANDS or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("Invalid policy callback")
    return command, token


def confirmation_token() -> tuple[str, str]:
    token = token_urlsafe(32)
    return token, token_hash("decision", token)


def prepare_review_button(
    session: Session,
    notification: ShadowReviewNotification,
    settings: Settings,
    now: datetime,
) -> dict | None:
    if not (
        settings.policy_telegram_review_enabled
        and int(notification.notification_destination)
        in settings.policy_telegram_admin_ids
    ):
        return None
    row = _request_for_notification(session, notification.id)
    token = token_urlsafe(32)
    digest = token_hash("review", token)
    if row is None:
        session.add(
            TelegramPolicyReviewRequest(
                notification_id=notification.id,
                candidate_id=notification.candidate_id,
                shadow_enrollment_id=notification.shadow_enrollment_id,
                review_policy_signature=notification.review_policy_signature,
                telegram_chat_id=int(notification.notification_destination),
                review_token_hash=digest,
                status="PENDING",
                updated_at=now,
            )
        )
    elif row.status == "PENDING" and row.telegram_message_id is None:
        row.review_token_hash = digest
        row.updated_at = now
    else:
        raise ValueError("Review request is already bound or closed")
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔍 정책 상세 검토",
                    "callback_data": f"policy_review:{token}",
                }
            ]
        ]
    }


def finalize_review_button(
    session: Session,
    *,
    notification_id: int,
    message_id: int | None,
    retry_at: datetime | None,
    now: datetime,
    settings: Settings,
) -> None:
    row = _request_for_notification(session, notification_id)
    if row is None or row.status != "PENDING" or row.telegram_message_id is not None:
        raise ValueError("Review request is not awaiting a receipt")
    if message_id is not None:
        row.telegram_message_id = message_id
        row.review_expires_at = now + timedelta(
            seconds=settings.policy_telegram_review_token_ttl_seconds
        )
    elif retry_at is None:
        row.status = "CANCELLED"
    row.updated_at = now


def _request_for_notification(
    session: Session, notification_id: int
) -> TelegramPolicyReviewRequest | None:
    return session.scalar(
        select(TelegramPolicyReviewRequest)
        .where(TelegramPolicyReviewRequest.notification_id == notification_id)
        .with_for_update()
    )
