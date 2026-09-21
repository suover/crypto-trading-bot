import argparse


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate recommendation outcomes. Default is a read-only dry-run."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--limit", type=int, default=50)
    namespace = parser.parse_args(args)
    if namespace.user_id is not None and namespace.user_id <= 0:
        parser.error("--user-id must be positive")
    if namespace.limit <= 0:
        parser.error("--limit must be positive")
    return namespace


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.config.settings import get_settings
        from crypto_trading_bot.db.database import SessionLocal
        from crypto_trading_bot.services.recommendation_outcome_service import (
            RecommendationOutcomeService,
        )

        settings = get_settings()
        with SessionLocal() as session:
            result = RecommendationOutcomeService(session).evaluate_due(
                horizons=settings.recommendation_outcome_horizon_list,
                batch_size=namespace.limit,
                user_id=namespace.user_id,
                apply=namespace.apply,
            )
            print(f"mode={'APPLY' if namespace.apply else 'DRY_RUN'}")
            for name in (
                "recommendation_count",
                "due_outcome_count",
                "selected_complete_count",
                "selected_partial_count",
                "selected_new_count",
                "selected_update_count",
                "candidate_complete_count",
                "candidate_partial_count",
                "candidate_new_count",
                "candidate_update_count",
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
            f"Recommendation outcome rebuild failed. error_type={type(error).__name__}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
