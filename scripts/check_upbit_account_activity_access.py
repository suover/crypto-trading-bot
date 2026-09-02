import argparse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import UpbitOrderReadError


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(
        description="Check read-only Upbit account activity API permissions."
    ).parse_args(args)


def _check(name: str, operation: Callable[[], list[dict[str, Any]]]) -> bool:
    try:
        operation()
    except UpbitOrderReadError as error:
        status = (
            "OUT_OF_SCOPE"
            if error.safe_error.upbit_error_name == "out_of_scope"
            else "UNAVAILABLE"
        )
        print(f"{name}={status}")
        print(f"{name}_error_type={error.safe_error.error_type}")
        print(f"{name}_safe_error_code={error.safe_error.upbit_error_name or '-'}")
        print(f"{name}_status_code={error.safe_error.status_code or '-'}")
        return False
    except Exception as error:
        print(f"{name}=UNAVAILABLE")
        print(f"{name}_error_type={type(error).__name__}")
        print(f"{name}_safe_error_code=-")
        print(f"{name}_status_code=-")
        return False
    print(f"{name}=AVAILABLE")
    return True


def run_diagnostic(client: UpbitClient) -> bool:
    now = datetime.now(UTC)
    print("Upbit account activity access diagnostic")
    results = (
        _check(
            "closed_orders",
            lambda: client.get_closed_orders(
                start_time=(now - timedelta(hours=1)).isoformat(),
                end_time=now.isoformat(),
                limit=1,
                order_by="desc",
            ),
        ),
        _check("deposits", lambda: client.get_deposits(page=1, limit=1)),
        _check("withdrawals", lambda: client.get_withdrawals(page=1, limit=1)),
    )
    return all(results)


def main(args: list[str] | None = None) -> int:
    parse_arguments(args)
    return 0 if run_diagnostic(UpbitClient()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
