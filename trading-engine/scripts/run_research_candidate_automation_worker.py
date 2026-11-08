"""Periodic DB-only research candidate automation."""

import argparse
from dataclasses import asdict
import json
import time


RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY = 2026110801


def run_worker(
    *,
    once=False,
    apply=False,
    settings=None,
    session_factory=None,
    lock_factory=None,
    sleep_fn=time.sleep,
) -> int:
    from crypto_trading_bot.config.settings import get_settings
    from crypto_trading_bot.services.research_candidate_automation_service import (
        ERRORS,
        ResearchCandidateAutomationResult,
        ResearchCandidateAutomationService,
    )

    settings = settings or get_settings()
    enabled = settings.research_candidate_automation_enabled
    register = settings.research_candidate_auto_registration_enabled
    interval = settings.research_candidate_automation_interval_seconds
    apply_cycle = apply if once else enabled and register

    def report_status(status, reason=None):
        result = ResearchCandidateAutomationResult.from_settings(
            settings, apply=apply_cycle
        )
        result.status = status
        result.safe_reason = reason
        print(json.dumps(asdict(result), default=str, sort_keys=True), flush=True)

    if apply and (not once or not enabled or not register):
        report_status("APPLY_DISABLED", "--once --apply requires both enable flags")
        return 1
    if not once and not enabled:
        report_status("DISABLED")
        while True:
            sleep_fn(interval)

    if session_factory is None:
        from crypto_trading_bot.db.database import SessionLocal

        session_factory = SessionLocal
    if lock_factory is None:
        from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock

        lock_factory = PostgresAdvisoryLock

    lock = lock_factory(RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY)
    service = ResearchCandidateAutomationService(session_factory, settings=settings)
    while True:
        code = 0
        try:
            if not lock.acquire():
                report_status("LOCK_NOT_ACQUIRED")
                return 0
            result = service.run_cycle(apply=apply_cycle, scheduled=not once)
            print(json.dumps(asdict(result), default=str, sort_keys=True), flush=True)
            code = int(result.status in ERRORS)
        except Exception as error:
            report_status("EXECUTION_ERROR", type(error).__name__)
            code = 1
        finally:
            lock.release()
        if once:
            return code
        sleep_fn(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run one read-only preview")
    parser.add_argument(
        "--apply", action="store_true", help="register with both enable flags"
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
                    "report_type": "RESEARCH_CANDIDATE_AUTOMATION_CYCLE",
                    "status": "EXECUTION_ERROR",
                    "safe_reason": type(error).__name__,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
