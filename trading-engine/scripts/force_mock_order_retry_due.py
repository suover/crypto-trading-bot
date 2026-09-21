from argparse import ArgumentParser, Namespace
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    OrderExecutionAttempt,
    OrderLog,
    TradeRecommendation,
)


KST = ZoneInfo("Asia/Seoul")


def get_latest_mock_attempt(
    session: Session,
    recommendation_id: int,
) -> OrderExecutionAttempt | None:
    statement = (
        select(OrderExecutionAttempt)
        .where(
            OrderExecutionAttempt.recommendation_id == recommendation_id,
            OrderExecutionAttempt.trading_mode == "MOCK",
        )
        .order_by(OrderExecutionAttempt.attempt_number.desc())
        .limit(1)
    )

    return session.scalar(statement)


def get_recommendation(
    session: Session,
    recommendation_id: int,
) -> TradeRecommendation | None:
    return session.get(TradeRecommendation, recommendation_id)


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


def force_mock_order_retry_due(
    recommendation_id: int,
    seconds_ago: int,
) -> None:
    if seconds_ago <= 0:
        raise ValueError(
            f"seconds_ago must be greater than 0. seconds_ago={seconds_ago}"
        )

    with SessionLocal() as session:
        recommendation = get_recommendation(
            session=session,
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            raise ValueError(
                "Trade recommendation was not found. "
                f"recommendation_id={recommendation_id}"
            )

        if recommendation.status != "APPROVED":
            raise ValueError(
                "Trade recommendation must be APPROVED to retry. "
                f"recommendation_id={recommendation_id}, "
                f"status={recommendation.status}"
            )

        if recommendation.action not in {"BUY", "SELL"}:
            raise ValueError(
                "Trade recommendation action must be BUY or SELL. "
                f"recommendation_id={recommendation_id}, "
                f"action={recommendation.action}"
            )

        order_log = get_order_log(
            session=session,
            recommendation_id=recommendation_id,
        )

        if order_log is not None:
            raise ValueError(
                "Order log already exists. This recommendation is not retryable. "
                f"recommendation_id={recommendation_id}, "
                f"order_log_id={order_log.id}"
            )

        attempt = get_latest_mock_attempt(
            session=session,
            recommendation_id=recommendation_id,
        )

        if attempt is None:
            raise ValueError(
                "Mock order execution attempt was not found. "
                f"recommendation_id={recommendation_id}"
            )

        if attempt.status != "RETRYABLE_FAILED":
            raise ValueError(
                "Latest mock order attempt must be RETRYABLE_FAILED. "
                f"attempt_id={attempt.id}, "
                f"status={attempt.status}"
            )

        if attempt.next_retry_at is None:
            raise ValueError(
                "Latest mock order attempt does not have next_retry_at. "
                f"attempt_id={attempt.id}"
            )

        previous_next_retry_at = attempt.next_retry_at
        forced_next_retry_at = attempt.next_retry_at.now(tz=KST) - timedelta(
            seconds=seconds_ago,
        )

        attempt.next_retry_at = forced_next_retry_at

        session.commit()
        session.refresh(attempt)

        print("Mock order retry attempt was forced due.")
        print(f"recommendation_id={recommendation.id}")
        print(f"action={recommendation.action}")
        print(f"recommendation_status={recommendation.status}")
        print(f"attempt_id={attempt.id}")
        print(f"attempt_number={attempt.attempt_number}")
        print(f"attempt_status={attempt.status}")
        print(f"previous_next_retry_at={previous_next_retry_at}")
        print(f"forced_next_retry_at={attempt.next_retry_at}")


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description=(
            "Force the latest retryable mock order attempt to become due. "
            "No actual Upbit order is placed."
        ),
    )

    parser.add_argument(
        "--recommendation-id",
        type=int,
        required=True,
        help="Trade recommendation ID to force due.",
    )

    parser.add_argument(
        "--seconds-ago",
        type=int,
        default=1,
        help="How many seconds in the past next_retry_at should be moved. Defaults to 1.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    force_mock_order_retry_due(
        recommendation_id=args.recommendation_id,
        seconds_ago=args.seconds_ago,
    )
