from collections.abc import Generator
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    OrderExecutionAttempt,
    OrderLog,
    OrderRetryNotification,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.services.order_retry_notification_outbox_service import (
    OrderRetryNotificationOutboxService,
)


KST = ZoneInfo("Asia/Seoul")


class SuccessfulTelegramClient:
    def __init__(self) -> None:
        self.messages = []

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
    ) -> dict:
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

        return {
            "message_id": 1,
            "chat": {
                "id": chat_id,
            },
            "text": text,
        }


class FailingTelegramClient:
    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
    ) -> dict:
        raise RuntimeError("Temporary Telegram failure")


@pytest.fixture
def created_ids() -> Generator[dict[str, list[int]], None, None]:
    ids = {
        "notifications": [],
        "attempts": [],
        "order_logs": [],
        "approval_requests": [],
        "recommendations": [],
        "analysis_runs": [],
        "users": [],
    }

    yield ids

    with SessionLocal() as session:
        _delete_by_ids(
            session=session,
            model=OrderRetryNotification,
            ids=ids["notifications"],
        )
        _delete_by_ids(
            session=session,
            model=OrderExecutionAttempt,
            ids=ids["attempts"],
        )
        _delete_by_ids(
            session=session,
            model=OrderLog,
            ids=ids["order_logs"],
        )
        _delete_by_ids(
            session=session,
            model=ApprovalRequest,
            ids=ids["approval_requests"],
        )
        _delete_by_ids(
            session=session,
            model=TradeRecommendation,
            ids=ids["recommendations"],
        )
        _delete_by_ids(
            session=session,
            model=AnalysisRun,
            ids=ids["analysis_runs"],
        )
        _delete_by_ids(
            session=session,
            model=User,
            ids=ids["users"],
        )

        session.commit()


def test_process_due_sends_pending_notification(
    created_ids: dict[str, list[int]],
) -> None:
    notification_id, telegram_chat_id = _create_notification(
        created_ids=created_ids,
        delivery_status="PENDING",
        attempt_status="EXECUTED",
    )

    telegram_client = SuccessfulTelegramClient()

    summary = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=telegram_client,
    ).process_due(
        limit=100,
    )

    assert summary.processed_count == 1
    assert summary.sent_count == 1
    assert summary.failed_count == 0

    with SessionLocal() as session:
        notification = session.get(
            OrderRetryNotification,
            notification_id,
        )

        assert notification is not None
        assert notification.delivery_status == "SENT"
        assert notification.retry_count == 0
        assert notification.next_retry_at is None
        assert notification.error_message is None
        assert notification.sent_at is not None

    assert len(telegram_client.messages) == 1
    assert telegram_client.messages[0]["chat_id"] == telegram_chat_id
    assert "모의 주문 재시도 결과" in telegram_client.messages[0]["text"]
    assert "처리 결과: 재시도 성공" in telegram_client.messages[0]["text"]
    assert "OUTBOX-TEST" in telegram_client.messages[0]["text"]


def test_process_due_marks_pending_notification_failed_when_send_fails(
    created_ids: dict[str, list[int]],
) -> None:
    notification_id, _ = _create_notification(
        created_ids=created_ids,
        delivery_status="PENDING",
        attempt_status="EXECUTED",
    )

    summary = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=FailingTelegramClient(),
    ).process_due(
        limit=100,
    )

    assert summary.processed_count == 1
    assert summary.sent_count == 0
    assert summary.failed_count == 1

    with SessionLocal() as session:
        notification = session.get(
            OrderRetryNotification,
            notification_id,
        )

        assert notification is not None
        assert notification.delivery_status == "FAILED"
        assert notification.retry_count == 1
        assert notification.next_retry_at is not None
        assert notification.sent_at is None
        assert notification.error_message is not None
        assert "RuntimeError" in notification.error_message
        assert "Temporary Telegram failure" in notification.error_message


def test_process_due_skips_failed_notification_before_next_retry_time(
    created_ids: dict[str, list[int]],
) -> None:
    future_retry_at = datetime.now(KST) + timedelta(hours=1)

    notification_id, _ = _create_notification(
        created_ids=created_ids,
        delivery_status="FAILED",
        attempt_status="EXECUTED",
        retry_count=1,
        next_retry_at=future_retry_at,
    )

    telegram_client = SuccessfulTelegramClient()

    summary = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=telegram_client,
    ).process_due(
        limit=100,
    )

    assert summary.processed_count == 0
    assert summary.sent_count == 0
    assert summary.failed_count == 0
    assert telegram_client.messages == []

    with SessionLocal() as session:
        notification = session.get(
            OrderRetryNotification,
            notification_id,
        )

        assert notification is not None
        assert notification.delivery_status == "FAILED"
        assert notification.retry_count == 1
        assert notification.next_retry_at is not None
        assert notification.error_message == "previous delivery failure"
        assert notification.sent_at is None


def test_process_due_retries_due_failed_notification(
    created_ids: dict[str, list[int]],
) -> None:
    due_retry_at = datetime.now(KST) - timedelta(minutes=1)

    notification_id, telegram_chat_id = _create_notification(
        created_ids=created_ids,
        delivery_status="FAILED",
        attempt_status="EXECUTED",
        retry_count=1,
        next_retry_at=due_retry_at,
    )

    telegram_client = SuccessfulTelegramClient()

    summary = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=telegram_client,
    ).process_due(
        limit=100,
    )

    assert summary.processed_count == 1
    assert summary.sent_count == 1
    assert summary.failed_count == 0

    with SessionLocal() as session:
        notification = session.get(
            OrderRetryNotification,
            notification_id,
        )

        assert notification is not None
        assert notification.delivery_status == "SENT"
        assert notification.retry_count == 1
        assert notification.next_retry_at is None
        assert notification.error_message is None
        assert notification.sent_at is not None

    assert len(telegram_client.messages) == 1
    assert telegram_client.messages[0]["chat_id"] == telegram_chat_id


