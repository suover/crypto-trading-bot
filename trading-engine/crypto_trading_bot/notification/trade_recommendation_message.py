from decimal import Decimal

from crypto_trading_bot.db.models import TradeRecommendation


def format_decimal(value: Decimal | None, digit_count: int = 4) -> str:
    if value is None:
        return "-"

    return f"{value:,.{digit_count}f}"


def format_krw(value: Decimal | None) -> str:
    if value is None:
        return "-"

    return f"{value:,.0f}원"


def format_percentage(value: Decimal | None) -> str:
    if value is None or not value.is_finite():
        return "-"

    percentage = (value * Decimal("100")).quantize(Decimal("0.01"))
    formatted_percentage = format(percentage, "f").rstrip("0").rstrip(".")

    return f"{formatted_percentage}%"


def truncate_text(text: str | None, max_length: int = 500) -> str:
    if not text:
        return "-"

    if len(text) <= max_length:
        return text

    return f"{text[:max_length]}..."


def build_superseded_request_notice(
    superseded_request_count: int = 0,
) -> str | None:
    if superseded_request_count > 0:
        return (
            f"※ 이전 승인 요청 {superseded_request_count}건은 "
            "최신 분석으로 대체되었습니다."
        )

    return None


def build_hold_recommendation_message(
    recommendation: TradeRecommendation,
    superseded_request_count: int = 0,
) -> str:
    action = recommendation.action.strip().upper()

    if action != "HOLD":
        raise ValueError(
            f"HOLD recommendation message requires HOLD action. action={action}"
        )

    message_lines = [
        "🤖 AI 매매 분석",
        "",
        f"마켓: {recommendation.market}",
        "판단: HOLD",
        f"신뢰도: {format_percentage(recommendation.confidence)}",
        "",
        "사유:",
        truncate_text(recommendation.reason),
        "",
        "현재 주문은 실행하지 않습니다.",
        "",
        f"AI 모델: {recommendation.ai_model or '-'}",
    ]

    superseded_notice = build_superseded_request_notice(
        superseded_request_count=superseded_request_count,
    )
    if superseded_notice is not None:
        message_lines.extend(["", superseded_notice])

    return "\n".join(message_lines)
