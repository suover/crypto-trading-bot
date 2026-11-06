from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.research_lifecycle_service import (
    ResearchLifecycleService,
)
from scripts.run_research_lifecycle_worker import report


def settings(**changes):
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        **changes,
    )


def enrollment_result(
    candidate_id=1, status="DRY_RUN", gate_status="ELIGIBLE_FOR_REVIEW"
):
    return SimpleNamespace(
        candidate_id=candidate_id,
        enrollment_status=status,
        gate=(SimpleNamespace(status=gate_status) if gate_status else None),
        enrollment=object() if status in ("CREATED", "ALREADY_ENROLLED") else None,
        database_write=status == "CREATED",
        shadow_enrollment_created=status == "CREATED",
        external_calls=False,
        live_policy_change=False,
        promotion_performed=False,
    )


def service_for(candidate_ids=(1,), *, apply_enabled=False, batch_size=10):
    sessions = []

    def factory():
        session = MagicMock()
        session.__enter__.return_value = session
        if not sessions:
            session.scalar.side_effect = [
                len(candidate_ids),
                max(candidate_ids, default=0),
            ]
            session.scalars.return_value = candidate_ids
        else:
            session.scalar.return_value = None
        sessions.append(session)
        return session

    gates = MagicMock()
    gates.return_value.evaluate.side_effect = lambda candidate_id: SimpleNamespace(
        candidate_id=candidate_id, status="ELIGIBLE_FOR_REVIEW"
    )
    enrollments = MagicMock()
    enrollments.return_value.preview.side_effect = lambda candidate_id: (
        enrollment_result(candidate_id)
    )
    enrollments.return_value.enroll.side_effect = lambda candidate_id: (
        enrollment_result(candidate_id, "CREATED")
    )
    service = ResearchLifecycleService(
        factory,
        settings=settings(
            research_lifecycle_enabled=apply_enabled,
            research_auto_shadow_enrollment_enabled=apply_enabled,
            research_lifecycle_batch_size=batch_size,
        ),
        gate_service_factory=gates,
        enrollment_service_factory=enrollments,
    )
    return service, sessions, gates, enrollments


@pytest.mark.parametrize(
    "status", ["INSUFFICIENT_DATA", "NOT_ELIGIBLE", "INVALID_PROMOTION_DATA"]
)
def test_blocked_gate_never_attempts_enrollment_or_commits(status):
    service, sessions, gates, enrollments = service_for(apply_enabled=True)
    gates.return_value.evaluate.side_effect = None
    gates.return_value.evaluate.return_value = SimpleNamespace(
        candidate_id=1, status=status
    )

    result = service.run_cycle(apply=True)

    assert result.candidates[0].status == status
    assert not result.database_write
    enrollments.return_value.preview.assert_not_called()
    enrollments.return_value.enroll.assert_not_called()
    for session in sessions:
        session.commit.assert_not_called()
        session.rollback.assert_called_once()
        session.__exit__.assert_called_once()


def test_eligible_preview_and_apply_use_existing_service_and_separate_transactions():
    service, sessions, _, enrollments = service_for((1, 2), apply_enabled=True)
    result = service.run_cycle(apply=True)
    assert result.counts == {"CREATED": 2}
    assert result.database_write
    assert len(sessions) == 3
    assert enrollments.return_value.enroll.call_count == 2
    for session in sessions[1:]:
        session.commit.assert_called_once()
        session.rollback.assert_not_called()
        session.__exit__.assert_called_once()

    service, sessions, _, enrollments = service_for(apply_enabled=True)
    result = service.run_cycle()
    assert result.counts == {"DRY_RUN": 1}
    enrollments.return_value.preview.assert_called_once_with(candidate_id=1)
    enrollments.return_value.enroll.assert_not_called()
    sessions[-1].commit.assert_not_called()
    assert not result.database_write


@pytest.mark.parametrize("enabled,auto", [(False, False), (True, False), (False, True)])
def test_apply_requires_both_flags_before_opening_database(enabled, auto):
    factory = MagicMock()
    service = ResearchLifecycleService(
        factory,
        settings=settings(
            research_lifecycle_enabled=enabled,
            research_auto_shadow_enrollment_enabled=auto,
        ),
    )
    with pytest.raises(ValueError, match="both enable flags"):
        service.run_cycle(apply=True)
    factory.assert_not_called()


