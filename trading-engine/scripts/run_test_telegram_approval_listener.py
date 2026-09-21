from scripts.execute_test_mock_order import TestUpbitClient
from scripts.run_telegram_approval_listener import (
    run_telegram_approval_listener,
)


if __name__ == "__main__":
    print("Telegram test approval listener started.")
    print("Mock order data source: TestUpbitClient")
    print("Actual Upbit order will not be executed.")

    run_telegram_approval_listener(
        order_upbit_client=TestUpbitClient(),
    )
