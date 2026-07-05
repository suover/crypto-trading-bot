from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

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
    get_or_create_call_count = 0
    save_telegram_message_id_call_count = 0
    expire_pending_requests_call_count = 0

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
        return 1

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
            telegram_message_id=None,
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
    FakeApprovalRequestService.get_or_create_call_count = 0
    FakeApprovalRequestService.save_telegram_message_id_call_count = 0
    FakeApprovalRequestService.expire_pending_requests_call_count = 0


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
        confidence=Decimal("0.7500"),
        reason="test recommendation",
        recommended_amount_krw=Decimal("5000"),
        recommended_quantity=None,
        ai_model="TEST",
        ai_response={},
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

    # 요약 메시지 + 승인 요청 메시지
    assert len(telegram_client.sent_messages) == 2


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

    # 요약 메시지만 전송된다.
    assert len(telegram_client.sent_messages) == 1


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
    reset_fake_approval_request_service()