@pytest.mark.parametrize("status", ["ALREADY_ENROLLED", "INVALID_SHADOW_ENROLLMENT"])
def test_existing_enrollment_is_validated_without_gate_or_commit(status):
    service, sessions, gates, enrollments = service_for(apply_enabled=True)
    original_factory = service.session_factory

    def factory():
        session = original_factory()
        if len(sessions) > 1:
            session.scalar.return_value = 7
        return session

    service.session_factory = factory
    enrollments.return_value.preview.side_effect = lambda candidate_id: (
        enrollment_result(candidate_id, status, gate_status=None)
    )
    result = service.run_cycle(apply=True)
    assert result.candidates[0].status == status
    gates.return_value.evaluate.assert_not_called()
    enrollments.return_value.enroll.assert_not_called()
    sessions[-1].commit.assert_not_called()


@pytest.mark.parametrize("status", ["INSUFFICIENT_DATA", "NOT_ELIGIBLE"])
def test_gate_change_at_enrollment_is_reported_without_commit(status):
    service, sessions, _, enrollments = service_for(apply_enabled=True)
    enrollments.return_value.enroll.side_effect = lambda candidate_id: (
        enrollment_result(candidate_id, "GATE_NOT_ELIGIBLE", status)
    )
    result = service.run_cycle(apply=True)
    assert result.candidates[0].status == status
    assert result.candidates[0].enrollment_status == "GATE_NOT_ELIGIBLE"
    sessions[-1].commit.assert_not_called()


def test_candidate_error_is_isolated_and_exception_text_never_enters_report():
    service, sessions, gates, _ = service_for((1, 2), apply_enabled=True)

    def evaluate(*, candidate_id):
        if candidate_id == 1:
            raise RuntimeError("fixture-only-secret-must-not-appear")
        return SimpleNamespace(candidate_id=candidate_id, status="ELIGIBLE_FOR_REVIEW")

    gates.return_value.evaluate.side_effect = evaluate
    result = service.run_cycle(apply=True)
    assert [item.status for item in result.candidates] == ["EXECUTION_ERROR", "CREATED"]
    assert result.cursor_candidate_id == 2
    sessions[1].rollback.assert_called_once()
    sessions[2].commit.assert_called_once()
    assert "fixture-only-secret-must-not-appear" not in str(report(result))


def test_commit_failure_rolls_back_only_failed_candidate_and_continues():
    service, sessions, _, _ = service_for((1, 2), apply_enabled=True)
    original_factory = service.session_factory

    def factory():
        session = original_factory()
        if len(sessions) == 2:
            session.commit.side_effect = RuntimeError("fixture-only-secret")
        return session

    service.session_factory = factory
    result = service.run_cycle(apply=True)
    assert result.counts == {"EXECUTION_ERROR": 1, "CREATED": 1}
    assert result.database_write
    sessions[1].rollback.assert_called_once()
    sessions[2].commit.assert_called_once()


@pytest.mark.parametrize(
    "change",
    ["database_write", "live_policy_change", "external_calls", "promotion_performed"],
)
def test_inconsistent_service_result_rolls_back(change):
    service, sessions, _, enrollments = service_for(apply_enabled=True)
    result = enrollment_result(status="CREATED")
    setattr(result, change, not getattr(result, change))
    enrollments.return_value.enroll.side_effect = None
    enrollments.return_value.enroll.return_value = result
    cycle = service.run_cycle(apply=True)
    assert cycle.counts == {"EXECUTION_ERROR": 1}
    assert not cycle.database_write
    sessions[-1].commit.assert_not_called()


def test_empty_registry_is_successful_and_read_only():
    service, sessions, gates, enrollments = service_for(())
    result = service.run_cycle()
    assert result.registered_candidate_count == 0
    assert result.candidates == ()
    assert not result.database_write
    gates.assert_not_called()
    enrollments.assert_not_called()
    sessions[0].commit.assert_not_called()


def test_keyset_batches_are_fair_and_discover_new_candidates_without_infinite_sweep():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE research_policy_candidates (id INTEGER PRIMARY KEY)")
            )
            connection.execute(
                text(
                    "CREATE TABLE shadow_policy_enrollments (id INTEGER PRIMARY KEY, candidate_id INTEGER)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO research_policy_candidates VALUES (1), (2), (3), (4), (5)"
                )
            )
        gates = MagicMock()
        gates.return_value.evaluate.side_effect = lambda candidate_id: SimpleNamespace(
            candidate_id=candidate_id, status="INSUFFICIENT_DATA"
        )
        service = ResearchLifecycleService(
            lambda: Session(engine, autoflush=False),
            settings=settings(research_lifecycle_batch_size=2),
            gate_service_factory=gates,
        )
        batches = []
        for number in range(6):
            if number == 1:
                with engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO research_policy_candidates VALUES (6)")
                    )
            cycle = service.run_cycle()
            batches.append(tuple(item.candidate_id for item in cycle.candidates))
        assert batches == [(1, 2), (3, 4), (5,), (1, 2), (3, 4), (5, 6)]
        assert cycle.registered_candidate_count == 6
    finally:
        engine.dispose()
