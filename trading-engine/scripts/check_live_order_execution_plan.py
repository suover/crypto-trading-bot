from argparse import ArgumentParser, Namespace
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.services.live_order_execution_service import (
    LiveOrderExecutionError,
    LiveOrderExecutionService,
)
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
    LiveOrderSafetyError,
    check_live_order_safety,
)


def mask_configured(value: str) -> str:
    return "configured" if value else "missing"


def format_decimal(value: object) -> str:
    if value is None:
        return "-"

    return str(Decimal(str(value)))


def get_recommendation(
    session: Session,
    recommendation_id: int,
) -> TradeRecommendation | None:
    return session.get(
        TradeRecommendation,
        recommendation_id,
    )


def get_order_log(
    session: Session,
    recommendation_id: int,
) -> OrderLog | None:
    statement = (
        select(OrderLog)
        .where(OrderLog.recommendation_id == recommendation_id)
        .order_by(OrderLog.id.desc())
        .limit(1)
    )

    return session.scalar(statement)


def print_settings_summary() -> None:
    settings = get_settings()
    safety_check = check_live_order_safety(settings)

    print("Live order safety settings")
    print("-" * 60)
    print(f"app_env={settings.app_env}")
    print(f"trading_mode={settings.trading_mode}")
    print(f"live_order_enabled={settings.live_order_enabled}")
    print(
        "live_order_confirmation_matches="
        f"{settings.live_order_confirmation == LIVE_ORDER_CONFIRMATION_TEXT}"
    )
    print(f"upbit_access_key={mask_configured(settings.upbit_access_key)}")
    print(f"upbit_secret_key={mask_configured(settings.upbit_secret_key)}")
    print(f"allowed_markets={settings.allowed_market_list}")
    print(f"max_order_amount_krw={settings.max_order_amount_krw}")
    print(f"daily_max_order_amount_krw={settings.daily_max_order_amount_krw}")
    print(f"safety_ready={safety_check.ready}")

    if safety_check.reasons:
        print("safety_reasons:")
        for reason in safety_check.reasons:
            print(f"- {reason}")


def print_recommendation_summary(
    recommendation: TradeRecommendation,
) -> None:
    print("Trade recommendation")
    print("-" * 60)
    print(f"id={recommendation.id}")
    print(f"user_id={recommendation.user_id}")
    print(f"exchange={recommendation.exchange}")
    print(f"market={recommendation.market}")
    print(f"action={recommendation.action}")
    print(f"status={recommendation.status}")
    print(f"confidence={recommendation.confidence}")
    print(
        f"recommended_amount_krw={format_decimal(recommendation.recommended_amount_krw)}"
    )
    print(f"recommended_quantity={format_decimal(recommendation.recommended_quantity)}")
    print(f"ai_model={recommendation.ai_model}")


def print_order_log_summary(
    order_log: OrderLog | None,
) -> None:
    print("Existing order log")
    print("-" * 60)

    if order_log is None:
        print("order_log=-")
        return

    print(f"id={order_log.id}")
    print(f"recommendation_id={order_log.recommendation_id}")
    print(f"approval_request_id={order_log.approval_request_id}")
    print(f"trading_mode={order_log.trading_mode}")
    print(f"exchange={order_log.exchange}")
    print(f"market={order_log.market}")
    print(f"side={order_log.side}")
    print(f"order_type={order_log.order_type}")
    print(f"amount_krw={format_decimal(order_log.amount_krw)}")
    print(f"quantity={format_decimal(order_log.quantity)}")
    print(f"status={order_log.status}")
    print(f"exchange_order_id={order_log.exchange_order_id}")


def print_execution_plan(
    service: LiveOrderExecutionService,
    recommendation_id: int,
) -> bool:
    print("Live order execution plan")
    print("-" * 60)

    try:
        plan = service.build_execution_plan(
            recommendation_id=recommendation_id,
        )
    except (LiveOrderExecutionError, LiveOrderSafetyError) as error:
        print("plan_ready=False")
        print(f"error_type={type(error).__name__}")
        print(f"error={error}")
        return False

    print("plan_ready=True")
    print(f"recommendation_id={plan.recommendation_id}")
    print(f"exchange={plan.exchange}")
    print(f"market={plan.market}")
    print(f"action={plan.action}")
    print(f"amount_krw={format_decimal(plan.amount_krw)}")
    print(f"quantity={format_decimal(plan.quantity)}")
    print(f"ready_to_execute={plan.ready_to_execute}")
    return True


def check_live_order_execution_plan(
    recommendation_id: int,
    strict: bool,
) -> None:
    print("Live order execution plan check")
    print("=" * 60)
    print("Actual Upbit order will not be executed.")
    print("LiveOrderExecutionService.execute() will not be called.")
    print("=" * 60)

    with SessionLocal() as session:
        print_settings_summary()
        print()

        recommendation = get_recommendation(
            session=session,
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            print("Trade recommendation")
            print("-" * 60)
            print(f"recommendation_id={recommendation_id}")
            print("found=False")

            if strict:
                raise SystemExit(1)

            return

        print_recommendation_summary(
            recommendation=recommendation,
        )
        print()

        order_log = get_order_log(
            session=session,
            recommendation_id=recommendation_id,
        )

        print_order_log_summary(
            order_log=order_log,
        )
        print()

        if order_log is not None:
            print("Live order execution plan")
            print("-" * 60)
            print("plan_ready=False")
            print(
                "error=Existing order log already exists. Duplicate live order is blocked."
            )

            if strict:
                raise SystemExit(1)

            return

        service = LiveOrderExecutionService(
            session=session,
        )

        plan_ready = print_execution_plan(
            service=service,
            recommendation_id=recommendation_id,
        )

        if strict and not plan_ready:
            raise SystemExit(1)


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description=(
            "Check whether a recommendation can be executed as a live Upbit order. "
            "No actual Upbit order is placed."
        ),
    )

    parser.add_argument(
        "--recommendation-id",
        type=int,
        required=True,
        help="Trade recommendation ID to check.",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with a non-zero status when the live execution plan is not ready.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    check_live_order_execution_plan(
        recommendation_id=args.recommendation_id,
        strict=args.strict,
    )
