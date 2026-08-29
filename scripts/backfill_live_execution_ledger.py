import argparse

from crypto_trading_bot.services.live_execution_ledger_backfill_service import (
    LiveExecutionLedgerBackfillService,
)


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize execution data from stored OrderLog raw JSON. "
            "No network or exchange operation is performed."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. The default dry-run does not persist changes.",
    )
    parser.add_argument("--limit", type=int, default=None)
    namespace = parser.parse_args(args)
    if namespace.limit is not None and namespace.limit <= 0:
        parser.error("--limit must be greater than 0")
    return namespace


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        # Lazy DB import keeps --help network/DB-free and puts configuration
        # failures inside the sanitized error boundary.
        from crypto_trading_bot.db.database import SessionLocal

        summary = LiveExecutionLedgerBackfillService(SessionLocal).run(
            apply=namespace.apply, limit=namespace.limit
        )
    except Exception as error:
        print(f"Execution ledger backfill failed. error_type={type(error).__name__}")
        return 1
    print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
    print(f"processed_count={summary.processed_count}")
    print(f"eligible_count={summary.eligible_count}")
    print(f"applied_count={summary.applied_count}")
    print(f"skipped_count={summary.skipped_count}")
    print(f"error_count={summary.error_count}")
    print(f"normalized_fill_count={summary.normalized_fill_count}")
    return 1 if summary.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
