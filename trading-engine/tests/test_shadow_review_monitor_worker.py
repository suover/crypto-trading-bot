import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from crypto_trading_bot.config.settings import Settings
from scripts import run_shadow_review_monitor_worker as worker
from scripts.run_research_candidate_automation_worker import (
    RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY,
)
from scripts.run_research_lifecycle_worker import RESEARCH_LIFECYCLE_WORKER_LOCK_KEY


def config(**kwargs):
    return Settings(
        _env_file=None, database_url="postgresql://test:test@localhost/test", **kwargs
    )


def test_default_flags_lock_key_and_disabled_worker(capsys):
    settings = config()
    assert not settings.shadow_review_monitor_enabled
    assert not settings.shadow_review_telegram_notifications_enabled
    assert settings.shadow_review_monitor_interval_seconds == 21600
    assert settings.shadow_review_monitor_batch_size == 10
    assert settings.shadow_review_notification_max_per_cycle == 5
    assert settings.shadow_review_retry_delays == (1, 5, 15)
    assert worker.SHADOW_REVIEW_MONITOR_LOCK_KEY not in {
        RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY,
        RESEARCH_LIFECYCLE_WORKER_LOCK_KEY,
    }
    session, lock, telegram = MagicMock(), MagicMock(), MagicMock()
    sleep = MagicMock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(
            settings=settings,
            session_factory=session,
            lock_factory=lock,
            telegram_factory=telegram,
            sleep_fn=sleep,
        )
    assert (
        worker.run_worker(
            once=True,
            apply=True,
            settings=settings,
            session_factory=session,
            lock_factory=lock,
            telegram_factory=telegram,
        )
        == 1
    )
    for mock in (session, lock, telegram):
        mock.assert_not_called()
    sleep.assert_called_once_with(21600)
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [report["status"] for report in reports] == ["DISABLED", "APPLY_DISABLED"]
    assert all(
        not report["database_write"] and not report["external_calls"]
        for report in reports
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"shadow_review_monitor_interval_seconds": 0},
        {"shadow_review_monitor_batch_size": 0},
        {"shadow_review_notification_max_per_cycle": 0},
        {"shadow_review_notification_max_retries": -1},
        {"shadow_review_notification_retry_delays_minutes": "1,,5,15"},
        {"shadow_review_notification_retry_delays_minutes": "1,5"},
        {"shadow_review_notification_retry_delays_minutes": "0,5,15"},
        {"shadow_review_monitor_enabled": True, "telegram_chat_id": "@invalid"},
        {"shadow_review_monitor_enabled": True, "telegram_chat_id": "0"},
        {
            "shadow_review_telegram_notifications_enabled": True,
            "telegram_chat_id": "-10001",
        },
        {
            "shadow_review_telegram_notifications_enabled": True,
            "telegram_bot_token": "123:" + "a" * 30,
        },
    ],
)
def test_invalid_monitor_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        config(**overrides)


def test_config_can_monitor_without_delivery_and_normalizes_destination():
    settings = config(shadow_review_monitor_enabled=True, telegram_chat_id="-001001")
    assert settings.shadow_review_destination == "-1001"
    assert not settings.shadow_review_telegram_notifications_enabled
