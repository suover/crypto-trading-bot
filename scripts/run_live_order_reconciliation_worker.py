import argparse
import time

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.services.live_order_reconciliation_worker_service import (
    LiveOrderReconciliationWorkerService,
)


LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY = 2026082801


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll existing LIVE Upbit orders. No creation, retry or cancellation."
    )
    parser.add_argument(
        "--once", action="store_true", help="Process one batch and exit."
    )
    return parser.parse_args(args)


def run_worker(*, once: bool = False) -> None:
    settings = get_settings()
    interval = settings.live_order_reconciliation_interval_seconds
    if (
        not settings.live_order_reconciliation_enabled
        or settings.order_execution_mode != "LIVE"
    ):
        print(
            "Live order reconciliation worker inactive (disabled or non-LIVE mode).",
            flush=True,
        )
        # Keep disabled Compose runtimes idle instead of entering a restart loop.
        while not once:
            time.sleep(interval)
        return

    # Initialize DB/config inside main's sanitized error boundary, and only
    # for an active worker. Inactive --once must not initialize a DB connection.
    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

    lock = PostgresAdvisoryLock(lock_key=LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY)
    if not lock.acquire():
        print(
            "Another live order reconciliation worker is already running. Worker will exit.",
            flush=True,
        )
        return
    try:
        worker = LiveOrderReconciliationWorkerService(session_factory=SessionLocal)
        print(
            "Live order reconciliation worker started. Existing order GET only.",
            flush=True,
        )
        while True:
            started = time.monotonic()
            results = worker.reconcile_pending(
                limit=settings.live_order_reconciliation_batch_size
            )
            for result in results:
                print(
                    f"order_log_id={result.order_log_id} recommendation_id={result.recommendation_id} "
                    f"outcome={result.outcome} status={result.status} "
                    f"error_type={result.error_type} status_code={result.status_code}",
                    flush=True,
                )
            print(
                f"Reconciliation cycle completed. candidate_count={len(results)}",
                flush=True,
            )
            if once:
                return
            time.sleep(max(0, interval - (time.monotonic() - started)))
    finally:
        lock.release()


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        run_worker(once=namespace.once)
    except KeyboardInterrupt:
        print("Live order reconciliation worker stopped.", flush=True)
    except Exception as error:
        # Fail the process on DB/config/programming errors; never print a raw
        # exception/traceback that may include credentials or a connection URL.
        print(
            f"Live order reconciliation worker failed. error_type={type(error).__name__}",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
