from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    TradeRecommendation,
)
from crypto_trading_bot.services import trade_recommendation_notification_service
from crypto_trading_bot.services.trade_recommendation_notification_service import (
    TradeRecommendationNotificationService,
)


def clear_settings_cache() -> None:
    get_settings.cache_clear()


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.next_message_id = 1000

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

        self.next_message_id += 1

        return {
            "message_id": self.next_message_id,
        }


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


class FakeApprovalRequestService:
    supersede_call_count = 0
    superseded_request_count = 1
    get_or_create_call_count = 0
    save_telegram_message_id_call_count = 0
    expire_pending_requests_call_count = 0
    existing_telegram_message_id: int | None = None

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def expire_pending_requests(self) -> int:
        type(self).expire_pending_requests_call_count += 1
        return 0

    def supersede_active_pending_requests_for_market(
        self,
        recommendation: TradeRecommendation,
    ) -> int:
        type(self).supersede_call_count += 1
        return type(self).superseded_request_count

    def get_or_create_pending_request(
        self,
        recommendation: TradeRecommendation,
        telegram_chat_id: str | int,
    ) -> tuple[ApprovalRequest, bool]:
        type(self).get_or_create_call_count += 1

        approval_request = ApprovalRequest(
            id=20,
            recommendation_id=recommendation.id,
            user_id=recommendation.user_id,
            status="PENDING",
            telegram_chat_id=int(telegram_chat_id),
            telegram_message_id=type(self).existing_telegram_message_id,
            callback_token="new-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

        return approval_request, True

    def save_telegram_message_id(
        self,
        approval_request: ApprovalRequest,
        telegram_message_id: int,
    ) -> ApprovalRequest:
        type(self).save_telegram_message_id_call_count += 1
        approval_request.telegram_message_id = telegram_message_id
        return approval_request


def reset_fake_approval_request_service() -> None:
    FakeApprovalRequestService.supersede_call_count = 0
    FakeApprovalRequestService.superseded_request_count = 1
    FakeApprovalRequestService.get_or_create_call_count = 0
    FakeApprovalRequestService.save_telegram_message_id_call_count = 0
    FakeApprovalRequestService.expire_pending_requests_call_count = 0
    FakeApprovalRequestService.existing_telegram_message_id = None


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
    market: str = "KRW-BTC",
    action: str = "BUY",
) -> TradeRecommendation:
    return TradeRecommendation(
        id=recommendation_id,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange="UPBIT",
        market=market,
        action=action,
        trade_ratio=Decimal("0.2"),
        confidence=Decimal("0.7500"),
        reason="test recommendation",
        recommended_amount_krw=(None if action == "HOLD" else Decimal("5000")),
        recommended_quantity=(Decimal("0.001234") if action == "SELL" else None),
        ai_model="gpt-5.6-sol",
        ai_response={"safe_advice": {"risk_notes": "test risk"}},
        status="CREATED",
    )


def build_service_with_recommendations(
    monkeypatch: pytest.MonkeyPatch,
    recommendations: list[TradeRecommendation],
) -> tuple[TradeRecommendationNotificationService, FakeTelegramClient]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    clear_settings_cache()

    reset_fake_approval_request_service()

    monkeypatch.setattr(
        trade_recommendation_notification_service,
        "ApprovalRequestService",
        FakeApprovalRequestService,
    )

    telegram_client = FakeTelegramClient()
    service = TradeRecommendationNotificationService(
        session=FakeSession(),  # type: ignore[arg-type]
        telegram_client=telegram_client,  # type: ignore[arg-type]
    )

    monkeypatch.setattr(
        service,
        "_get_latest_ai_analysis_run",
        build_analysis_run,
    )
    monkeypatch.setattr(
        service,
        "_get_recommendations",
        lambda analysis_run_id: recommendations,
    )

    return service, telegram_client


