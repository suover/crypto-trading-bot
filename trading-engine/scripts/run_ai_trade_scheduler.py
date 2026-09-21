import argparse
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.postgres_advisory_lock import (
    PostgresAdvisoryLock,
)
from scripts.run_ai_trade_analysis import (
    PipelineStepError,
    run_ai_trade_analysis,
)


KST = ZoneInfo("Asia/Seoul")

AI_TRADE_SCHEDULER_LOCK_KEY = 2026070501
SCHEDULE_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class AnalysisScheduleTime:
    hour: int
    minute: int

    @property
    def label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def job_id(self) -> str:
        return f"ai-trade-analysis-{self.hour:02d}{self.minute:02d}"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run AI trade analysis pipeline at configured daily times. "
            "This scheduler creates Telegram approval requests. "
            "Actual order execution still requires Telegram approval."
        )
    )

    parser.add_argument(
        "--schedule-times",
        type=str,
        default=None,
        help=(
            "Comma-separated daily run times in HH:MM format. "
            "Example: 09:00,15:00,21:00. "
            "If omitted, AI_ANALYSIS_SCHEDULE_TIMES is used."
        ),
    )

    parser.add_argument(
        "--run-on-startup",
        action="store_true",
        help="Run AI analysis once immediately when scheduler starts.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one AI analysis cycle immediately and exit.",
    )

    return parser.parse_args()


def parse_schedule_times(schedule_times: str) -> tuple[AnalysisScheduleTime, ...]:
    values = [value.strip() for value in schedule_times.split(",") if value.strip()]

    if not values:
        raise ValueError("ai_analysis_schedule_times must not be empty")

    parsed_times: list[AnalysisScheduleTime] = []
    seen_labels: set[str] = set()

    for value in values:
        if SCHEDULE_TIME_PATTERN.fullmatch(value) is None:
            raise ValueError(
                f"Analysis schedule time must use HH:MM format. value={value}"
            )

        hour_text, minute_text = value.split(":")
        hour = int(hour_text)
        minute = int(minute_text)

        if not (0 <= hour <= 23):
            raise ValueError(
                f"Analysis schedule hour must be between 00 and 23. value={value}"
            )

        if not (0 <= minute <= 59):
            raise ValueError(
                f"Analysis schedule minute must be between 00 and 59. value={value}"
            )

        schedule_time = AnalysisScheduleTime(
            hour=hour,
            minute=minute,
        )

        if schedule_time.label in seen_labels:
            continue

        seen_labels.add(schedule_time.label)
        parsed_times.append(schedule_time)

    return tuple(parsed_times)


def run_analysis_job() -> None:
    print("AI trade scheduled analysis started.", flush=True)

    try:
        run_ai_trade_analysis()

    except PipelineStepError as error:
        print(
            "AI trade scheduled analysis failed. "
            f"failed_step={error.step.name}, "
            f"module={error.step.module}, "
            f"return_code={error.return_code}",
            flush=True,
        )

        raise

    except Exception as error:
        print(
            "AI trade scheduled analysis failed. "
            f"error_type={type(error).__name__}, "
            f"error={error}",
            flush=True,
        )

        raise

    print("AI trade scheduled analysis completed.", flush=True)


def build_scheduler(
    schedule_times: tuple[AnalysisScheduleTime, ...],
) -> BlockingScheduler:
    scheduler = BlockingScheduler(
        timezone=KST,
    )

    for schedule_time in schedule_times:
        scheduler.add_job(
            run_analysis_job,
            trigger=CronTrigger(
                hour=schedule_time.hour,
                minute=schedule_time.minute,
                timezone=KST,
            ),
            id=schedule_time.job_id,
            name=f"AI trade analysis {schedule_time.label}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    return scheduler


def run_scheduler(
    schedule_times: tuple[AnalysisScheduleTime, ...],
    run_on_startup: bool,
    once: bool,
) -> None:
    settings = get_settings()

    if not settings.ai_analysis_scheduler_enabled:
        print("AI analysis scheduler is disabled. Scheduler will exit.", flush=True)
        return

    scheduler_lock = PostgresAdvisoryLock(
        lock_key=AI_TRADE_SCHEDULER_LOCK_KEY,
    )

    if not scheduler_lock.acquire():
        print("Another AI trade scheduler is already running. Scheduler will exit.")
        return

    print("AI trade scheduler lock acquired.", flush=True)

    try:
        print("AI trade scheduler started.", flush=True)
        print(
            "schedule_times="
            f"{','.join(schedule_time.label for schedule_time in schedule_times)}",
            flush=True,
        )
        print(f"run_on_startup={run_on_startup}", flush=True)
        print(f"once={once}", flush=True)
        print("Actual order execution still requires Telegram approval.", flush=True)

        if once:
            run_analysis_job()
            return

        if run_on_startup:
            run_analysis_job()

        scheduler = build_scheduler(
            schedule_times=schedule_times,
        )

        scheduler.start()

    except KeyboardInterrupt:
        print("AI trade scheduler stopped.", flush=True)

    finally:
        scheduler_lock.release()
        print("AI trade scheduler lock released.", flush=True)


if __name__ == "__main__":
    arguments = parse_arguments()
    settings = get_settings()

    resolved_schedule_times = parse_schedule_times(
        arguments.schedule_times
        if arguments.schedule_times is not None
        else settings.ai_analysis_schedule_times
    )

    resolved_run_on_startup = (
        arguments.run_on_startup or settings.ai_analysis_run_on_startup
    )

    run_scheduler(
        schedule_times=resolved_schedule_times,
        run_on_startup=resolved_run_on_startup,
        once=arguments.once,
    )
