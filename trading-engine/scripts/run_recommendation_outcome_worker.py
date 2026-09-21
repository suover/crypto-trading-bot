import argparse
from dataclasses import dataclass
import time


RECOMMENDATION_OUTCOME_WORKER_LOCK_KEY = 2026090501


@dataclass(frozen=True)
class ShadowSelectionAutomaticCycleResult:
    enrollment_count: int
    processed_candidate_count: int
    success_candidate_count: int
    no_post_snapshot_candidate_count: int
    no_new_evaluation_candidate_count: int
    no_shadow_enrollment_candidate_count: int
    invalid_candidate_count: int
    exception_candidate_count: int
    created_evaluation_count: int
    success_evaluation_count: int
    context_mismatch_count: int
    baseline_integrity_failed_count: int
    replay_incompatible_count: int


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


def run_shadow_selection_cycle(session_factory, *, evaluation_service_factory=None):
    from sqlalchemy import select

    from crypto_trading_bot.db.models import ShadowPolicyEnrollment
    from crypto_trading_bot.services.shadow_policy_evaluation_service import (
        BASELINE_INTEGRITY_FAILED,
        CONTEXT_MISMATCH,
        INVALID_SHADOW_EVALUATION,
        NO_NEW_SHADOW_EVALUATIONS,
        NO_POST_ENROLLMENT_SNAPSHOTS,
        NO_SHADOW_ENROLLMENT,
        REPLAY_INCOMPATIBLE,
        SUCCESS,
        ShadowPolicyEvaluationService,
    )

    evaluation_service_factory = (
        evaluation_service_factory or ShadowPolicyEvaluationService
    )

    with session_factory() as enumeration_session:
        candidate_ids = tuple(
            enumeration_session.scalars(
                select(ShadowPolicyEnrollment.candidate_id).order_by(
                    ShadowPolicyEnrollment.id
                )
            )
        )
        enumeration_session.rollback()

    counts = {
        "success_candidate_count": 0,
        "no_post_snapshot_candidate_count": 0,
        "no_new_evaluation_candidate_count": 0,
        "no_shadow_enrollment_candidate_count": 0,
        "invalid_candidate_count": 0,
        "exception_candidate_count": 0,
        "created_evaluation_count": 0,
        "success_evaluation_count": 0,
        "context_mismatch_count": 0,
        "baseline_integrity_failed_count": 0,
        "replay_incompatible_count": 0,
    }
    no_write_status_counts = {
        NO_POST_ENROLLMENT_SNAPSHOTS: "no_post_snapshot_candidate_count",
        NO_NEW_SHADOW_EVALUATIONS: "no_new_evaluation_candidate_count",
        NO_SHADOW_ENROLLMENT: "no_shadow_enrollment_candidate_count",
    }
    evaluation_status_counts = {
        SUCCESS: "success_evaluation_count",
        CONTEXT_MISMATCH: "context_mismatch_count",
        BASELINE_INTEGRITY_FAILED: "baseline_integrity_failed_count",
        REPLAY_INCOMPATIBLE: "replay_incompatible_count",
    }

    for candidate_id in candidate_ids:
        with session_factory() as session:
            try:
                result = evaluation_service_factory(session).evaluate(
                    candidate_id=candidate_id
                )
                if result.status == SUCCESS:
                    if not result.database_write or result.created_evaluation_count < 1:
                        raise RuntimeError("SUCCESS result has no database write")
                    created = result.evaluations[-result.created_evaluation_count :]
                    if len(created) != result.created_evaluation_count:
                        raise RuntimeError("created evaluation summary is inconsistent")
                    created_status_counts = {
                        key: 0 for key in evaluation_status_counts.values()
                    }
                    for evaluation in created:
                        try:
                            key = evaluation_status_counts[evaluation.evaluation_status]
                        except KeyError:
                            raise RuntimeError(
                                "created evaluation status is unsupported"
                            ) from None
                        created_status_counts[key] += 1
                    session.commit()
                    counts["success_candidate_count"] += 1
                    counts["created_evaluation_count"] += len(created)
                    for key, value in created_status_counts.items():
                        counts[key] += value
                elif result.status == INVALID_SHADOW_EVALUATION:
                    if result.database_write or result.created_evaluation_count != 0:
                        raise RuntimeError("invalid result reports a database write")
                    session.rollback()
                    counts["invalid_candidate_count"] += 1
                    print(
                        "Shadow selection candidate invalid. "
                        f"candidate_id={candidate_id} status={result.status} "
                        f"safe_reason={result.safe_reason}",
                        flush=True,
                    )
                elif result.status in no_write_status_counts:
                    if result.database_write or result.created_evaluation_count != 0:
                        raise RuntimeError("no-op result reports a database write")
                    session.rollback()
                    counts[no_write_status_counts[result.status]] += 1
                    if result.status == NO_SHADOW_ENROLLMENT:
                        print(
                            "Shadow selection enrollment disappeared after enumeration. "
                            f"candidate_id={candidate_id}",
                            flush=True,
                        )
                else:
                    raise RuntimeError("Shadow selection result status is unsupported")
            except Exception as error:
                session.rollback()
                counts["exception_candidate_count"] += 1
                print(
                    "Shadow selection candidate failed. "
                    f"candidate_id={candidate_id} "
                    f"error_type={type(error).__name__}",
                    flush=True,
                )

    return ShadowSelectionAutomaticCycleResult(
        enrollment_count=len(candidate_ids),
        processed_candidate_count=len(candidate_ids),
        **counts,
    )


