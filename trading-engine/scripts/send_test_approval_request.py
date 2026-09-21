from argparse import ArgumentParser, ArgumentTypeError, Namespace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.notification.approval_request_message import (
    build_approval_request_message,
    build_approval_request_reply_markup,
)
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.approval_request_service import (
    DEFAULT_APPROVAL_EXPIRATION_MINUTES,
    ApprovalRequestService,
)
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


TEST_MARKET = "KRW-BTC"
TEST_CONFIDENCE = Decimal("0.7500")

SUPPORTED_TEST_ACTIONS = {"BUY", "SELL"}

MIN_TEST_BUY_AMOUNT_KRW = Decimal("5000")
DEFAULT_TEST_SELL_QUANTITY = Decimal("0.0001")

MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.0000000001")


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


def normalize_action(action: str) -> str:
    normalized_action = action.strip().upper()

    if normalized_action not in SUPPORTED_TEST_ACTIONS:
        raise ValueError(f"Test approval action must be BUY or SELL. action={action}")

    return normalized_action


def normalize_sell_quantity(sell_quantity: Decimal) -> Decimal:
    normalized_quantity = sell_quantity.quantize(
        QUANTITY_QUANTUM,
        rounding=ROUND_DOWN,
    )

    if normalized_quantity <= 0:
        raise ValueError(
            f"sell_quantity must be greater than 0. sell_quantity={sell_quantity}"
        )

    return normalized_quantity


def normalize_expiration_minutes(
    expires_in_minutes: int,
) -> tuple[int, int, bool]:
    if expires_in_minutes < 0:
        raise ValueError("expires_in_minutes must be greater than or equal to 0")

    if expires_in_minutes == 0:
        return 1, 0, True

    return expires_in_minutes, expires_in_minutes, False


def get_test_user(session: Session, user_id: int | None) -> User:
    return RuntimeUserResolver(session).resolve_configured(user_id)


def build_test_reason(action: str) -> str:
    action_label = "매수" if action == "BUY" else "매도"

    return (
        f"텔레그램 승인 요청 기능 검증을 위한 테스트용 {action_label} 추천입니다. "
        "이 추천으로 실제 거래소 주문은 실행되지 않습니다."
    )


def create_test_trade_recommendation(
    session: Session,
    user: User,
    action: str,
    test_buy_amount_krw: Decimal,
    test_sell_quantity: Decimal,
    expires_in_minutes: int,
) -> tuple[AnalysisRun, TradeRecommendation]:
    settings = get_settings()
    now = datetime.now(UTC)

    recommended_amount_krw = test_buy_amount_krw if action == "BUY" else None
    recommended_quantity = test_sell_quantity if action == "SELL" else None

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
        action=action,
        confidence=TEST_CONFIDENCE,
        reason=build_test_reason(action),
        recommended_amount_krw=recommended_amount_krw,
        recommended_quantity=recommended_quantity,
        ai_model="TEST",
        ai_response={
            "source": "approval_request_test",
            "actual_order_enabled": False,
            "test_action": action,
            "test_buy_amount_krw": str(test_buy_amount_krw),
            "test_sell_quantity": str(test_sell_quantity),
            "max_order_amount_krw": str(settings.max_order_amount_krw),
            "daily_max_order_amount_krw": str(settings.daily_max_order_amount_krw),
            "expires_in_minutes": expires_in_minutes,
        },
        status="CREATED",
    )

    session.add(recommendation)
    session.commit()

    session.refresh(analysis_run)
    session.refresh(recommendation)

    return analysis_run, recommendation


def expire_approval_request_immediately(
    session: Session,
    approval_request: ApprovalRequest,
) -> ApprovalRequest:
    approval_request.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    session.commit()
    session.refresh(approval_request)

    return approval_request


def send_test_approval_request(
    action: str,
    expires_in_minutes: int,
    sell_quantity: Decimal,
) -> None:
    settings = get_settings()

    if not settings.telegram_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is not configured")

    normalized_action = normalize_action(action)
    test_sell_quantity = normalize_sell_quantity(sell_quantity)

    (
        request_expires_in_minutes,
        display_expires_in_minutes,
        expire_immediately,
    ) = normalize_expiration_minutes(expires_in_minutes)

    test_buy_amount_krw = get_test_buy_amount_krw()
    telegram_client = TelegramClient()

    with SessionLocal() as session:
        user = get_test_user(session, settings.trading_user_id)

        analysis_run, recommendation = create_test_trade_recommendation(
            session=session,
            user=user,
            action=normalized_action,
            test_buy_amount_krw=test_buy_amount_krw,
            test_sell_quantity=test_sell_quantity,
            expires_in_minutes=display_expires_in_minutes,
        )

        approval_request_service = ApprovalRequestService(session)

        approval_request, created = (
            approval_request_service.get_or_create_pending_request(
                recommendation=recommendation,
                telegram_chat_id=settings.telegram_chat_id,
                expires_in_minutes=request_expires_in_minutes,
            )
        )

        message = build_approval_request_message(
            recommendation=recommendation,
            expires_in_minutes=request_expires_in_minutes,
        )

        if expire_immediately:
            message += "\n\n[테스트] 이 승인 요청은 발송 직후 만료 처리됩니다."

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

        approval_request = approval_request_service.save_telegram_message_id(
            approval_request=approval_request,
            telegram_message_id=telegram_message_id,
        )

        if expire_immediately:
            approval_request = expire_approval_request_immediately(
                session=session,
                approval_request=approval_request,
            )

        print("Test approval request sent successfully.")
        print(f"analysis_run_id={analysis_run.id}")
        print(f"recommendation_id={recommendation.id}")
        print(f"approval_request_id={approval_request.id}")
        print(f"approval_request_created={created}")
        print(f"telegram_message_id={telegram_message_id}")
        print(f"action={normalized_action}")
        print(f"test_buy_amount_krw={test_buy_amount_krw}")
        print(f"test_sell_quantity={test_sell_quantity}")
        print(f"expires_in_minutes={display_expires_in_minutes}")
        print(f"request_expires_in_minutes={request_expires_in_minutes}")
        print(f"expire_immediately={expire_immediately}")
        print(f"expires_at={approval_request.expires_at}")
        print("Actual exchange order was not executed.")


def parse_positive_decimal(value: str) -> Decimal:
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise ArgumentTypeError(f"Invalid decimal value. value={value}") from error

    if decimal_value <= 0:
        raise ArgumentTypeError(f"Value must be greater than 0. value={value}")

    return decimal_value


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description="Send a test Telegram approval request.",
    )

    parser.add_argument(
        "--action",
        choices=sorted(SUPPORTED_TEST_ACTIONS),
        default="BUY",
        help="Test approval action. Defaults to BUY.",
    )

    parser.add_argument(
        "--sell-quantity",
        type=parse_positive_decimal,
        default=DEFAULT_TEST_SELL_QUANTITY,
        help=(f"Test SELL quantity. Defaults to {DEFAULT_TEST_SELL_QUANTITY}."),
    )

    parser.add_argument(
        "--expires-in-minutes",
        type=int,
        default=DEFAULT_APPROVAL_EXPIRATION_MINUTES,
        help=(
            "Approval request expiration minutes. "
            "Use 0 to create an immediately expired test request. "
            f"Defaults to {DEFAULT_APPROVAL_EXPIRATION_MINUTES}."
        ),
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    send_test_approval_request(
        action=args.action,
        expires_in_minutes=args.expires_in_minutes,
        sell_quantity=args.sell_quantity,
    )
