import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.services.research_lifecycle_service import (
    ResearchLifecycleCycleResult,
)
from scripts import run_research_lifecycle_worker as worker
from test_research_lifecycle_service import settings


def dependencies(*, acquired=True):
    lock = MagicMock()
    lock.acquire.return_value = acquired
    lock_factory = MagicMock(return_value=lock)
    lifecycle = MagicMock()
    lifecycle.run_cycle.return_value = ResearchLifecycleCycleResult(0, (), 0, 0, False)
    lifecycle_factory = MagicMock(return_value=lifecycle)
    return (
        lock,
        lifecycle,
        dict(
            session_factory=MagicMock(),
            lock_factory=lock_factory,
            lifecycle_factory=lifecycle_factory,
        ),
    )


def test_disabled_daemon_does_not_open_database_or_acquire_lock():
    _, _, kwargs = dependencies()
    sleep = MagicMock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(settings=settings(), sleep_fn=sleep, **kwargs)
    kwargs["lock_factory"].assert_not_called()
    kwargs["session_factory"].assert_not_called()
    kwargs["lifecycle_factory"].assert_not_called()
    sleep.assert_called_once_with(21600)


@pytest.mark.parametrize("enabled,auto", [(False, False), (True, False), (True, True)])
def test_once_preview_is_allowed_regardless_of_flags_and_never_applies(
    enabled, auto, capsys
):
    lock, lifecycle, kwargs = dependencies()
    assert (
        worker.run_worker(
            once=True,
            settings=settings(
                research_lifecycle_enabled=enabled,
                research_auto_shadow_enrollment_enabled=auto,
            ),
            **kwargs,
        )
        == 0
    )
    lifecycle.run_cycle.assert_called_once_with(apply=False)
    lock.release.assert_called_once()
    output = json.loads(capsys.readouterr().out)
    assert not output["database_write"]
    assert not output["live_policy_change"]
    assert not output["live_orders_created"]
    assert output["lifecycle_enabled"] == enabled
    assert output["auto_shadow_enrollment_enabled"] == auto


@pytest.mark.parametrize("enabled,auto", [(False, False), (True, False), (False, True)])
def test_once_apply_rejected_before_lock_without_both_flags(enabled, auto):
    lock, _, kwargs = dependencies()
    with pytest.raises(ValueError, match="both enable flags"):
        worker.run_worker(
            once=True,
            apply=True,
            settings=settings(
                research_lifecycle_enabled=enabled,
                research_auto_shadow_enrollment_enabled=auto,
            ),
            **kwargs,
        )
    lock.acquire.assert_not_called()


def test_explicit_once_apply_uses_apply():
    _, lifecycle, kwargs = dependencies()
    worker.run_worker(
        once=True,
        apply=True,
        settings=settings(
            research_lifecycle_enabled=True,
            research_auto_shadow_enrollment_enabled=True,
        ),
        **kwargs,
    )
    lifecycle.run_cycle.assert_called_once_with(apply=True)


def test_lock_loser_exits_before_processing():
    lock, lifecycle, kwargs = dependencies(acquired=False)
    assert worker.run_worker(once=True, settings=settings(), **kwargs) == 0
    lifecycle.run_cycle.assert_not_called()
    lock.release.assert_called_once()


def test_failed_cycle_redacts_error_and_releases_lock(capsys):
    lock, lifecycle, kwargs = dependencies()
    lifecycle.run_cycle.side_effect = RuntimeError("fixture-only-secret")
    assert worker.run_worker(once=True, settings=settings(), **kwargs) == 1
    output = capsys.readouterr().out
    assert "error_type=RuntimeError" in output
    assert "fixture-only-secret" not in output
    lock.release.assert_called_once()


@pytest.mark.parametrize("auto", [False, True])
def test_daemon_waits_full_interval_after_failure_and_cleans_up(auto):
    lock, lifecycle, kwargs = dependencies()
    lifecycle.run_cycle.side_effect = RuntimeError("cycle failure")
    sleep = MagicMock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(
            settings=settings(
                research_lifecycle_enabled=True,
                research_auto_shadow_enrollment_enabled=auto,
            ),
            sleep_fn=sleep,
            **kwargs,
        )
    lifecycle.run_cycle.assert_called_once_with(apply=auto)
    sleep.assert_called_once_with(21600)
    lock.release.assert_called_once()


def test_cli_apply_requires_once_and_lock_key_is_unique():
    assert vars(worker.parse_arguments(["--once"])) == {"once": True, "apply": False}
    assert worker.parse_arguments(["--once", "--apply"]).apply
    with pytest.raises(SystemExit):
        worker.parse_arguments(["--apply"])
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for source in scripts.glob("*.py"):
        if source.name != "run_research_lifecycle_worker.py":
            assert str(
                worker.RESEARCH_LIFECYCLE_WORKER_LOCK_KEY
            ) not in source.read_text(encoding="utf-8")
