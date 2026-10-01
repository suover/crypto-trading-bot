from typing import Any

from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.notification.trade_recommendation_message import (
    build_superseded_request_notice,
    format_decimal,
    format_krw,
    format_percentage,
    truncate_text,
)

SUPPORTED_APPROVAL_ACTIONS = {"BUY", "SELL"}


def build_approval_request_message(
    recommendation: TradeRecommendation,
    expires_in_minutes: int,
    superseded_request_count: int = 0,
) -> str:
    action = _normalize_action(recommendation.action)

    if expires_in_minutes <= 0:
        raise ValueError("expires_in_minutes must be greater than 0")

    action_label = "매수" if action == "BUY" else "매도"
    action_icon = "🟢" if action == "BUY" else "🔴"

    message_lines = [
        f"{action_icon} AI {action_label} 추천",
        "",
        f"마켓: {recommendation.market}",
        f"신뢰도: {format_percentage(recommendation.confidence)}",
        f"{action_label} 비율: {format_percentage(recommendation.trade_ratio)}",
    ]

    if action == "BUY":
        message_lines.append(
            f"매수 금액: {format_krw(recommendation.recommended_amount_krw)}"
        )
    else:
        message_lines.append(
            f"매도 수량: {format_decimal(recommendation.recommended_quantity, 10)}"
        )
        message_lines.append(
            f"예상 매도금액: {format_krw(recommendation.recommended_amount_krw)}"
        )

    message_lines.extend(
        [
            "",
            "사유:",
            truncate_text(recommendation.reason),
            "",
            "리스크:",
            truncate_text(_get_risk_notes(recommendation)),
            "",
            f"승인 유효시간: {expires_in_minutes}분",
            f"AI 모델: {recommendation.ai_model or '-'}",
        ]
    )

    superseded_notice = build_superseded_request_notice(
        superseded_request_count=superseded_request_count,
    )
    if superseded_notice is not None:
        message_lines.extend(["", superseded_notice])

    return "\n".join(message_lines)


def _get_risk_notes(recommendation: TradeRecommendation) -> str:
    ai_response = recommendation.ai_response or {}
    safe_advice = ai_response.get("safe_advice") or {}
    return str(safe_advice.get("risk_notes") or "기록 없음")


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
