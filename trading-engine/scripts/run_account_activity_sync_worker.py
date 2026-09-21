import argparse
import time
from datetime import timedelta


ACCOUNT_ACTIVITY_SYNC_WORKER_LOCK_KEY = 2026090203


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync read-only Upbit account activity into its isolated ledger."
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_cycle(session_factory, service_factory, *, user_id: int, overlap: timedelta):
    from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver

    with session_factory() as session:
        user = RuntimeUserResolver(session).resolve(user_id)
        return service_factory(session, overlap=overlap).run(user.id, apply=True)


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings
    from crypto_trading_bot.services.runtime_user_resolver import (
        require_trading_user_id,
    )

    settings = get_settings()
    interval = settings.account_activity_sync_interval_seconds
    if not settings.account_activity_sync_enabled:
        print("Account activity sync worker inactive (disabled).", flush=True)
        while not once:
            time.sleep(interval)
        return

    user_id = require_trading_user_id(settings.trading_user_id)

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
    from crypto_trading_bot.services.account_activity_sync_service import (
        AccountActivitySyncService,
    )

    lock = PostgresAdvisoryLock(ACCOUNT_ACTIVITY_SYNC_WORKER_LOCK_KEY)
    if not lock.acquire():
        print("Another account activity sync worker is running. Worker will exit.")
        return
    try:
        print("Account activity sync worker started. Upbit GET only.", flush=True)
        while True:
            started = time.monotonic()
            result = run_cycle(
                SessionLocal,
                AccountActivitySyncService,
                user_id=user_id,
                overlap=timedelta(hours=settings.account_activity_sync_overlap_hours),
            )
            statuses = ",".join(
                f"{source.source_type}:{source.status}" for source in result.sources
            )
            print(f"Account activity sync cycle completed. sources={statuses}")
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
        print("Account activity sync worker stopped.", flush=True)
    except Exception as error:
        print(f"Account activity sync worker failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