def run_worker(*, once: bool = False) -> None:
    from crypto_trading_bot.config.settings import get_settings

    settings = get_settings()
    recommendation_enabled = settings.recommendation_outcome_enabled
    research_enabled = settings.research_candidate_outcome_enabled
    shadow_enabled = settings.shadow_selection_evaluation_enabled
    intervals = (
        settings.recommendation_outcome_interval_seconds,
        settings.research_candidate_outcome_interval_seconds,
        settings.shadow_selection_evaluation_interval_seconds,
    )
    if not recommendation_enabled and not research_enabled and not shadow_enabled:
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
            "Analytics worker started. Public market data or DB-only cycles. "
            f"recommendation_enabled={recommendation_enabled} "
            f"research_enabled={research_enabled} "
            f"shadow_selection_enabled={shadow_enabled}"
        )
        recommendation_next_due = time.monotonic()
        research_next_due = time.monotonic()
        shadow_next_due = time.monotonic()
        while True:
            now = time.monotonic()
            recommendation_due = recommendation_enabled and (
                once or now >= recommendation_next_due
            )
            research_due = research_enabled and (once or now >= research_next_due)
            shadow_due = shadow_enabled and (once or now >= shadow_next_due)
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
            if shadow_due:
                try:
                    shadow = run_shadow_selection_cycle(SessionLocal)
                    print(
                        "Shadow selection evaluation cycle completed. "
                        f"enrollment_count={shadow.enrollment_count} "
                        f"processed_candidate_count={shadow.processed_candidate_count} "
                        f"created_evaluation_count={shadow.created_evaluation_count} "
                        f"invalid_candidate_count={shadow.invalid_candidate_count} "
                        f"exception_candidate_count={shadow.exception_candidate_count}",
                        flush=True,
                    )
                except Exception as error:
                    print(
                        "Shadow selection evaluation cycle failed. "
                        f"error_type={type(error).__name__}",
                        flush=True,
                    )
                shadow_next_due = (
                    time.monotonic()
                    + settings.shadow_selection_evaluation_interval_seconds
                )
            if once:
                return
            next_due = min(
                due
                for enabled, due in (
                    (recommendation_enabled, recommendation_next_due),
                    (research_enabled, research_next_due),
                    (shadow_enabled, shadow_next_due),
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
