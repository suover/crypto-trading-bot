"""Shadow Review evaluation, committed outbox creation and optional delivery."""

import argparse
from dataclasses import asdict
import json
import time


SHADOW_REVIEW_MONITOR_LOCK_KEY = 2026110901


def run_worker(
    *,
    once=False,
    apply=False,
    settings=None,
    session_factory=None,
    lock_factory=None,
    telegram_factory=None,
    sleep_fn=time.sleep,
) -> int:
    from crypto_trading_bot.config.settings import get_settings
    from crypto_trading_bot.services.shadow_review_monitor_service import (
        ShadowReviewMonitorResult,
        ShadowReviewMonitorService,
    )

    settings = settings or get_settings()
    enabled = settings.shadow_review_monitor_enabled
    interval = settings.shadow_review_monitor_interval_seconds
    apply_cycle = apply if once else enabled

    def report(status):
        result = ShadowReviewMonitorResult.from_settings(settings, apply=apply_cycle)
        result.status = status
        return result

    if apply and (not once or not enabled):
        _emit(report("APPLY_DISABLED"))
        return 1
    if not once and not enabled:
        _emit(report("DISABLED"))
        while True:
            sleep_fn(interval)

    if session_factory is None:
        from crypto_trading_bot.db.database import SessionLocal

        session_factory = SessionLocal
    if lock_factory is None:
        from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

        lock_factory = PostgresAdvisoryLock

    monitor = ShadowReviewMonitorService(session_factory, settings=settings)
    delivery = None
    if apply_cycle and settings.shadow_review_telegram_notifications_enabled:
        from crypto_trading_bot.services.shadow_review_delivery_service import (
            ShadowReviewDeliveryService,
        )

        kwargs = {"telegram_factory": telegram_factory} if telegram_factory else {}
        delivery = ShadowReviewDeliveryService(
            session_factory, settings=settings, **kwargs
        )

    lock = lock_factory(SHADOW_REVIEW_MONITOR_LOCK_KEY)
    while True:
        result = report("SUCCESS")
        try:
            if not lock.acquire():
                _emit(report("LOCK_NOT_ACQUIRED"))
                return 0
            result = monitor.run_cycle(apply=apply_cycle)
            if delivery is not None:
                delivery.process_due(result)
        except Exception as error:
            result.status = "EXECUTION_ERROR"
            result.safe_reason = type(error).__name__
        finally:
            lock.release()
        _emit(result)
        if once:
            return int(result.status in {"EXECUTION_ERROR", "PARTIAL"})
        sleep_fn(interval)


def _emit(result) -> None:
    print(json.dumps(asdict(result), sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run a read-only preview")
    parser.add_argument(
        "--apply", action="store_true", help="enable writes for this cycle"
    )
    args = parser.parse_args(argv)
    if args.apply and not args.once:
        parser.error("--apply requires --once")
    try:
        return run_worker(once=args.once, apply=args.apply)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "report_type": "SHADOW_REVIEW_MONITOR_CYCLE",
                    "status": "EXECUTION_ERROR",
                    "safe_reason": type(error).__name__,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
