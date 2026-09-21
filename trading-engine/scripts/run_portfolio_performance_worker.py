import argparse
import time


PORTFOLIO_PERFORMANCE_WORKER_LOCK_KEY = 2026090301


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run account-level portfolio performance accounting."
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_cycle(session_factory) -> tuple[int, int]:
    from sqlalchemy import select

    from crypto_trading_bot.db.models import PortfolioSnapshot
    from crypto_trading_bot.services.cash_flow_valuation_service import (
        CashFlowValuationService,
    )
    from crypto_trading_bot.services.portfolio_performance_service import (
        PortfolioPerformanceService,
    )

    with session_factory() as session:
        scopes = tuple(
            sorted(
                set(
                    session.execute(
                        select(PortfolioSnapshot.user_id, PortfolioSnapshot.exchange)
                    )
                )
            )
        )
        rebuilt = 0
        for user_id, exchange in scopes:
            cash_result = CashFlowValuationService(session).value_scope(
                user_id, exchange=exchange, apply=True
            )
            PortfolioPerformanceService(session).rebuild(
                user_id,
                exchange=exchange,
                valuation_plans=cash_result.plans,
                apply=True,
            )
            rebuilt += 1
        session.commit()
    return len(scopes), rebuilt


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    interval = settings.portfolio_performance_interval_seconds
    if not settings.portfolio_performance_enabled:
        print("Portfolio performance worker inactive (disabled).", flush=True)
        while not once:
            time.sleep(interval)
        return

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

    lock = PostgresAdvisoryLock(PORTFOLIO_PERFORMANCE_WORKER_LOCK_KEY)
    if not lock.acquire():
        print(
            "Another portfolio performance worker is already running. Worker will exit."
        )
        return
    try:
        print("Portfolio performance worker started.", flush=True)
        while True:
            started = time.monotonic()
            scope_count, rebuilt_count = run_cycle(SessionLocal)
            print(
                f"Portfolio performance cycle completed. scope_count={scope_count} "
                f"rebuilt_count={rebuilt_count}",
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
        print("Portfolio performance worker stopped.", flush=True)
    except Exception as error:
        print(f"Portfolio performance worker failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
