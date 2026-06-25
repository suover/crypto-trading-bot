import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.mock_order_retry_service import (
    MockOrderRetryService,
    MockOrderRetrySummary,
)
from crypto_trading_bot.services.order_retry_notification_outbox_service import (
    OrderRetryNotificationDeliverySummary,
    OrderRetryNotificationOutboxService,
)
from scripts.retry_pending_mock_orders import print_retry_result


KST = ZoneInfo("Asia/Seoul")

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_LIMIT = 100

MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 3600
MAX_LIMIT = 100


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continuously process due mock order retries "
            "and Telegram notification outbox deliveries. "
            "No actual Upbit order is placed."
        )
    )

    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=(
            f"Retry polling interval in seconds. Default: {DEFAULT_INTERVAL_SECONDS}"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Maximum number of candidates processed "
            f"in one cycle. Default: {DEFAULT_LIMIT}"
        ),
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one worker cycle and exit.",
    )

    arguments = parser.parse_args()

    if not (MIN_INTERVAL_SECONDS <= arguments.interval_seconds <= MAX_INTERVAL_SECONDS):
        parser.error(
            "--interval-seconds must be between "
            f"{MIN_INTERVAL_SECONDS} and "
            f"{MAX_INTERVAL_SECONDS}"
        )

    if arguments.limit <= 0:
        parser.error("--limit must be greater than 0")

    if arguments.limit > MAX_LIMIT:
        parser.error(f"--limit must not exceed {MAX_LIMIT}")

    return arguments


def print_retry_summary(
    summary: MockOrderRetrySummary,
) -> None:
    for result in summary.results:
        print_retry_result(result)

    print("Retry worker cycle summary.")
    print(f"candidate_count={summary.candidate_count}")
    print(f"executed_count={summary.executed_count}")
    print(f"already_executed_count={summary.already_executed_count}")
    print(f"retryable_failed_count={summary.retryable_failed_count}")
    print(f"permanent_failed_count={summary.permanent_failed_count}")
    print(f"retry_exhausted_count={summary.retry_exhausted_count}")


def print_outbox_delivery_summary(
    summary: OrderRetryNotificationDeliverySummary,
) -> None:
    for result in summary.results:
        print(
            "Outbox delivery result. "
            f"notification_id={result.notification_id}, "
            f"attempt_id={result.attempt_id}, "
            f"retry_status={result.retry_status}, "
            f"delivery_status={result.delivery_status}, "
            f"retry_count={result.retry_count}, "
            f"next_retry_at={result.next_retry_at}, "
            f"error={result.error_message}"
        )

    print("Outbox delivery summary.")
    print(f"processed_count={summary.processed_count}")
    print(f"sent_count={summary.sent_count}")
    print(f"failed_count={summary.failed_count}")


def run_retry_cycle(
    limit: int,
    upbit_client: UpbitClient | None = None,
    telegram_client: TelegramClient | None = None,
) -> MockOrderRetrySummary:
    retry_service = MockOrderRetryService(
        session_factory=SessionLocal,
        upbit_client=upbit_client,
    )

    retry_summary = retry_service.retry_pending(
        limit=limit,
    )

    print(f"Retry worker cycle completed. completed_at={datetime.now(KST).isoformat()}")

    print_retry_summary(
        summary=retry_summary,
    )

    # 이번 주문 재시도 결과가 없어도 기존에 전송 실패한
    # Outbox 알림의 재전송 시각이 도래했을 수 있으므로
    # Outbox 처리는 매 사이클 항상 실행한다.
    outbox_service = OrderRetryNotificationOutboxService(
        session_factory=SessionLocal,
        telegram_client=telegram_client,
    )

    delivery_summary = outbox_service.process_due(
        limit=limit,
    )

    print_outbox_delivery_summary(
        summary=delivery_summary,
    )

    return retry_summary


def run_worker(
    interval_seconds: int,
    limit: int,
    once: bool,
) -> None:
    print("Mock order retry worker started.")
    print(f"interval_seconds={interval_seconds}")
    print(f"limit={limit}")
    print(f"once={once}")
    print("Actual Upbit order will not be executed.")

    try:
        while True:
            cycle_started_at = time.monotonic()

            try:
                run_retry_cycle(
                    limit=limit,
                )
            except Exception as error:
                print(
                    "Retry worker cycle failed. "
                    f"error_type={type(error).__name__}, "
                    f"error={error}"
                )

                if once:
                    raise

            if once:
                return

            elapsed_seconds = time.monotonic() - cycle_started_at

            sleep_seconds = max(
                0,
                interval_seconds - elapsed_seconds,
            )

            time.sleep(sleep_seconds)

    except KeyboardInterrupt:
        print("Mock order retry worker stopped.")


if __name__ == "__main__":
    arguments = parse_arguments()

    run_worker(
        interval_seconds=arguments.interval_seconds,
        limit=arguments.limit,
        once=arguments.once,
    )
