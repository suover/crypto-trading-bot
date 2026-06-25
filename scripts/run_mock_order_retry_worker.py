import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.services.mock_order_retry_notification_service import (
    NOTIFIABLE_RETRY_STATUSES,
    MockOrderRetryNotificationResult,
    MockOrderRetryNotificationService,
)
from crypto_trading_bot.services.mock_order_retry_service import (
    MockOrderRetryService,
    MockOrderRetrySummary,
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
            "Continuously process due mock order retries. "
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
        help="Run one retry cycle and exit.",
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


def print_notification_results(
    results: tuple[
        MockOrderRetryNotificationResult,
        ...,
    ],
) -> None:
    sent_count = 0
    skipped_count = 0
    failed_count = 0

    for result in results:
        print(
            "Retry notification result. "
            f"recommendation_id={result.recommendation_id}, "
            f"approval_request_id="
            f"{result.approval_request_id}, "
            f"attempt_id={result.attempt_id}, "
            f"retry_status={result.retry_status}, "
            f"notification_status="
            f"{result.notification_status}, "
            f"error={result.error_message}"
        )

        if result.notification_status == "SENT":
            sent_count += 1
        elif result.notification_status == "SKIPPED":
            skipped_count += 1
        else:
            failed_count += 1

    print("Retry notification summary.")
    print(f"sent_count={sent_count}")
    print(f"skipped_count={skipped_count}")
    print(f"failed_count={failed_count}")


def notify_retry_results(
    summary: MockOrderRetrySummary,
    telegram_client: TelegramClient | None = None,
) -> tuple[
    MockOrderRetryNotificationResult,
    ...,
]:
    notifiable_results = tuple(
        result
        for result in summary.results
        if result.status in NOTIFIABLE_RETRY_STATUSES
    )

    if not notifiable_results:
        return ()

    if telegram_client is None:
        try:
            resolved_telegram_client = TelegramClient()
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"

            return tuple(
                MockOrderRetryNotificationResult(
                    recommendation_id=(retry_result.recommendation_id),
                    approval_request_id=(retry_result.approval_request_id),
                    attempt_id=retry_result.attempt_id,
                    retry_status=retry_result.status,
                    notification_status="FAILED",
                    error_message=error_message,
                )
                for retry_result in notifiable_results
            )
    else:
        resolved_telegram_client = telegram_client

    notification_results: list[MockOrderRetryNotificationResult] = []

    with SessionLocal() as session:
        service = MockOrderRetryNotificationService(
            session=session,
            telegram_client=resolved_telegram_client,
        )

        for retry_result in notifiable_results:
            notification_result = service.notify(
                retry_result=retry_result,
            )

            notification_results.append(notification_result)

    return tuple(notification_results)


def run_retry_cycle(
    limit: int,
    upbit_client: UpbitClient | None = None,
    telegram_client: TelegramClient | None = None,
) -> MockOrderRetrySummary:
    service = MockOrderRetryService(
        session_factory=SessionLocal,
        upbit_client=upbit_client,
    )

    summary = service.retry_pending(
        limit=limit,
    )

    print(f"Retry worker cycle completed. completed_at={datetime.now(KST).isoformat()}")

    print_retry_summary(summary)

    notification_results = notify_retry_results(
        summary=summary,
        telegram_client=telegram_client,
    )

    print_notification_results(
        results=notification_results,
    )

    return summary


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
