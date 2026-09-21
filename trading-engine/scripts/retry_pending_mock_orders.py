import argparse
from datetime import datetime

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.mock_order_retry_service import (
    MockOrderRetryResult,
    MockOrderRetryService,
)


DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retry approved trade recommendations according to "
            "the mock order retry policy. "
            "No actual Upbit order is placed."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Maximum number of retry candidates. "
            f"Default: {DEFAULT_LIMIT}, maximum: {MAX_LIMIT}"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=("Only list retry candidates without executing mock orders"),
    )

    arguments = parser.parse_args()

    if arguments.limit <= 0:
        parser.error("--limit must be greater than 0")

    if arguments.limit > MAX_LIMIT:
        parser.error(f"--limit must not exceed {MAX_LIMIT}")

    return arguments


def format_optional_datetime(
    value: datetime | None,
) -> str:
    if value is None:
        return "-"

    return value.isoformat()


def print_retry_result(
    result: MockOrderRetryResult,
) -> None:
    print(
        "Retry result. "
        f"recommendation_id={result.recommendation_id}, "
        f"approval_request_id={result.approval_request_id}, "
        f"attempt_id={result.attempt_id}, "
        f"attempt_number={result.attempt_number}, "
        f"status={result.status}, "
        f"order_log_id={result.order_log_id}, "
        f"error_code={result.error_code}, "
        f"error={result.error_message}, "
        f"next_retry_at="
        f"{format_optional_datetime(result.next_retry_at)}"
    )


def run_retry(
    limit: int,
    dry_run: bool,
) -> None:
    service = MockOrderRetryService(
        session_factory=SessionLocal,
    )

    candidates = service.get_candidates(
        limit=limit,
    )

    print("Pending mock order retry started.")
    print(f"candidate_count={len(candidates)}")
    print(f"dry_run={dry_run}")

    if not candidates:
        print("No pending mock order retry candidates.")
        print("Actual Upbit order was not executed.")
        return

    if dry_run:
        for candidate in candidates:
            print(
                "Retry candidate. "
                f"recommendation_id="
                f"{candidate.recommendation_id}, "
                f"approval_request_id="
                f"{candidate.approval_request_id}"
            )

        print("Dry run completed.")
        print("Actual Upbit order was not executed.")
        return

    summary = service.retry_candidates(
        candidates=candidates,
    )

    for result in summary.results:
        print_retry_result(
            result=result,
        )

    print("Retry summary.")
    print(f"candidate_count={summary.candidate_count}")
    print(f"executed_count={summary.executed_count}")
    print(f"already_executed_count={summary.already_executed_count}")
    print(f"retryable_failed_count={summary.retryable_failed_count}")
    print(f"permanent_failed_count={summary.permanent_failed_count}")
    print(f"retry_exhausted_count={summary.retry_exhausted_count}")
    print("Actual Upbit order was not executed.")


if __name__ == "__main__":
    arguments = parse_arguments()

    run_retry(
        limit=arguments.limit,
        dry_run=arguments.dry_run,
    )
