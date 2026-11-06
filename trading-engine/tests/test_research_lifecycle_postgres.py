from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import delete, func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.forward_candidate_provenance import (
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.research_lifecycle_service import (
    ResearchLifecycleService,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentService,
    validate_stored_shadow_policy_enrollment,
)
from test_research_lifecycle_service import settings
from test_research_policy_candidate_registry_postgres import (
    _policy_data,
    _scenario,
    _snapshot,
)
from test_shadow_policy_enrollment_service import _gate
from scripts import run_research_lifecycle_worker as worker


def test_postgres_lifecycle_worker_lock_rejects_concurrent_runner_and_releases():
    first = PostgresAdvisoryLock(worker.RESEARCH_LIFECYCLE_WORKER_LOCK_KEY)
    assert first.acquire()
    lifecycle_factory = MagicMock()
    try:
        assert (
            worker.run_worker(
                once=True,
                settings=settings(),
                session_factory=SessionLocal,
                lifecycle_factory=lifecycle_factory,
            )
            == 0
        )
        lifecycle_factory.assert_not_called()
    finally:
        first.release()
    next_runner = PostgresAdvisoryLock(worker.RESEARCH_LIFECYCLE_WORKER_LOCK_KEY)
    try:
        assert next_runner.acquire()
    finally:
        next_runner.release()


def test_postgres_lifecycle_preview_fresh_gate_commit_duplicate_and_corruption():
    """Real candidate provenance, immutable enrollment INSERT and transaction boundary.

    Gate outcomes are controlled here; the existing Gate tests cover its unchanged
    evidence mathematics. Enrollment performs a second Gate evaluation itself.
    """
    start = datetime(2068, 1, 1, tzinfo=UTC)
    with SessionLocal() as session:
        user = User(name=f"lifecycle-{uuid4()}")
        session.add(user)
        session.flush()
        user_id = user.id
        reference = _snapshot(
            session, user, captured_at=start, policy_data=_policy_data()
        )
        candidate = (
            ResearchPolicyCandidateRegistryService(
                session, now_fn=lambda: start + timedelta(days=1)
            )
            .register(reference_snapshot_id=reference.id, scenario=_scenario())
            .candidate
        )
        candidate_id = candidate.id
        latest = _snapshot(
            session,
            user,
            captured_at=start + timedelta(days=2),
            policy_data=_policy_data(),
        )
        latest_id = latest.id
        session.commit()

    calls = []
    enrollment_gate_status = "ELIGIBLE_FOR_REVIEW"

    def gate_factory(session, *, enrollment=False):
        def evaluate(*, candidate_id):
            calls.append("enrollment" if enrollment else "initial")
            candidate = load_and_validate_forward_candidate(
                session, candidate_id
            ).metadata
            return _gate(
                candidate=candidate,
                status=enrollment_gate_status if enrollment else "ELIGIBLE_FOR_REVIEW",
                evaluated_at=start + timedelta(days=3),
                forward_snapshot_id_ceiling=latest_id,
            )

        return SimpleNamespace(evaluate=evaluate)

    def enrollment_factory(session):
        return ShadowPolicyEnrollmentService(
            session,
            gate_service=gate_factory(session, enrollment=True),
            now_fn=lambda: start + timedelta(days=4),
        )

    service = ResearchLifecycleService(
        SessionLocal,
        settings=settings(
            research_lifecycle_enabled=True,
            research_auto_shadow_enrollment_enabled=True,
            research_lifecycle_batch_size=500,
        ),
        gate_service_factory=gate_factory,
        enrollment_service_factory=enrollment_factory,
    )
    try:
        preview = service.run_cycle()
        assert (
            next(
                item for item in preview.candidates if item.candidate_id == candidate_id
            ).status
            == "DRY_RUN"
        )
        assert calls == ["initial", "enrollment"]
        assert not preview.database_write
        with SessionLocal() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ShadowPolicyEnrollment)
                    .where(ShadowPolicyEnrollment.candidate_id == candidate_id)
                )
                == 0
            )

        # A newly ineligible Gate must block even after the initial eligible result.
        enrollment_gate_status = "INSUFFICIENT_DATA"
        changed = service.run_cycle(apply=True)
        assert (
            next(
                item for item in changed.candidates if item.candidate_id == candidate_id
            ).status
            == "INSUFFICIENT_DATA"
        )
        assert not changed.database_write

        enrollment_gate_status = "ELIGIBLE_FOR_REVIEW"
        applied = service.run_cycle(apply=True)
        assert (
            next(
                item for item in applied.candidates if item.candidate_id == candidate_id
            ).status
            == "CREATED"
        )
        assert applied.database_write
        with SessionLocal() as session:
            stored = session.scalar(
                select(ShadowPolicyEnrollment).where(
                    ShadowPolicyEnrollment.candidate_id == candidate_id
                )
            )
            metadata = load_and_validate_forward_candidate(
                session, candidate_id
            ).metadata
            validate_stored_shadow_policy_enrollment(stored, metadata)
            assert stored.shadow_snapshot_id_watermark == latest_id
            assert stored.shadow_enrolled_at == start + timedelta(days=4)
            signature = stored.gate_decision_signature

        calls.clear()
        repeated = service.run_cycle(apply=True)
        assert repeated.counts == {"ALREADY_ENROLLED": 1}
        assert not repeated.database_write
        assert calls == []
        with SessionLocal() as session:
            rows = list(
                session.scalars(
                    select(ShadowPolicyEnrollment).where(
                        ShadowPolicyEnrollment.candidate_id == candidate_id
                    )
                )
            )
            assert len(rows) == 1
            assert rows[0].gate_decision_signature == signature
            rows[0].gate_decision_signature = "fixture-only-corrupt"
            session.commit()
        corrupted = service.run_cycle(apply=True)
        assert corrupted.counts == {"INVALID_SHADOW_ENROLLMENT": 1}
        assert not corrupted.database_write
        assert calls == []
    finally:
        # Only this test's committed rows are removed; no shared fixture truncation.
        with SessionLocal() as session:
            for model in (
                ShadowPolicyEnrollment,
                ResearchPolicyCandidate,
                StrategyReplaySnapshot,
                AnalysisRun,
            ):
                session.execute(delete(model).where(model.user_id == user_id))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
