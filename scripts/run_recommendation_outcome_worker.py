import argparse
import time


RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY = 2026090501


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recommendation outcome evaluation."
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_cycle(session_factory, *, horizons: tuple[int, ...], batch_size: int):
    from crypto_trading_bot.services.recommendation_outcome_service import (
        RecommendationOutcomeService,
    )

    with session_factory() as session:
        result = RecommendationOutcomeService(session).evaluate_due(
            horizons=horizons,
            batch_size=batch_size,
            apply=True,
        )
        session.commit()
        return result


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    interval = settings.recommendation_outcome_interval_seconds
    if not settings.recommendation_outcome_enabled:
        print("Recommendation outcome worker inactive (disabled).", flush=True)
        while not once:
            time.sleep(interval)
        return

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

    lock = PostgresAdvisoryLock(RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY)
    if not lock.acquire():
        print("Another recommendation outcome worker is running. Worker will exit.")
        return
    try:
        print("Recommendation outcome worker started. Public market data only.")
        while True:
            started = time.monotonic()
            result = run_cycle(
                SessionLocal,
                horizons=settings.recommendation_outcome_horizon_list,
                batch_size=settings.recommendation_outcome_batch_size,
            )
            print(
                "Recommendation outcome cycle completed. "
                f"recommendation_count={result.recommendation_count} "
                f"due_outcome_count={result.due_outcome_count}",
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
        print("Recommendation outcome worker stopped.")
    except Exception as error:
        print(
            f"Recommendation outcome worker failed. error_type={type(error).__name__}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