def test_process_due_recovers_stale_processing_notification(
    created_ids: dict[str, list[int]],
) -> None:
    notification_id, telegram_chat_id = _create_notification(
        created_ids=created_ids,
        delivery_status="PROCESSING",
        attempt_status="EXECUTED",
    )

    stale_time = datetime.now(KST) - timedelta(minutes=10)

    with SessionLocal() as session:
        session.execute(
            update(OrderRetryNotification)
            .where(
                OrderRetryNotification.id == notification_id,
            )
            .values(
                updated_at=stale_time,
            )
        )
        session.commit()

    telegram_client = SuccessfulTelegramClient()

    summary = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=telegram_client,
    ).process_due(
        limit=100,
    )

    assert summary.processed_count == 1
    assert summary.sent_count == 1
    assert summary.failed_count == 0

    with SessionLocal() as session:
        notification = session.get(
            OrderRetryNotification,
            notification_id,
        )

        assert notification is not None
        assert notification.delivery_status == "SENT"
        assert notification.retry_count == 0
        assert notification.next_retry_at is None
        assert notification.error_message is None
        assert notification.sent_at is not None

    assert len(telegram_client.messages) == 1
    assert telegram_client.messages[0]["chat_id"] == telegram_chat_id


def _create_notification(
    created_ids: dict[str, list[int]],
    delivery_status: str,
    attempt_status: str,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
) -> tuple[int, int]:
    _assert_outbox_table_is_empty()

    with SessionLocal() as session:
        now = datetime.now(KST)
        telegram_chat_id = -(uuid4().int % 9_000_000_000_000_000)

        user = User(
            name=f"outbox-test-user-{uuid4()}",
            telegram_chat_id=telegram_chat_id,
            is_active=True,
        )

        session.add(user)
        session.flush()
        created_ids["users"].append(user.id)

        analysis_run = AnalysisRun(
            user_id=user.id,
            run_type="TEST",
            trading_mode="MOCK",
            status="COMPLETED",
            finished_at=now,
            error_message=None,
        )

        session.add(analysis_run)
        session.flush()
        created_ids["analysis_runs"].append(analysis_run.id)

        recommendation = TradeRecommendation(
            analysis_run_id=analysis_run.id,
            market_snapshot_id=None,
            user_id=user.id,
            exchange="UPBIT",
            market="OUTBOX-TEST",
            action="BUY",
            confidence=0.9,
            reason="outbox service pytest",
            recommended_amount_krw=10_000,
            recommended_quantity=None,
            ai_model="pytest",
            ai_response={
                "test": True,
            },
            status="APPROVED",
        )

        session.add(recommendation)
        session.flush()
        created_ids["recommendations"].append(recommendation.id)

        approval_request = ApprovalRequest(
            recommendation_id=recommendation.id,
            user_id=user.id,
            status="APPROVED",
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=None,
            callback_token=f"outbox-pytest-{uuid4()}",
            expires_at=now + timedelta(days=1),
            approved_at=now,
            rejected_at=None,
        )

        session.add(approval_request)
        session.flush()
        created_ids["approval_requests"].append(approval_request.id)

        order_log_id = None

        if attempt_status == "EXECUTED":
            order_log = OrderLog(
                recommendation_id=recommendation.id,
                approval_request_id=approval_request.id,
                user_id=user.id,
                trading_mode="MOCK",
                exchange="UPBIT",
                market="OUTBOX-TEST",
                side="BUY",
                order_type="MARKET",
                amount_krw=10_000,
                quantity=0.0001,
                price=100_000_000,
                status="EXECUTED",
                exchange_order_id=None,
                error_message=None,
                raw_response={
                    "pytest": True,
                    "actual_order": False,
                },
            )

            session.add(order_log)
            session.flush()
            order_log_id = order_log.id
            created_ids["order_logs"].append(order_log.id)

        attempt = OrderExecutionAttempt(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            order_log_id=order_log_id,
            user_id=user.id,
            trading_mode="MOCK",
            attempt_number=2,
            status=attempt_status,
            error_code=None,
            error_message=None,
            attempted_at=now,
            next_retry_at=None,
        )

        session.add(attempt)
        session.flush()
        created_ids["attempts"].append(attempt.id)

        notification = OrderRetryNotification(
            attempt_id=attempt.id,
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            telegram_chat_id=telegram_chat_id,
            retry_status=attempt_status,
            delivery_status=delivery_status,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
            error_message=(
                "previous delivery failure" if delivery_status == "FAILED" else None
            ),
            sent_at=None,
        )

        session.add(notification)
        session.flush()

        notification_id = notification.id
        created_ids["notifications"].append(notification_id)

        session.commit()

        return notification_id, telegram_chat_id


def _assert_outbox_table_is_empty() -> None:
    with SessionLocal() as session:
        outbox_count = session.scalar(
            select(func.count()).select_from(
                OrderRetryNotification,
            )
        )

    assert int(outbox_count or 0) == 0, (
        "order_retry_notifications table must be empty "
        "before running this integration test."
    )


def _delete_by_ids(
    session: Session,
    model: type,
    ids: list[int],
) -> None:
    if not ids:
        return

    session.execute(
        delete(model).where(
            model.id.in_(ids),
        )
    )
