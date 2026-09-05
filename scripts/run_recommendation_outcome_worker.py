import argparse
import time


RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY = 2026090501


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recommendation outcome evaluation."
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(args)


def run_cycle(
    session_factory, *, horizons: tuple[int, ...], batch_size: int, price_resolver=None
):
    from crypto_trading_bot.services.recommendation_outcome_service import (
        RecommendationOutcomeService,
    )

    with session_factory() as session:
        try:
            result = RecommendationOutcomeService(
                session, price_resolver=price_resolver
            ).evaluate_due(
                horizons=horizons,
                batch_size=batch_size,
                apply=True,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def run_research_cycle(
    session_factory, *, horizons: tuple[int, ...], batch_size: int, price_resolver=None
):
    from crypto_trading_bot.services.research_candidate_outcome_service import (
        ResearchCandidateOutcomeService,
    )

    with session_factory() as session:
        try:
            result = ResearchCandidateOutcomeService(
                session, price_resolver=price_resolver
            ).evaluate_due(
                horizons=horizons,
                batch_size=batch_size,
                apply=True,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    recommendation_enabled = settings.recommendation_outcome_enabled
    research_enabled = settings.research_candidate_outcome_enabled
    intervals = (
        settings.recommendation_outcome_interval_seconds,
        settings.research_candidate_outcome_interval_seconds,
    )
    if not recommendation_enabled and not research_enabled:
        print(
            "Recommendation outcome worker inactive (all analytics disabled).",
            flush=True,
        )
        while not once:
            time.sleep(min(intervals))
        return

    from crypto_trading_bot.db.database import SessionLocal
    from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

    lock = PostgresAdvisoryLock(RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY)
    if not lock.acquire():
        print("Another recommendation outcome worker is running. Worker will exit.")
        return
    try:
        print(
            "Recommendation outcome worker started. Public market data only. "
            f"recommendation_enabled={recommendation_enabled} "
            f"research_enabled={research_enabled}"
        )
        recommendation_next_due = time.monotonic()
        research_next_due = time.monotonic()
        while True:
            now = time.monotonic()
            recommendation_due = recommendation_enabled and (
                once or now >= recommendation_next_due
            )
            research_due = research_enabled and (once or now >= research_next_due)
            price_resolver = None
            if recommendation_due or research_due:
                from crypto_trading_bot.services.historical_outcome_price_resolver import (
                    HistoricalOutcomePriceResolver,
                )

                price_resolver = HistoricalOutcomePriceResolver()
            if recommendation_due:
                try:
                    result = run_cycle(
                        SessionLocal,
                        horizons=settings.recommendation_outcome_horizon_list,
                        batch_size=settings.recommendation_outcome_batch_size,
                        price_resolver=price_resolver,
                    )
                    print(
                        "Recommendation outcome cycle completed. "
                        f"recommendation_count={result.recommendation_count} "
                        f"due_outcome_count={result.due_outcome_count}",
                        flush=True,
                    )
                except Exception as error:
                    print(
                        "Recommendation outcome cycle failed. "
                        f"error_type={type(error).__name__}",
                        flush=True,
                    )
                recommendation_next_due = (
                    time.monotonic() + settings.recommendation_outcome_interval_seconds
                )
            if research_due:
                try:
                    research = run_research_cycle(
                        SessionLocal,
                        horizons=settings.research_candidate_outcome_horizon_list,
                        batch_size=settings.research_candidate_outcome_batch_size,
                        price_resolver=price_resolver,
                    )
                    print(
                        "Research candidate outcome cycle completed. "
                        f"candidate_count={research.candidate_count} "
                        f"due_outcome_count={research.due_outcome_count}",
                        flush=True,
                    )
                except Exception as error:
                    print(
                        "Research candidate outcome cycle failed. "
                        f"error_type={type(error).__name__}",
                        flush=True,
                    )
                research_next_due = (
                    time.monotonic()
                    + settings.research_candidate_outcome_interval_seconds
                )
            if once:
                return
            next_due = min(
                due
                for enabled, due in (
                    (recommendation_enabled, recommendation_next_due),
                    (research_enabled, research_next_due),
                )
                if enabled
            )
            time.sleep(max(0, next_due - time.monotonic()))
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
