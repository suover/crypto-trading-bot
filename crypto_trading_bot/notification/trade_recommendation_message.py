from decimal import Decimal

from crypto_trading_bot.db.models import AnalysisRun, TradeRecommendation


def format_decimal(value: Decimal | None, digit_count: int = 4) -> str:
    if value is None:
        return "-"

    return f"{value:,.{digit_count}f}"


def format_krw(value: Decimal | None) -> str:
    if value is None:
        return "-"

    return f"{value:,.0f}원"


def truncate_text(text: str | None, max_length: int = 500) -> str:
    if not text:
        return "-"

    if len(text) <= max_length:
        return text

    return f"{text[:max_length]}..."


def build_trade_recommendation_summary_message(
    analysis_run: AnalysisRun,
    recommendations: list[TradeRecommendation],
) -> str:
    message_lines = [
        "[AI 매매 분석 결과]",
        "",
        f"실행 ID: {analysis_run.id}",
        f"실행 상태: {analysis_run.status}",
        f"거래 모드: {analysis_run.trading_mode}",
        "",
    ]

    for recommendation in recommendations:
        message_lines.extend(
            [
                "------------------------------",
                f"마켓: {recommendation.market}",
                f"판단: {recommendation.action}",
                f"신뢰도: {format_decimal(recommendation.confidence)}",
                f"추천금액: {format_krw(recommendation.recommended_amount_krw)}",
                (
                    "추천수량: "
                    f"{format_decimal(recommendation.recommended_quantity, 10)}"
                ),
                f"AI 모델: {recommendation.ai_model or '-'}",
                "",
                "사유:",
                truncate_text(recommendation.reason),
                "",
            ]
        )

    message_lines.append("※ HOLD는 주문 요청이 아니라 분석 결과 알림입니다.")

    return "\n".join(message_lines)