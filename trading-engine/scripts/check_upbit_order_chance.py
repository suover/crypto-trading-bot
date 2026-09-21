from argparse import ArgumentParser, Namespace

from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import UpbitOrderOperationError
from crypto_trading_bot.services.upbit_order_chance_service import (
    UpbitOrderChanceValidationError,
    parse_upbit_order_chance,
)


def parse_args(args: list[str] | None = None) -> Namespace:
    parser = ArgumentParser(
        description="Read current Upbit order conditions. GET only; no order is placed."
    )
    parser.add_argument("--market", default="KRW-BTC")
    return parser.parse_args(args)


def run_diagnostic(*, market: str, client: UpbitClient) -> bool:
    print("Upbit order chance diagnostic")
    print(f"market={market}")
    try:
        chance = parse_upbit_order_chance(
            client.get_order_chance(market), expected_market=market
        )
    except UpbitOrderOperationError as error:
        print("result=FAIL")
        print(f"error_type={type(error).__name__}")
        print(f"safe_error_code={error.safe_error.error_type}")
        print(f"status_code={error.safe_error.status_code or '-'}")
        return False
    except UpbitOrderChanceValidationError as error:
        print("result=FAIL")
        print(f"error_type={type(error).__name__}")
        print(f"safe_error_code={error.reason_code}")
        print("status_code=-")
        return False
    except Exception as error:
        print("result=FAIL")
        print(f"error_type={type(error).__name__}")
        print("safe_error_code=UNKNOWN")
        print("status_code=-")
        return False

    print("result=SUCCESS")
    print(f"bid_fee={chance.bid_fee}")
    print(f"ask_fee={chance.ask_fee}")
    print(f"bid_min_total={chance.rules.bid_min_total}")
    print(f"ask_min_total={chance.rules.ask_min_total}")
    print(f"max_total={chance.rules.max_total}")
    print(f"bid_available_balance={chance.bid_account.available_balance}")
    print(f"ask_available_balance={chance.ask_account.available_balance}")
    print(f"market_buy_supported={'price' in chance.rules.bid_types}")
    print(f"market_sell_supported={'market' in chance.rules.ask_types}")
    return True


def main(args: list[str] | None = None) -> int:
    namespace = parse_args(args)
    return 0 if run_diagnostic(market=namespace.market, client=UpbitClient()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
