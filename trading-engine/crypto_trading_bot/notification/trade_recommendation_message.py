from decimal import Decimal

from crypto_trading_bot.db.models import AnalysisRun, TradeRecommendation


SUPPORTED_APPROVAL_ACTIONS = {"BUY", "SELL"}


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


def build_approval_request_summary_line(
    recommendation: TradeRecommendation,
    superseded_request_count: int = 0,
) -> str:
    action = recommendation.action.strip().upper()

    if superseded_request_count > 0:
        return (
            "승인요청 상태: "
            f"기존 승인 요청 {superseded_request_count}건이 최신 분석으로 대체됨"
        )

    if action in SUPPORTED_APPROVAL_ACTIONS:
        return "승인요청 상태: 별도 승인 요청 메시지 발송 대상"

    return "승인요청 상태: HOLD는 승인 요청 없음"


def build_trade_recommendation_summary_message(
    analysis_run: AnalysisRun,
    recommendations: list[TradeRecommendation],
    superseded_request_counts_by_recommendation_id: dict[int, int] | None = None,
) -> str:
    superseded_request_counts_by_recommendation_id = (
        superseded_request_counts_by_recommendation_id or {}
    )

    message_lines = [
        "[AI 매매 분석 결과]",
        "",
        f"실행 ID: {analysis_run.id}",
        f"실행 상태: {analysis_run.status}",
        f"거래 모드: {analysis_run.trading_mode}",
        "",
    ]

    for recommendation in recommendations:
        superseded_request_count = superseded_request_counts_by_recommendation_id.get(
            recommendation.id or 0,
            0,
        )

        message_lines.extend(
            [
                "------------------------------",
                f"마켓: {recommendation.market}",
                f"판단: {recommendation.action}",
                build_approval_request_summary_line(
                    recommendation=recommendation,
                    superseded_request_count=superseded_request_count,
                ),
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

    message_lines.append(
        "※ BUY/SELL은 별도 승인 요청 대상이며, HOLD는 주문을 실행하지 않습니다."
    )

    return "\n".join(message_lines)
