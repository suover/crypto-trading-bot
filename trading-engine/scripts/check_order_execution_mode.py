from argparse import ArgumentParser, Namespace

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
)
from crypto_trading_bot.services.order_execution_mode import (
    OrderExecutionModeError,
    check_order_execution_mode,
)


def mask_configured(value: str) -> str:
    return "configured" if value else "missing"


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description=(
            "Check current order execution mode. No actual Upbit order is placed."
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with an error when the current order execution mode is not ready.",
    )

    return parser.parse_args()


def main(strict: bool) -> None:
    settings = get_settings()

    print("Order execution mode check")
    print("=" * 60)
    print("Actual Upbit order will not be executed.")
    print("=" * 60)
    print(f"app_env={settings.app_env}")
    print(f"trading_mode={settings.trading_mode}")
    print(f"order_execution_mode={settings.order_execution_mode}")
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
    print("-" * 60)

    try:
        check = check_order_execution_mode(settings)
    except OrderExecutionModeError as error:
        print("mode_ready=False")
        print(f"error_type={type(error).__name__}")
        print(f"error={error}")

        if strict:
            raise SystemExit(1)

        return

    print(f"normalized_mode={check.mode}")
    print(f"mode_ready={check.ready}")
    print(f"live_safety_ready={check.live_safety_ready}")

    if check.reasons:
        print("reasons:")
        for reason in check.reasons:
            print(f"- {reason}")

    if check.live_safety_reasons:
        print("live_safety_reasons:")
        for reason in check.live_safety_reasons:
            print(f"- {reason}")

    if strict and not check.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    args = parse_args()

    main(
        strict=args.strict,
    )
