import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo


OPERATIONAL_ALERT_WORKER_LOCK_KEY = 2026083002
KST = ZoneInfo("Asia/Seoul")


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect stale LIVE orders and deliver DB-backed operational alerts."
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    interval = settings.operational_alert_interval_seconds
    if not settings.operational_alerting_enabled:
        print("Operational alert worker inactive (disabled).", flush=True)
        while not once:
            time.sleep(interval)
        return

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
    from crypto_trading_bot.services.operational_alert_worker_service import (
        OperationalAlertWorkerService,
    )

    lock = PostgresAdvisoryLock(OPERATIONAL_ALERT_WORKER_LOCK_KEY)
    if not lock.acquire():
        print("Another operational alert worker is already running. Worker will exit.")
        return
    try:
        worker = OperationalAlertWorkerService(
            SessionLocal,
            telegram_chat_id=settings.telegram_chat_id,
            stale_after_seconds=settings.live_order_stale_alert_after_seconds,
            max_retries=settings.operational_alert_max_retries,
            retry_delays_minutes=settings.operational_alert_retry_delay_list,
            now_fn=lambda: datetime.now(KST),
        )
        print("Operational alert worker started. Alerting only.", flush=True)
        while True:
            started = time.monotonic()
            result = worker.run_cycle()
            print(
                "Operational alert cycle completed. "
                f"stale_order_count={result.stale_order_count} "
                f"created_alert_count={result.created_alert_count} "
                f"resolved_alert_count={result.resolved_alert_count} "
                f"delivered_count={result.delivered_count} "
                f"failed_delivery_count={result.failed_delivery_count}",
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
        print("Operational alert worker stopped.", flush=True)
    except Exception as error:
        print(f"Operational alert worker failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
