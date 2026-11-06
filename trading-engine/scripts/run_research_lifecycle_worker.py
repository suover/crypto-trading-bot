import argparse
import json
import time


RESEARCH_LIFECYCLE_WORKER_LOCK_KEY = 2026110601


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate registered research candidates; optionally enroll Shadow."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--apply", action="store_true")
    namespace = parser.parse_args(args)
    if namespace.apply and not namespace.once:
        parser.error("--apply requires --once")
    return namespace


def report(result) -> dict:
    counts = result.counts
    return {
        "report_type": "RESEARCH_LIFECYCLE_CYCLE",
        "registered_candidate_count": result.registered_candidate_count,
        "processed_candidate_count": len(result.candidates),
        "eligible_for_review_count": sum(
            item.gate_status == "ELIGIBLE_FOR_REVIEW" for item in result.candidates
        ),
        "insufficient_data_count": counts.get("INSUFFICIENT_DATA", 0),
        "not_eligible_count": counts.get("NOT_ELIGIBLE", 0),
        "integrity_error_count": counts.get("INVALID_PROMOTION_DATA", 0)
        + counts.get("INVALID_SHADOW_ENROLLMENT", 0),
        "execution_error_count": counts.get("EXECUTION_ERROR", 0),
        "already_enrolled_count": counts.get("ALREADY_ENROLLED", 0),
        "created_enrollment_count": counts.get("CREATED", 0),
        "database_write": result.database_write,
        "live_policy_change": result.live_policy_change,
        "live_orders_created": result.live_orders_created,
        "external_calls": result.external_calls,
        "apply": result.apply,
        "cursor_candidate_id": result.cursor_candidate_id,
        "sweep_candidate_id_ceiling": result.sweep_candidate_id_ceiling,
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "status": item.status,
                "gate_status": item.gate_status,
                "enrollment_status": item.enrollment_status,
                "database_write": item.database_write,
                "error_type": item.error_type,
            }
            for item in result.candidates
        ],
    }


def run_worker(
    *,
    once: bool = False,
    apply: bool = False,
    settings=None,
    session_factory=None,
    lock_factory=None,
    lifecycle_factory=None,
    sleep_fn=None,
) -> int:
    from crypto_trading_bot.config.settings import get_settings

    settings = settings or get_settings()
    sleep_fn = sleep_fn or time.sleep
    if apply and (
        not once
        or not settings.research_lifecycle_enabled
        or not settings.research_auto_shadow_enrollment_enabled
    ):
        raise ValueError(
            "Research lifecycle Apply requires --once and both enable flags"
        )
    if not once and not settings.research_lifecycle_enabled:
        print("Research lifecycle worker inactive (disabled).", flush=True)
        while True:
            sleep_fn(settings.research_lifecycle_interval_seconds)

    if session_factory is None:
        from crypto_trading_bot.db.database import SessionLocal

        session_factory = SessionLocal
    if lock_factory is None:
        from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

        lock_factory = PostgresAdvisoryLock
    if lifecycle_factory is None:
        from crypto_trading_bot.services.research_lifecycle_service import (
            ResearchLifecycleService,
        )

        lifecycle_factory = ResearchLifecycleService

    lock = lock_factory(RESEARCH_LIFECYCLE_WORKER_LOCK_KEY)
    try:
        if not lock.acquire():
            print("Another research lifecycle worker is running. Worker will exit.")
            return 0
        lifecycle = lifecycle_factory(session_factory, settings=settings)
        apply_cycle = (
            apply if once else settings.research_auto_shadow_enrollment_enabled
        )
        while True:
            exit_code = 0
            try:
                result = lifecycle.run_cycle(apply=apply_cycle)
                output = report(result)
                output.update(
                    lifecycle_enabled=settings.research_lifecycle_enabled,
                    auto_shadow_enrollment_enabled=(
                        settings.research_auto_shadow_enrollment_enabled
                    ),
                    interval_seconds=settings.research_lifecycle_interval_seconds,
                    batch_size=settings.research_lifecycle_batch_size,
                )
                print(json.dumps(output, sort_keys=True), flush=True)
                if output["integrity_error_count"] or output["execution_error_count"]:
                    exit_code = 1
            except Exception as error:
                print(
                    "Research lifecycle cycle failed. "
                    f"error_type={type(error).__name__}",
                    flush=True,
                )
                exit_code = 1
            if once:
                return exit_code
            # Even failed cycles wait the full interval; no immediate retry loop.
            sleep_fn(settings.research_lifecycle_interval_seconds)
    finally:
        lock.release()


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        return run_worker(once=namespace.once, apply=namespace.apply)
    except KeyboardInterrupt:
        print("Research lifecycle worker stopped.", flush=True)
        return 0
    except Exception as error:
        print(f"Research lifecycle worker failed. error_type={type(error).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
