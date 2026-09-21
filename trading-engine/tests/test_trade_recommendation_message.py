from datetime import UTC, datetime
from decimal import Decimal

from crypto_trading_bot.db.models import AnalysisRun, TradeRecommendation
from crypto_trading_bot.notification.trade_recommendation_message import (
    build_trade_recommendation_summary_message,
)


COMMON_ORDER_NOTICE = (
    "※ BUY/SELL은 별도 승인 요청 대상이며, HOLD는 주문을 실행하지 않습니다."
)


def build_analysis_run() -> AnalysisRun:
    return AnalysisRun(
        id=1,
        user_id=1,
        run_type="AI_RECOMMENDATION",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


def build_recommendation(
    *,
    recommendation_id: int = 1,
    action: str = "BUY",
) -> TradeRecommendation:
    return TradeRecommendation(
        id=recommendation_id,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action=action,
        confidence=Decimal("0.7500"),
        reason="test reason",
        recommended_amount_krw=Decimal("5000") if action != "HOLD" else None,
        recommended_quantity=None,
        ai_model="TEST",
        ai_response={},
        status="CREATED",
    )


def test_trade_recommendation_summary_message_shows_buy_approval_notice() -> None:
    message = build_trade_recommendation_summary_message(
        analysis_run=build_analysis_run(),
        recommendations=[
            build_recommendation(
                recommendation_id=1,
                action="BUY",
            )
        ],
    )

    assert "판단: BUY" in message
    assert "승인요청 상태: 별도 승인 요청 메시지 발송 대상" in message
    assert COMMON_ORDER_NOTICE in message


def test_trade_recommendation_summary_message_shows_sell_approval_notice() -> None:
    message = build_trade_recommendation_summary_message(
        analysis_run=build_analysis_run(),
        recommendations=[build_recommendation(recommendation_id=1, action="SELL")],
    )

    assert "판단: SELL" in message
    assert "승인요청 상태: 별도 승인 요청 메시지 발송 대상" in message
    assert COMMON_ORDER_NOTICE in message


def test_trade_recommendation_summary_message_shows_hold_notice() -> None:
    message = build_trade_recommendation_summary_message(
        analysis_run=build_analysis_run(),
        recommendations=[
            build_recommendation(
                recommendation_id=1,
                action="HOLD",
            )
        ],
    )

    assert "판단: HOLD" in message
    assert "승인요청 상태: HOLD는 승인 요청 없음" in message
    assert COMMON_ORDER_NOTICE in message


def test_trade_recommendation_summary_message_shows_superseded_notice() -> None:
    message = build_trade_recommendation_summary_message(
        analysis_run=build_analysis_run(),
        recommendations=[
            build_recommendation(
                recommendation_id=1,
                action="HOLD",
            )
        ],
        superseded_request_counts_by_recommendation_id={
            1: 2,
        },
    )

    assert "판단: HOLD" in message
    assert "승인요청 상태: 기존 승인 요청 2건이 최신 분석으로 대체됨" in message
