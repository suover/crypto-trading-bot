from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.research_candidate_automation_service import (
    ResearchCandidateAutomationService,
)
from scripts import run_research_candidate_automation_worker as worker
from scripts.run_research_lifecycle_worker import RESEARCH_LIFECYCLE_WORKER_LOCK_KEY


def settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        **overrides,
    )


def test_safe_defaults_and_distinct_lock():
    config = settings()
    assert not config.research_candidate_automation_enabled
    assert not config.research_candidate_auto_registration_enabled
    assert config.research_candidate_automation_interval_seconds == 86400
    assert config.research_candidate_min_new_snapshots == 21
    assert config.research_candidate_generation_step == Decimal("0.05")
    assert config.research_candidate_max_registrations_per_cycle == 5
    assert config.research_candidate_max_registered_per_context == 30
    assert (
        worker.RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY
        != RESEARCH_LIFECYCLE_WORKER_LOCK_KEY
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation_step", "Infinity"),
        ("generation_step", "NaN"),
        ("generation_step", "0"),
        ("generation_step", "0.101"),
        ("automation_interval_seconds", 59),
        ("automation_interval_seconds", 604801),
        ("min_new_snapshots", 0),
        ("max_registrations_per_cycle", -1),
        ("max_registered_per_context", 0),
    ],
)
def test_invalid_settings(field, value):
    with pytest.raises(ValidationError):
        settings(**{f"research_candidate_{field}": value})


def test_disabled_worker_and_blocked_apply_never_connect(capsys):
    session = MagicMock()
    lock = MagicMock()
    sleep = MagicMock(side_effect=KeyboardInterrupt)
    config = settings()
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(
            settings=config, session_factory=session, lock_factory=lock, sleep_fn=sleep
        )
    sleep.assert_called_once_with(86400)
    assert (
        worker.run_worker(
            once=True,
            apply=True,
            settings=config,
            session_factory=session,
            lock_factory=lock,
        )
        == 1
    )
    assert (
        ResearchCandidateAutomationService(session, settings=config)
        .run_cycle(scheduled=True)
        .status
        == "DISABLED"
    )
    lock.assert_not_called()
    session.assert_not_called()
    assert "APPLY_DISABLED" in capsys.readouterr().out


def test_periodic_failure_releases_lock_and_waits_interval(capsys):
    lock = MagicMock()
    lock.acquire.return_value = True
    session = MagicMock(side_effect=RuntimeError("private-value"))
    config = settings(research_candidate_automation_enabled=True)

    def sleep(seconds):
        assert seconds == 86400
        lock.release.assert_called_once()
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(
            settings=config,
            session_factory=session,
            lock_factory=lambda _: lock,
            sleep_fn=sleep,
        )
    session.assert_called_once()
    output = capsys.readouterr().out
    assert "EXECUTION_ERROR" in output and "private-value" not in output
