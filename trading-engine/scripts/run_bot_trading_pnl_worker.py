import argparse
import time


BOT_TRADING_PNL_WORKER_LOCK_KEY = 2026083001


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the DB-only bot PnL worker.")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_cycle(session_factory) -> tuple[int, int]:
    from sqlalchemy import select

    from crypto_trading_bot.db.models import BotTradingPnlSummary, OrderLog
    from crypto_trading_bot.services.bot_trading_pnl_service import (
        TERMINAL_PNL_SOURCE_STATUSES,
        BotTradingPnlService,
    )

    with session_factory() as session:
        source_scopes = set(
            session.execute(
                select(OrderLog.user_id, OrderLog.exchange).where(
                    OrderLog.trading_mode == "LIVE",
                    OrderLog.exchange == "UPBIT",
                    OrderLog.status.in_(TERMINAL_PNL_SOURCE_STATUSES),
                )
            )
        )
        existing_scopes = set(
            session.execute(
                select(
                    BotTradingPnlSummary.user_id, BotTradingPnlSummary.exchange
                ).where(BotTradingPnlSummary.exchange == "UPBIT")
            )
        )
        scopes = sorted(source_scopes | existing_scopes)
        rebuilt = 0
        for user_id, exchange in scopes:
            service = BotTradingPnlService(session)
            if not service.source_changed(user_id, exchange=exchange):
                continue
            service.rebuild(user_id, exchange=exchange, apply=True)
            rebuilt += 1
        session.commit()
    return len(scopes), rebuilt


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    interval = settings.bot_trading_pnl_interval_seconds
    if not settings.bot_trading_pnl_enabled:
        print("Bot trading PnL worker inactive (disabled).", flush=True)
        while not once:
            time.sleep(interval)
        return

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

    lock = PostgresAdvisoryLock(BOT_TRADING_PNL_WORKER_LOCK_KEY)
    if not lock.acquire():
        print("Another bot trading PnL worker is already running. Worker will exit.")
        return
    try:
        print("Bot trading PnL worker started. DB-only accounting.", flush=True)
        while True:
            started = time.monotonic()
            scope_count, rebuilt_count = run_cycle(SessionLocal)
            print(
                f"Bot PnL cycle completed. scope_count={scope_count} "
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
        print("Bot trading PnL worker stopped.", flush=True)
    except Exception as error:
        print(f"Bot trading PnL worker failed. error_type={type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
