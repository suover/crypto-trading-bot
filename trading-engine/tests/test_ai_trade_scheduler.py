import pytest

from crypto_trading_bot.config.settings import get_settings
from scripts.run_ai_trade_scheduler import (
    parse_schedule_times,
)


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_parse_schedule_times_accepts_single_time() -> None:
    schedule_times = parse_schedule_times("09:00")

    assert len(schedule_times) == 1
    assert schedule_times[0].hour == 9
    assert schedule_times[0].minute == 0
    assert schedule_times[0].label == "09:00"
    assert schedule_times[0].job_id == "ai-trade-analysis-0900"


def test_parse_schedule_times_accepts_multiple_times() -> None:
    schedule_times = parse_schedule_times("09:00,15:00,21:30")

    assert [schedule_time.label for schedule_time in schedule_times] == [
        "09:00",
        "15:00",
        "21:30",
    ]


def test_parse_schedule_times_removes_duplicates() -> None:
    schedule_times = parse_schedule_times("09:00,09:00,15:00")

    assert [schedule_time.label for schedule_time in schedule_times] == [
        "09:00",
        "15:00",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "9:00",
        "09",
        "24:00",
        "09:60",
        "invalid",
    ],
)
def test_parse_schedule_times_rejects_invalid_value(value: str) -> None:
    with pytest.raises(ValueError):
        parse_schedule_times(value)


def test_scheduler_settings_are_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("AI_ANALYSIS_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("AI_ANALYSIS_SCHEDULE_TIMES", "09:00,15:00")
    monkeypatch.setenv("AI_ANALYSIS_RUN_ON_STARTUP", "true")

    clear_settings_cache()

    settings = get_settings()

    assert settings.ai_analysis_scheduler_enabled is False
    assert settings.ai_analysis_schedule_times == "09:00,15:00"
    assert settings.ai_analysis_run_on_startup is True


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
