import argparse


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate research candidate outcomes. Default is a dry-run with public data."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--limit", type=int)
    namespace = parser.parse_args(args)
    for name in ("snapshot_id", "user_id", "limit"):
        value = getattr(namespace, name)
        if value is not None and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return namespace


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    lock = None
    try:
        from crypto_trading_bot.config.settings import get_settings
        from crypto_trading_bot.db.database import SessionLocal
        from crypto_trading_bot.services.research_candidate_outcome_service import (
            ResearchCandidateOutcomeService,
        )

        if namespace.apply:
            from crypto_trading_bot.db.postgres_advisory_lock import (
                PostgresAdvisoryLock,
            )
            from scripts.run_recommendation_outcome_worker import (
                RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY,
            )

            lock = PostgresAdvisoryLock(RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY)
            if not lock.acquire():
                print(
                    "Research candidate outcome rebuild rejected: worker lock is held."
                )
                return 1
        settings = get_settings()
        with SessionLocal() as session:
            result = ResearchCandidateOutcomeService(session).evaluate_due(
                horizons=settings.research_candidate_outcome_horizon_list,
                batch_size=(
                    namespace.limit
                    if namespace.limit is not None
                    else settings.research_candidate_outcome_batch_size
                ),
                user_id=namespace.user_id,
                snapshot_id=namespace.snapshot_id,
                apply=namespace.apply,
            )
            print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
            for name in (
                "candidate_count",
                "due_outcome_count",
                "complete_count",
                "partial_count",
                "new_count",
                "update_count",
                "skipped_complete_count",
                "skipped_non_retryable_count",
            ):
                print(f"{name}={getattr(result, name)}")
            for horizon, counts in result.by_horizon.items():
                print(
                    f"horizon_minutes={horizon} complete={counts['complete']} "
                    f"partial={counts['partial']}"
                )
            if namespace.apply:
                session.commit()
            else:
                session.rollback()
    except Exception as error:
        print(
            "Research candidate outcome rebuild failed. "
            f"error_type={type(error).__name__}"
        )
        return 1
    finally:
        if lock is not None:
            lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
