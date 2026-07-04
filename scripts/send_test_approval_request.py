from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import AnalysisRun, TradeRecommendation, User
from crypto_trading_bot.notification.approval_request_message import (
    build_approval_request_message,
    build_approval_request_reply_markup,
)
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.approval_request_service import (
    DEFAULT_APPROVAL_EXPIRATION_MINUTES,
    ApprovalRequestService,
)


TEST_USER_NAME = "Minsu"
TEST_MARKET = "KRW-BTC"
TEST_CONFIDENCE = Decimal("0.7500")

MIN_TEST_BUY_AMOUNT_KRW = Decimal("5000")
MONEY_QUANTUM = Decimal("0.01")


def get_test_buy_amount_krw() -> Decimal:
    settings = get_settings()

    max_order_amount_krw = Decimal(str(settings.max_order_amount_krw))
    daily_max_order_amount_krw = Decimal(str(settings.daily_max_order_amount_krw))

    test_buy_amount_krw = min(
        max_order_amount_krw,
        daily_max_order_amount_krw,
    ).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_DOWN,
    )

    if test_buy_amount_krw < MIN_TEST_BUY_AMOUNT_KRW:
        raise ValueError(
            "Test buy amount is below minimum order amount. "
            f"test_buy_amount_krw={test_buy_amount_krw}, "
            f"minimum={MIN_TEST_BUY_AMOUNT_KRW}"
        )

    return test_buy_amount_krw


def get_test_user(
    session: Session,
    user_name: str = TEST_USER_NAME,
) -> User:
    statement = (
        select(User)
        .where(
            User.name == user_name,
            User.is_active.is_(True),
        )
        .order_by(User.id.asc())
        .limit(1)
    )

    user = session.scalar(statement)

    if user is None:
        raise ValueError(f"Active user not found. name={user_name}")

    return user


def create_test_buy_recommendation(
    session: Session,
    user: User,
    test_buy_amount_krw: Decimal,
) -> tuple[AnalysisRun, TradeRecommendation]:
    settings = get_settings()
    now = datetime.now(UTC)

    analysis_run = AnalysisRun(
        user_id=user.id,
        run_type="APPROVAL_TEST",
        trading_mode=settings.trading_mode,
        status="SUCCESS",
        finished_at=now,
        error_message=None,
    )

    session.add(analysis_run)
    session.flush()

    recommendation = TradeRecommendation(
        analysis_run_id=analysis_run.id,
        market_snapshot_id=None,
        user_id=user.id,
        exchange="UPBIT",
        market=TEST_MARKET,
        action="BUY",
        confidence=TEST_CONFIDENCE,
        reason=(
            "텔레그램 승인 요청 기능 검증을 위한 테스트용 매수 추천입니다. "
            "이 추천으로 실제 거래소 주문은 실행되지 않습니다."
        ),
        recommended_amount_krw=test_buy_amount_krw,
        recommended_quantity=None,
        ai_model="TEST",
        ai_response={
            "source": "approval_request_test",
            "actual_order_enabled": False,
            "test_buy_amount_krw": str(test_buy_amount_krw),
            "max_order_amount_krw": str(settings.max_order_amount_krw),
            "daily_max_order_amount_krw": str(
                settings.daily_max_order_amount_krw
            ),
        },
        status="CREATED",
    )

    session.add(recommendation)
    session.commit()

    session.refresh(analysis_run)
    session.refresh(recommendation)

    return analysis_run, recommendation


def send_test_approval_request() -> None:
    settings = get_settings()

    if not settings.telegram_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is not configured")

    test_buy_amount_krw = get_test_buy_amount_krw()
    telegram_client = TelegramClient()

    with SessionLocal() as session:
        user = get_test_user(session)

        analysis_run, recommendation = create_test_buy_recommendation(
            session=session,
            user=user,
            test_buy_amount_krw=test_buy_amount_krw,
        )

        approval_request_service = ApprovalRequestService(session)

        approval_request, created = (
            approval_request_service.get_or_create_pending_request(
                recommendation=recommendation,
                telegram_chat_id=settings.telegram_chat_id,
                expires_in_minutes=DEFAULT_APPROVAL_EXPIRATION_MINUTES,
            )
        )

        message = build_approval_request_message(
            recommendation=recommendation,
            expires_in_minutes=DEFAULT_APPROVAL_EXPIRATION_MINUTES,
        )

        reply_markup = build_approval_request_reply_markup(
            action=recommendation.action,
            callback_token=approval_request.callback_token,
        )

        telegram_result = telegram_client.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
            reply_markup=reply_markup,
        )

        telegram_message_id = telegram_result.get("message_id")

        if not isinstance(telegram_message_id, int):
            raise ValueError(
                "Telegram response does not contain a valid message_id. "
                f"result={telegram_result}"
            )

        approval_request_service.save_telegram_message_id(
            approval_request=approval_request,
            telegram_message_id=telegram_message_id,
        )

        print("Test approval request sent successfully.")
        print(f"analysis_run_id={analysis_run.id}")
        print(f"recommendation_id={recommendation.id}")
        print(f"approval_request_id={approval_request.id}")
        print(f"approval_request_created={created}")
        print(f"telegram_message_id={telegram_message_id}")
        print(f"test_buy_amount_krw={test_buy_amount_krw}")
        print(f"expires_at={approval_request.expires_at}")
        print("Actual exchange order was not executed.")


if __name__ == "__main__":
    send_test_approval_request()