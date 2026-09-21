from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderExecutionAttempt,
    OrderLog,
    TradeRecommendation,
)


KST = ZoneInfo("Asia/Seoul")


def format_value(value: object) -> str:
    if value is None:
        return "-"

    return str(value)


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"

    return value.astimezone(KST).isoformat()


def format_bool(value: object) -> str:
    if value is None:
        return "-"

    return str(value)


def get_latest_recommendation(
    session: Session,
    recommendation_id: int | None,
) -> TradeRecommendation | None:
    if recommendation_id is not None:
        statement = select(TradeRecommendation).where(
            TradeRecommendation.id == recommendation_id,
        )
        return session.scalar(statement)

    statement = (
        select(TradeRecommendation)
        .order_by(TradeRecommendation.created_at.desc())
        .limit(1)
    )

    return session.scalar(statement)


def get_latest_approval_request(
    session: Session,
    recommendation_id: int,
) -> ApprovalRequest | None:
    statement = (
        select(ApprovalRequest)
        .where(ApprovalRequest.recommendation_id == recommendation_id)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )

    return session.scalar(statement)


def get_latest_order_execution_attempt(
    session: Session,
    recommendation_id: int,
) -> OrderExecutionAttempt | None:
    statement = (
        select(OrderExecutionAttempt)
        .where(OrderExecutionAttempt.recommendation_id == recommendation_id)
        .order_by(OrderExecutionAttempt.attempt_number.desc())
        .limit(1)
    )

    return session.scalar(statement)


def get_order_log(
    session: Session,
    recommendation_id: int,
) -> OrderLog | None:
    statement = (
        select(OrderLog)
        .where(OrderLog.recommendation_id == recommendation_id)
        .order_by(OrderLog.created_at.desc())
        .limit(1)
    )

    return session.scalar(statement)


def get_today_mock_order_amount(session: Session) -> Decimal:
    now = datetime.now(KST)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_start = day_start + timedelta(days=1)

    statement = select(func.coalesce(func.sum(OrderLog.amount_krw), 0)).where(
        OrderLog.trading_mode == "MOCK",
        OrderLog.created_at >= day_start,
        OrderLog.created_at < next_day_start,
    )

    amount = session.scalar(statement)

    if amount is None:
        return Decimal("0")

    return Decimal(str(amount))


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * 60)


def print_recommendation(recommendation: TradeRecommendation) -> None:
    print_section("Trade recommendation")
    print(f"id={recommendation.id}")
    print(f"analysis_run_id={recommendation.analysis_run_id}")
    print(f"market={recommendation.market}")
    print(f"exchange={recommendation.exchange}")
    print(f"action={recommendation.action}")
    print(f"status={recommendation.status}")
    print(f"confidence={format_value(recommendation.confidence)}")
    print(
        f"recommended_amount_krw={format_value(recommendation.recommended_amount_krw)}"
    )
    print(f"recommended_quantity={format_value(recommendation.recommended_quantity)}")
    print(f"ai_model={format_value(recommendation.ai_model)}")
    print(f"created_at={format_datetime(recommendation.created_at)}")
    print(f"updated_at={format_datetime(recommendation.updated_at)}")


def print_approval_request(approval_request: ApprovalRequest | None) -> None:
    print_section("Approval request")

    if approval_request is None:
        print("approval_request=-")
        return

    print(f"id={approval_request.id}")
    print(f"recommendation_id={approval_request.recommendation_id}")
    print(f"status={approval_request.status}")
    print(f"telegram_chat_id={format_value(approval_request.telegram_chat_id)}")
    print(f"telegram_message_id={format_value(approval_request.telegram_message_id)}")
    print(f"expires_at={format_datetime(approval_request.expires_at)}")
    print(f"approved_at={format_datetime(approval_request.approved_at)}")
    print(f"rejected_at={format_datetime(approval_request.rejected_at)}")
    print(f"created_at={format_datetime(approval_request.created_at)}")
    print(f"updated_at={format_datetime(approval_request.updated_at)}")


def print_order_execution_attempt(
    attempt: OrderExecutionAttempt | None,
) -> None:
    print_section("Order execution attempt")

    if attempt is None:
        print("order_execution_attempt=-")
        return

    print(f"id={attempt.id}")
    print(f"recommendation_id={attempt.recommendation_id}")
    print(f"approval_request_id={attempt.approval_request_id}")
    print(f"order_log_id={format_value(attempt.order_log_id)}")
    print(f"trading_mode={attempt.trading_mode}")
    print(f"attempt_number={attempt.attempt_number}")
    print(f"status={attempt.status}")
    print(f"error_code={format_value(attempt.error_code)}")
    print(f"error_message={format_value(attempt.error_message)}")
    print(f"attempted_at={format_datetime(attempt.attempted_at)}")
    print(f"next_retry_at={format_datetime(attempt.next_retry_at)}")


def print_order_log(order_log: OrderLog | None) -> None:
    print_section("Order log")

    if order_log is None:
        print("order_log=-")
        return

    actual_order_executed: object = None

    if isinstance(order_log.raw_response, dict):
        actual_order_executed = order_log.raw_response.get("actual_order_executed")

    print(f"id={order_log.id}")
    print(f"recommendation_id={order_log.recommendation_id}")
    print(f"approval_request_id={format_value(order_log.approval_request_id)}")
    print(f"trading_mode={order_log.trading_mode}")
    print(f"exchange={order_log.exchange}")
    print(f"market={order_log.market}")
    print(f"side={order_log.side}")
    print(f"order_type={order_log.order_type}")
    print(f"status={order_log.status}")
    print(f"amount_krw={format_value(order_log.amount_krw)}")
    print(f"quantity={format_value(order_log.quantity)}")
    print(f"price={format_value(order_log.price)}")
    print(f"exchange_order_id={format_value(order_log.exchange_order_id)}")
    print(f"actual_order_executed={format_bool(actual_order_executed)}")
    print(f"error_message={format_value(order_log.error_message)}")
    print(f"created_at={format_datetime(order_log.created_at)}")
    print(f"updated_at={format_datetime(order_log.updated_at)}")


def print_today_mock_order_amount(amount: Decimal) -> None:
    print_section("Daily mock order amount")
    print(f"today_mock_order_amount={amount}")


def show_latest_trade_flow_status(recommendation_id: int | None = None) -> None:
    with SessionLocal() as session:
        recommendation = get_latest_recommendation(
            session=session,
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            print("Trade recommendation was not found.")
            return

        approval_request = get_latest_approval_request(
            session=session,
            recommendation_id=recommendation.id,
        )

        attempt = get_latest_order_execution_attempt(
            session=session,
            recommendation_id=recommendation.id,
        )

        order_log = get_order_log(
            session=session,
            recommendation_id=recommendation.id,
        )

        today_mock_order_amount = get_today_mock_order_amount(session)

        print("Latest trade flow status")
        print("=" * 60)

        print_recommendation(recommendation)
        print_approval_request(approval_request)
        print_order_execution_attempt(attempt)
        print_order_log(order_log)
        print_today_mock_order_amount(today_mock_order_amount)


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description="Show latest trade recommendation, approval, attempt, and order status.",
    )

    parser.add_argument(
        "--recommendation-id",
        type=int,
        default=None,
        help="Trade recommendation ID to inspect. Defaults to the latest recommendation.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    show_latest_trade_flow_status(
        recommendation_id=args.recommendation_id,
    )
