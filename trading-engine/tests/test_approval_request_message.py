from decimal import Decimal

from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.notification.approval_request_message import (
    build_approval_request_message,
)


def recommendation(action: str, ratio: Decimal | None) -> TradeRecommendation:
    return TradeRecommendation(
        id=1,
        analysis_run_id=1,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action=action,
        trade_ratio=ratio,
        confidence=Decimal("0.8333"),
        reason="사유",
        recommended_amount_krw=Decimal("10000"),
        recommended_quantity=Decimal("0.0001") if action == "SELL" else None,
        ai_model="gpt-5.6-sol",
        ai_response={"safe_advice": {"risk_notes": "리스크"}},
        status="CREATED",
    )


def test_buy_message_combines_recommendation_and_approval_information() -> None:
    message = build_approval_request_message(recommendation("BUY", Decimal("0.2")), 30)

    assert message.startswith("🟢 AI 매수 추천")
    assert "마켓: KRW-BTC" in message
    assert "신뢰도: 83.33%" in message
    assert "매수 비율: 20%" in message
    assert "매수 금액: 10,000원" in message
    assert "사유:\n사유" in message
    assert "리스크:\n리스크" in message
    assert "승인 유효시간: 30분" in message
    assert "AI 모델: gpt-5.6-sol" in message
    assert "추천 ID" not in message
    assert "판단: BUY" not in message
    assert "계산된 KRW 금액" not in message
    assert "아래 버튼을 눌러" not in message
    assert "매도 수량" not in message


def test_sell_message_combines_recommendation_and_approval_information() -> None:
    message = build_approval_request_message(
        recommendation("SELL", Decimal("0.125")),
        30,
    )

    assert message.startswith("🔴 AI 매도 추천")
    assert "마켓: KRW-BTC" in message
    assert "신뢰도: 83.33%" in message
    assert "매도 비율: 12.5%" in message
    assert "매도 수량: 0.0001000000" in message
    assert "예상 매도금액: 10,000원" in message
    assert "승인 유효시간: 30분" in message
    assert "AI 모델: gpt-5.6-sol" in message
    assert "계산된 코인 수량" not in message
    assert "예상 KRW 매도가치" not in message


def test_historical_null_ratio_is_formatted_safely() -> None:
    message = build_approval_request_message(recommendation("BUY", None), 30)

    assert "매수 비율: -" in message


def test_approval_message_shows_superseded_notice_only_when_needed() -> None:
    recommendation_value = recommendation("BUY", Decimal("0.2"))

    without_notice = build_approval_request_message(recommendation_value, 30)
    with_notice = build_approval_request_message(
        recommendation_value,
        30,
        superseded_request_count=1,
    )

    assert "이전 승인 요청" not in without_notice
    assert "※ 이전 승인 요청 1건은 최신 분석으로 대체되었습니다." in with_notice
