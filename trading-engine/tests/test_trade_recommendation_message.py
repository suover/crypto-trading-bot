from decimal import Decimal

import pytest

from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.notification.trade_recommendation_message import (
    build_hold_recommendation_message,
    format_percentage,
)


def build_recommendation(
    *,
    action: str = "HOLD",
    confidence: Decimal | None = Decimal("0.82"),
) -> TradeRecommendation:
    return TradeRecommendation(
        id=1,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action=action,
        confidence=confidence,
        reason="현재 시장 상황에서는 관망이 적절합니다.",
        recommended_amount_krw=None,
        recommended_quantity=None,
        ai_model="gpt-5.6-sol",
        ai_response={},
        status="CREATED",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.82"), "82%"),
        (Decimal("0.825"), "82.5%"),
        (Decimal("0.8333"), "83.33%"),
        (Decimal("1"), "100%"),
        (None, "-"),
        (Decimal("Infinity"), "-"),
    ],
)
def test_format_percentage_is_user_friendly(
    value: Decimal | None,
    expected: str,
) -> None:
    assert format_percentage(value) == expected


def test_hold_recommendation_message_shows_only_relevant_information() -> None:
    message = build_hold_recommendation_message(build_recommendation())

    assert message.startswith("🤖 AI 매매 분석")
    assert "마켓: KRW-BTC" in message
    assert "판단: HOLD" in message
    assert "신뢰도: 82%" in message
    assert "사유:\n현재 시장 상황에서는 관망이 적절합니다." in message
    assert "현재 주문은 실행하지 않습니다." in message
    assert "AI 모델: gpt-5.6-sol" in message
    assert "실행 ID" not in message
    assert "실행 상태" not in message
    assert "거래 모드" not in message
    assert "승인요청 상태" not in message
    assert "추천금액" not in message
    assert "추천수량" not in message
    assert "이전 승인 요청" not in message


def test_hold_recommendation_message_shows_superseded_notice() -> None:
    message = build_hold_recommendation_message(
        build_recommendation(),
        superseded_request_count=2,
    )

    assert "※ 이전 승인 요청 2건은 최신 분석으로 대체되었습니다." in message


def test_hold_recommendation_message_rejects_non_hold_action() -> None:
    with pytest.raises(ValueError, match="requires HOLD action"):
        build_hold_recommendation_message(build_recommendation(action="BUY"))
