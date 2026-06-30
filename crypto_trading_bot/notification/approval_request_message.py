from typing import Any

from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.notification.trade_recommendation_message import (
    format_decimal,
    format_krw,
    truncate_text,
)

SUPPORTED_APPROVAL_ACTIONS = {"BUY", "SELL"}


def build_approval_request_message(
    recommendation: TradeRecommendation,
    expires_in_minutes: int,
) -> str:
    action = _normalize_action(recommendation.action)

    if expires_in_minutes <= 0:
        raise ValueError("expires_in_minutes must be greater than 0")

    action_label = "매수" if action == "BUY" else "매도"

    message_lines = [
        f"[AI {action_label} 승인 요청]",
        "",
        f"추천 ID: {recommendation.id}",
        f"마켓: {recommendation.market}",
        f"판단: {action}",
        f"신뢰도: {format_decimal(recommendation.confidence)}",
    ]

    if action == "BUY":
        message_lines.append(
            f"추천금액: {format_krw(recommendation.recommended_amount_krw)}"
        )
    else:
        message_lines.append(
            f"추천수량: {format_decimal(recommendation.recommended_quantity, 10)}"
        )

    message_lines.extend(
        [
            f"승인 유효시간: {expires_in_minutes}분",
            "",
            "사유:",
            truncate_text(recommendation.reason),
            "",
            "아래 버튼을 눌러 승인 또는 거절해 주세요.",
        ]
    )

    return "\n".join(message_lines)


def build_approval_request_reply_markup(
    action: str,
    callback_token: str,
) -> dict[str, Any]:
    normalized_action = _normalize_action(action)

    if not callback_token.strip():
        raise ValueError("callback_token must not be empty")

    approval_button_text = "매수 승인" if normalized_action == "BUY" else "매도 승인"

    return {
        "inline_keyboard": [
            [
                {
                    "text": approval_button_text,
                    "callback_data": f"approve:{callback_token}",
                },
                {
                    "text": "거절",
                    "callback_data": f"reject:{callback_token}",
                },
            ]
        ]
    }


def _normalize_action(action: str) -> str:
    normalized_action = action.strip().upper()

    if normalized_action not in SUPPORTED_APPROVAL_ACTIONS:
        raise ValueError(
            f"Approval request is only available for BUY or SELL. action={action}"
        )

    return normalized_action
