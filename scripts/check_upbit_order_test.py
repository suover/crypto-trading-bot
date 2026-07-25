from argparse import ArgumentParser, Namespace
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_safety import MIN_UPBIT_ORDER_AMOUNT_KRW


def parse_args(args: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(description="Run the official Upbit order test API.")
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--amount-krw", default="5000")
    return parser.parse_args(args)


def run_order_test(
    *,
    market: str,
    amount_krw: Decimal,
    client: UpbitClient,
    identifier_factory: Callable[[], str] | None = None,
) -> dict[str, object]:
    settings = get_settings()
    if not settings.upbit_access_key or not settings.upbit_secret_key:
        raise ValueError("Upbit API keys are not configured")
    if market not in settings.allowed_market_list:
        raise ValueError(f"Market is not allowed. market={market}")
    if amount_krw < MIN_UPBIT_ORDER_AMOUNT_KRW:
        raise ValueError("Amount is below the Upbit minimum")
    if amount_krw > Decimal(str(settings.max_order_amount_krw)):
        raise ValueError("Amount exceeds MAX_ORDER_AMOUNT_KRW")
    factory = identifier_factory or (lambda: uuid4().hex)
    identifier = f"order-test-{factory()}"[:40]
    return client.test_market_buy_order(
        market=market,
        amount_krw=amount_krw,
        identifier=identifier,
    )


def main(args: list[str] | None = None) -> int:
    namespace = parse_args(args)
    try:
        amount = Decimal(namespace.amount_krw)
        run_order_test(
            market=namespace.market,
            amount_krw=amount,
            client=UpbitClient(),
        )
    except Exception as error:
        print("Upbit order test")
        print("=" * 60)
        print("Actual Upbit order will not be executed.")
        print(f"market={namespace.market}")
        print(f"amount_krw={namespace.amount_krw}")
        print("result=FAIL")
        print(f"error_type={type(error).__name__}")
        return 1
    print("Upbit order test")
    print("=" * 60)
    print("Actual Upbit order will not be executed.")
    print(f"market={namespace.market}")
    print(f"amount_krw={amount}")
    print("result=SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
