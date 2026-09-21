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
        confidence=Decimal("0.8"),
        reason="사유",
        recommended_amount_krw=Decimal("10000"),
        recommended_quantity=Decimal("0.0001") if action == "SELL" else None,
        ai_response={"safe_advice": {"risk_notes": "리스크"}},
        status="CREATED",
    )


def test_buy_message_shows_ratio_and_amount_without_sell_fields() -> None:
    message = build_approval_request_message(recommendation("BUY", Decimal("0.5")), 30)
    assert "거래 비율: 50.00%" in message
    assert "계산된 KRW 금액: 10,000원" in message
    assert "계산된 코인 수량" not in message
    assert "리스크 메모:" in message


def test_sell_message_shows_ratio_quantity_and_estimated_value() -> None:
    message = build_approval_request_message(recommendation("SELL", Decimal("1")), 30)
    assert "거래 비율: 100.00%" in message
    assert "계산된 코인 수량: 0.0001000000" in message
    assert "예상 KRW 매도가치: 10,000원" in message


def test_historical_null_ratio_is_formatted_safely() -> None:
    message = build_approval_request_message(recommendation("BUY", None), 30)
    assert "거래 비율: 기록 없음" in message
