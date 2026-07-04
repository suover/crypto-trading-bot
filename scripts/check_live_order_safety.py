from argparse import ArgumentParser, Namespace

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
    assert_live_order_safety_enabled,
    check_live_order_safety,
)


def mask_configured(value: str) -> str:
    return "configured" if value else "missing"


def parse_args() -> Namespace:
    parser = ArgumentParser(
        description=(
            "Check whether live Upbit order execution safety settings are ready. "
            "No actual Upbit order is placed."
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with an error when live order safety is not ready.",
    )

    return parser.parse_args()


def main(strict: bool) -> None:
    settings = get_settings()
    check = check_live_order_safety(settings)

    print("Live order safety check")
    print("=" * 60)
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
    print("-" * 60)
    print(f"ready={check.ready}")

    if check.reasons:
        print("reasons:")
        for reason in check.reasons:
            print(f"- {reason}")

    print("Actual Upbit order was not executed.")

    if strict:
        assert_live_order_safety_enabled(settings)


if __name__ == "__main__":
    args = parse_args()

    main(
        strict=args.strict,
    )