def test_send_latest_ai_recommendation_supersedes_old_pending_and_sends_new_buy_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, telegram_client = build_service_with_recommendations(
        monkeypatch=monkeypatch,
        recommendations=[
            build_recommendation(
                recommendation_id=1,
                market="KRW-BTC",
                action="BUY",
            )
        ],
    )

    analysis_run, recommendation_count = service.send_latest_ai_recommendation_summary()

    assert analysis_run.id == 1
    assert recommendation_count == 1

    # 기존 PENDING 요청을 최신 추천 기준으로 대체 처리한다.
    assert FakeApprovalRequestService.supersede_call_count == 1

    # BUY는 최신 추천 기준으로 새 승인 요청을 보낸다.
    assert FakeApprovalRequestService.get_or_create_call_count == 1
    assert FakeApprovalRequestService.save_telegram_message_id_call_count == 1

    # BUY 추천과 승인 요청을 하나의 메시지로 전송한다.
    assert len(telegram_client.sent_messages) == 1
    sent_message = telegram_client.sent_messages[0]
    assert sent_message["text"].startswith("🟢 AI 매수 추천")
    assert "마켓: KRW-BTC" in sent_message["text"]
    assert "신뢰도: 75%" in sent_message["text"]
    assert "매수 비율: 20%" in sent_message["text"]
    assert "매수 금액: 5,000원" in sent_message["text"]
    assert "사유:\ntest recommendation" in sent_message["text"]
    assert "리스크:\ntest risk" in sent_message["text"]
    assert "승인 유효시간:" in sent_message["text"]
    assert "AI 모델: gpt-5.6-sol" in sent_message["text"]
    assert "[AI 매매 분석 결과]" not in sent_message["text"]
    assert sent_message["reply_markup"] == {
        "inline_keyboard": [
            [
                {"text": "매수 승인", "callback_data": "approve:new-token"},
                {"text": "거절", "callback_data": "reject:new-token"},
            ]
        ]
    }


def test_send_latest_ai_recommendation_supersedes_old_pending_and_does_not_send_hold_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, telegram_client = build_service_with_recommendations(
        monkeypatch=monkeypatch,
        recommendations=[
            build_recommendation(
                recommendation_id=1,
                market="KRW-BTC",
                action="HOLD",
            )
        ],
    )

    analysis_run, recommendation_count = service.send_latest_ai_recommendation_summary()

    assert analysis_run.id == 1
    assert recommendation_count == 1

    # HOLD도 기존 PENDING 승인 요청은 무효화해야 한다.
    assert FakeApprovalRequestService.supersede_call_count == 1

    # HOLD는 승인 요청 대상이 아니므로 새 승인 요청을 만들지 않는다.
    assert FakeApprovalRequestService.get_or_create_call_count == 0
    assert FakeApprovalRequestService.save_telegram_message_id_call_count == 0

    # HOLD 핵심 메시지만 전송되고 inline keyboard는 없다.
    assert len(telegram_client.sent_messages) == 1
    sent_message = telegram_client.sent_messages[0]
    assert sent_message["text"].startswith("🤖 AI 매매 분석")
    assert "판단: HOLD" in sent_message["text"]
    assert "신뢰도: 75%" in sent_message["text"]
    assert "AI 모델: gpt-5.6-sol" in sent_message["text"]
    assert "추천금액" not in sent_message["text"]
    assert "추천수량" not in sent_message["text"]
    assert (
        "※ 이전 승인 요청 1건은 최신 분석으로 대체되었습니다." in sent_message["text"]
    )
    assert sent_message["reply_markup"] is None


def test_send_latest_sell_recommendation_sends_one_approval_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, telegram_client = build_service_with_recommendations(
        monkeypatch=monkeypatch,
        recommendations=[build_recommendation(action="SELL")],
    )

    service.send_latest_ai_recommendation_summary()

    assert len(telegram_client.sent_messages) == 1
    sent_message = telegram_client.sent_messages[0]
    assert sent_message["text"].startswith("🔴 AI 매도 추천")
    assert "매도 비율: 20%" in sent_message["text"]
    assert "매도 수량: 0.0012340000" in sent_message["text"]
    assert "예상 매도금액: 5,000원" in sent_message["text"]
    assert "AI 모델: gpt-5.6-sol" in sent_message["text"]
    assert sent_message["reply_markup"]["inline_keyboard"][0][0]["text"] == (
        "매도 승인"
    )


def test_existing_pending_approval_message_is_not_sent_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, telegram_client = build_service_with_recommendations(
        monkeypatch=monkeypatch,
        recommendations=[build_recommendation(action="BUY")],
    )
    FakeApprovalRequestService.existing_telegram_message_id = 777

    service.send_latest_ai_recommendation_summary()

    assert FakeApprovalRequestService.get_or_create_call_count == 1
    assert FakeApprovalRequestService.save_telegram_message_id_call_count == 0
    assert telegram_client.sent_messages == []


def test_latest_ai_run_is_scoped_to_current_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_run_id = str(uuid4())
    monkeypatch.setenv("CRYPTO_TRADING_PIPELINE_RUN_ID", pipeline_run_id)
    session = MagicMock()
    expected = build_analysis_run()
    session.scalar.return_value = expected
    service = TradeRecommendationNotificationService(
        session=session,
        telegram_client=FakeTelegramClient(),  # type: ignore[arg-type]
    )

    assert service._get_latest_ai_analysis_run() is expected

    statement = session.scalar.call_args.args[0]
    assert pipeline_run_id in statement.compile().params.values()


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
    reset_fake_approval_request_service()
