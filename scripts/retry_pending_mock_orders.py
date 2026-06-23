import argparse

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
            "Retry approved trade recommendations without "
            "an order log. No actual Upbit order is placed."
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
        help=(
            "Only list retry candidates without executing "
            "mock orders"
        ),
    )

    arguments = parser.parse_args()

    if arguments.limit <= 0:
        parser.error("--limit must be greater than 0")

    if arguments.limit > MAX_LIMIT:
        parser.error(
            f"--limit must not exceed {MAX_LIMIT}"
        )

    return arguments


def print_retry_result(
    result: MockOrderRetryResult,
) -> None:
    print(
        "Retry result. "
        f"recommendation_id={result.recommendation_id}, "
        f"approval_request_id={result.approval_request_id}, "
        f"status={result.status}, "
        f"order_log_id={result.order_log_id}, "
        f"error={result.error_message}"
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
    print(
        "already_executed_count="
        f"{summary.already_executed_count}"
    )
    print(f"rejected_count={summary.rejected_count}")
    print("Actual Upbit order was not executed.")


if __name__ == "__main__":
    arguments = parse_arguments()

    run_retry(
        limit=arguments.limit,
        dry_run=arguments.dry_run,
    )