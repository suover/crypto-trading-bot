from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.sql.sqltypes import BigInteger

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal, engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchCandidateAutomationState,
    ResearchPolicyCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DeterministicRankingCandidateGeneratorService,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.research_candidate_automation_service import (
    ResearchCandidateAutomationService,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.screening_gated_research_candidate_registration_service import (
    ScreeningGatedResearchCandidateRegistrationService,
)
from scripts import run_research_candidate_automation_worker as worker
from test_cost_adjusted_ranking_evaluation_postgres import _create_snapshot
from test_research_policy_candidate_registry_postgres import _scenario
from test_screening_gated_research_candidate_registration_service import _screening


START = datetime(2026, 1, 1, tzinfo=UTC)
LOCK_KEY = worker.RESEARCH_CANDIDATE_AUTOMATION_LOCK_KEY


def settings(user_id=None, **overrides):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql://test:test@localhost/test",
            "trading_user_id": user_id,
            "market_universe_mode": "DYNAMIC",
            "market_universe_top_n": 2,
            "market_universe_prefilter_n": 3,
            "research_candidate_automation_enabled": True,
            "research_candidate_auto_registration_enabled": True,
            "research_candidate_min_new_snapshots": 8,
            **overrides,
        },
    )


@pytest.fixture
def users():
    created = []

    def new_user():
        with SessionLocal() as session:
            user = User(name=f"phase2-{uuid4()}")
            session.add(user)
            session.flush()
            created.append(user.id)
            session.commit()
            return created[-1]

    yield new_user
    with SessionLocal() as session:
        for model in (
            ResearchCandidateAutomationState,
            ResearchPolicyCandidate,
            AnalysisRun,
        ):
            session.execute(delete(model).where(model.user_id.in_(created)))
        session.execute(delete(User).where(User.id.in_(created)))
        session.commit()


def seed(
    user_id,
    indices,
    *,
    late=False,
    top_n=2,
    exchange="UPBIT",
    quote="KRW",
    invalid_schema=False,
):
    ids = []
    with SessionLocal() as session:
        user = session.get(User, user_id)
        for index in indices:
            captured_at = START + timedelta(days=index * 2)
            snapshot = _create_snapshot(
                session,
                user,
                captured_at=captured_at,
                sequence=index % 4,
                top_n=top_n,
                invalid=invalid_schema,
            )
            snapshot.created_at = captured_at + timedelta(seconds=1)
            snapshot.exchange = exchange
            snapshot.quote_asset = quote
            ids.append(snapshot.id)
            rows = session.scalars(
                select(StrategyReplayCandidateOutcome).where(
                    StrategyReplayCandidateOutcome.strategy_replay_snapshot_id
                    == snapshot.id
                )
            ).all()
            for row in rows:
                row.created_at = row.updated_at = row.evaluated_at
                if row.horizon_minutes != 60:
                    continue
                day = {
                    column.name: getattr(row, column.name)
                    for column in row.__table__.columns
                    if column.name != "id"
                }
                day.update(
                    horizon_minutes=1440,
                    target_at=captured_at + timedelta(days=1),
                    end_price_at=captured_at + timedelta(minutes=1439),
                    evaluated_at=captured_at + timedelta(minutes=1441),
                )
                day["created_at"] = day["updated_at"] = (
                    START + timedelta(days=15) if late else day["evaluated_at"]
                )
                session.add(StrategyReplayCandidateOutcome(**day))
        session.commit()
    return ids


def state(user_id):
    with SessionLocal() as session:
        return session.scalar(
            select(ResearchCandidateAutomationState).where(
                ResearchCandidateAutomationState.user_id == user_id
            )
        )


def bootstrap(user_id):
    (reference,) = seed(user_id, [-1])
    result = ResearchCandidateAutomationService(
        SessionLocal, settings=settings(user_id)
    ).run_cycle(scheduled=True)
    assert (result.status, result.new_snapshot_count) == ("BOOTSTRAPPED", 0)
    assert state(user_id).reviewed_snapshot_id == reference
    return reference


def candidates(user_id):
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ResearchPolicyCandidate).where(
                    ResearchPolicyCandidate.user_id == user_id
                )
            )
        )


def screening_factory(statuses=("PASS", "FAIL", "INSUFFICIENT"), *, service_type=None):
    def factory(session):
        def evaluate(*, reference_snapshot_id, step):
            generated = DeterministicRankingCandidateGeneratorService(session).generate(
                reference_snapshot_id=reference_snapshot_id, step=step
            )
            result = _screening(
                SimpleNamespace(**vars(candidate), status=status)
                for candidate, status in zip(generated.all_candidates, statuses)
            )
            reference = session.get(StrategyReplaySnapshot, reference_snapshot_id)
            result.historical_evidence_as_of = reference.created_at
            return result

        service = service_type or ScreeningGatedResearchCandidateRegistrationService
        return service(session, screening_service=SimpleNamespace(evaluate=evaluate))

    return factory


def test_postgres_checkpoint_schema():
    inspector = inspect(engine)
    table = "research_candidate_automation_states"
    assert inspector.get_pk_constraint(table)["constrained_columns"] == [
        "user_id",
        "exchange",
        "quote_asset",
        "dataset_schema_version",
        "baseline_policy_signature",
        "effective_top_n",
    ]
    assert inspector.get_foreign_keys(table)[0]["referred_table"] == "users"
    columns = {column["name"]: column for column in inspector.get_columns(table)}
    assert isinstance(columns["user_id"]["type"], BigInteger)
    assert columns["reviewed_captured_at"]["type"].timezone
    assert columns["reviewed_created_at"]["type"].timezone
    assert columns["last_registered_at"]["nullable"]


def test_first_cycle_anchors_retained_history_without_counting_it_as_new(users):
    user = users()
    ids = seed(user, range(9))
    factory = MagicMock()
    service = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=factory,
    )
    for apply in (False, True):
        result = service.run_cycle(apply=apply)
        assert result.status == "BOOTSTRAP_REQUIRED"
        assert not result.database_write and state(user) is None
    result = service.run_cycle(scheduled=True, apply=True)
    assert (result.status, result.new_snapshot_count) == ("BOOTSTRAPPED", 0)
    checkpoint = state(user)
    assert (
        checkpoint.last_observed_snapshot_id
        == checkpoint.reviewed_snapshot_id
        == ids[-1]
    )
    assert checkpoint.last_reference_snapshot_id is None
    assert checkpoint.last_registered_snapshot_id is None
    assert service.run_cycle(scheduled=True, apply=True).status == "NO_NEW_SNAPSHOTS"
    factory.assert_not_called()

    # Eight new snapshots do not by themselves have eight mature observations
    # at the newest cutoff. The retained history remains available as evidence.
    seed(user, range(9, 17))
    result = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=screening_factory(),
    ).run_cycle()
    assert (result.status, result.new_snapshot_count) == ("PREVIEW_READY", 8)


def test_growth_and_as_of_maturity_do_not_mix_contexts_or_consume_delayed_data(users):
    user = users()
    anchor = bootstrap(user)
    seed(user, range(7), late=True)
    seed(users(), [8])
    seed(user, [8], exchange="BITHUMB")
    seed(user, [8], quote="BTC")
    seed(user, [8], invalid_schema=True)
    registration = MagicMock()
    service = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=registration,
    )
    result = service.run_cycle(scheduled=True)
    assert (result.status, result.new_snapshot_count) == ("NO_NEW_SNAPSHOTS", 7)
    assert state(user).reviewed_snapshot_id == anchor
    assert state(user).last_observed_snapshot_id == result.reference_snapshot_id
    registration.assert_not_called()

    (reference,) = seed(user, [7], late=True)
    result = service.run_cycle(scheduled=True)
    assert result.status == "INSUFFICIENT_RESEARCH_DATA"
    assert result.reference_snapshot_id == reference
    assert result.state_write and not result.created_candidate_count
    assert state(user).reviewed_snapshot_id == anchor
    assert service.run_cycle(scheduled=True).status == "NO_NEW_SNAPSHOTS"
    registration.assert_not_called()

    (next_reference,) = seed(user, [8])
    fresh = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=screening_factory(),
    )
    result = fresh.run_cycle()
    assert (result.status, result.reference_snapshot_id) == (
        "PREVIEW_READY",
        next_reference,
    )
    assert not result.database_write
    assert state(user).last_reference_snapshot_id == reference


def test_real_historical_screening_preview_is_read_only_and_periodic_checkpoint_restarts(
    users,
):
    user = users()
    anchor = bootstrap(user)
    seed(user, range(9))
    config = settings(user, research_candidate_auto_registration_enabled=False)
    service = ResearchCandidateAutomationService(SessionLocal, settings=config)
    preview = service.run_cycle()
    assert preview.status in {
        "PREVIEW_READY",
        "NO_PASS_CANDIDATES",
        "REGISTRATION_CAP_EXCEEDED",
    }
    assert preview.screened_candidate_count == preview.generated_candidate_count == 42
    assert preview.generator_step == config.research_candidate_generation_step
    assert preview.fail_candidate_count + preview.pass_candidate_count == 42
    assert not preview.database_write and state(user).reviewed_snapshot_id == anchor
    assert not candidates(user)

    scheduled = service.run_cycle(scheduled=True)
    assert scheduled.status == preview.status and scheduled.state_write
    assert state(user).reviewed_snapshot_id == preview.reference_snapshot_id
    assert state(user).last_registered_snapshot_id is None
    restarted = ResearchCandidateAutomationService(SessionLocal, settings=config)
    assert restarted.run_cycle(scheduled=True).status == "NO_NEW_SNAPSHOTS"
    assert restarted.run_cycle(apply=True).status == "APPLY_DISABLED"
    assert not candidates(user)


def test_apply_registers_only_pass_and_commits_forward_anchor_and_checkpoint(users):
    user = users()
    bootstrap(user)
    ids = seed(user, range(8))
    service = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=screening_factory(),
    )
    preview = service.run_cycle()
    result = service.run_cycle(apply=True)
    assert result.status == "REGISTERED"
    assert result.registration_plan_signature == preview.registration_plan_signature
    assert result.created_candidate_count == result.pass_candidate_count == 1
    assert result.database_write and result.state_write
    assert not result.external_calls and not result.live_policy_change
    assert state(user).reviewed_snapshot_id == ids[-1]
    assert state(user).last_registered_snapshot_id == ids[-1]
    (candidate,) = candidates(user)
    assert state(user).last_registered_at == candidate.registered_at
    assert sum(map(Decimal, candidate.component_weights.values())) == Decimal(1)
    assert candidate.registration_snapshot_id_watermark == ids[-1]
    assert (
        candidate.registration_captured_at_watermark
        == candidate.reference_snapshot_captured_at
    )
    with SessionLocal() as session:
        assert (
            load_and_validate_forward_candidate(
                session, candidate.id
            ).metadata.candidate_id
            == candidate.id
        )
    assert service.run_cycle(apply=True).status == "NO_NEW_SNAPSHOTS"
    assert len(candidates(user)) == 1


@pytest.mark.parametrize("newer_reference", [False, True])
def test_scheduled_review_keeps_registration_opportunity_after_opt_in(
    users,
    newer_reference,
):
    user = users()
    bootstrap(user)
    ids = seed(user, range(8))
    factory = screening_factory()
    review = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user, research_candidate_auto_registration_enabled=False),
        registration_factory=factory,
    ).run_cycle(scheduled=True)
    assert review.status == "PREVIEW_READY"
    assert state(user).reviewed_snapshot_id == ids[-1]
    assert state(user).last_registered_snapshot_id is None
    assert state(user).last_registration_status is None
    assert not candidates(user)

    reference = seed(user, [8])[0] if newer_reference else ids[-1]
    applied = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=factory,
    ).run_cycle(apply=True, scheduled=True)
    assert applied.status == "REGISTERED"
    assert applied.new_snapshot_count == int(newer_reference)
    assert applied.reference_snapshot_id == reference
    assert (
        applied.registration_plan_signature != review.registration_plan_signature
    ) is newer_reference
    assert state(user).last_registered_snapshot_id == reference
    assert len(candidates(user)) == 1


def test_cap_block_is_not_registration_and_changed_cap_allows_fresh_retry(users):
    user = users()
    bootstrap(user)
    (reference,) = seed(user, range(8))[-1:]
    factory = MagicMock(wraps=screening_factory(("PASS", "PASS")))
    limited = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user, research_candidate_max_registrations_per_cycle=1),
        registration_factory=factory,
    )
    result = limited.run_cycle(apply=True, scheduled=True)
    assert result.status == "REGISTRATION_CAP_EXCEEDED"
    assert state(user).last_registration_status == "REGISTRATION_CAP_EXCEEDED"
    assert state(user).last_registered_snapshot_id is None
    assert not candidates(user)
    assert limited.run_cycle(apply=True, scheduled=True).status == "NO_NEW_SNAPSHOTS"
    factory.assert_called_once()

    retried = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=factory,
    ).run_cycle(apply=True, scheduled=True)
    assert (retried.status, retried.new_snapshot_count) == ("REGISTERED", 0)
    assert state(user).last_registered_snapshot_id == reference
    assert len(candidates(user)) == 2
    assert factory.call_count == 2


@pytest.mark.parametrize(
    "limit",
    [
        "research_candidate_max_registrations_per_cycle",
        "research_candidate_max_registered_per_context",
    ],
)
def test_whole_plan_is_rejected_at_each_cap(users, limit):
    user = users()
    anchor = bootstrap(user)
    seed(user, range(8))
    service = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user, **{limit: 1}),
        registration_factory=screening_factory(("PASS", "PASS")),
    )
    result = service.run_cycle(apply=True)
    assert result.status == "REGISTRATION_CAP_EXCEEDED"
    assert result.registration_candidate_count == 2
    assert not result.database_write and not candidates(user)
    assert state(user).reviewed_snapshot_id == anchor


@pytest.mark.parametrize("change", ["reference", "signature", "context_cap"])
def test_apply_fails_closed_when_preview_state_changes(users, change):
    user = users()
    anchor = bootstrap(user)
    seed(user, range(8))

    class ChangedAfterPreview(ScreeningGatedResearchCandidateRegistrationService):
        def apply(self, **kwargs):
            if change == "reference":
                seed(user, [8])
            elif change == "signature":
                kwargs["expected_plan_signature"] = "different-signature"
            else:
                with SessionLocal() as concurrent:
                    ResearchPolicyCandidateRegistryService(concurrent).register(
                        reference_snapshot_id=kwargs["reference_snapshot_id"],
                        scenario=_scenario(),
                    )
                    concurrent.commit()
            return super().apply(**kwargs)

    service = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user, research_candidate_max_registered_per_context=1),
        registration_factory=screening_factory(service_type=ChangedAfterPreview),
    )
    result = service.run_cycle(apply=True)
    assert (
        result.status
        == {
            "reference": "STALE_REFERENCE",
            "signature": "STALE_REGISTRATION_PLAN",
            "context_cap": "REGISTRATION_CAP_EXCEEDED",
        }[change]
    )
    assert not result.database_write and state(user).reviewed_snapshot_id == anchor
    assert len(candidates(user)) == int(change == "context_cap")


@pytest.mark.parametrize("failure", ["checkpoint", "commit"])
def test_checkpoint_or_commit_failure_rolls_back_and_can_retry(
    users, monkeypatch, failure
):
    user = users()
    anchor = bootstrap(user)
    seed(user, range(8))

    def failing_session():
        session = SessionLocal()

        def fail_after_flush():
            session.flush()
            raise RuntimeError("sensitive-connection-text")

        session.commit = fail_after_flush
        return session

    config = settings(user)
    factory = screening_factory()
    service = ResearchCandidateAutomationService(
        failing_session if failure == "commit" else SessionLocal,
        settings=config,
        registration_factory=factory,
    )
    if failure == "checkpoint":
        monkeypatch.setattr(
            service, "_record", MagicMock(side_effect=RuntimeError("sensitive-text"))
        )
    result = service.run_cycle(apply=True)
    assert (result.status, result.safe_reason) == ("EXECUTION_ERROR", "RuntimeError")
    assert result.created_candidate_count == 0 and not result.database_write
    assert result.registration_status == "REGISTRATION_FAILED"
    assert not candidates(user) and state(user).reviewed_snapshot_id == anchor
    assert state(user).last_registered_snapshot_id is None
    assert (
        ResearchCandidateAutomationService(
            SessionLocal,
            settings=config,
            registration_factory=factory,
        )
        .run_cycle(apply=True)
        .status
        == "REGISTERED"
    )


def test_latest_other_top_n_blocks_instead_of_using_older_reference(users):
    user = users()
    seed(user, range(8))
    (latest,) = seed(user, [8], top_n=3)
    factory = MagicMock()
    result = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=factory,
    ).run_cycle(apply=True)
    assert (result.status, result.reference_snapshot_id) == (
        "RUNTIME_CONTEXT_MISMATCH",
        latest,
    )
    factory.assert_not_called()
    assert not candidates(user)


def test_existing_definitions_are_excluded_without_rerunning_screening(users):
    user = users()
    bootstrap(user)
    ids = seed(user, range(8))
    with SessionLocal() as session:
        generated = DeterministicRankingCandidateGeneratorService(session).generate(
            reference_snapshot_id=ids[-1]
        )
        registry = ResearchPolicyCandidateRegistryService(session)
        for candidate in generated.all_candidates:
            registry.register(
                reference_snapshot_id=ids[-1], scenario=candidate.definition
            )
        session.commit()

    factory = MagicMock()
    result = ResearchCandidateAutomationService(
        SessionLocal, settings=settings(user), registration_factory=factory
    ).run_cycle(apply=True)
    assert result.status == "NO_NOVEL_CANDIDATES"
    assert (
        result.already_registered_candidate_count
        == result.generated_candidate_count
        == 42
    )
    assert result.novel_candidate_count == 0 and not result.database_write
    assert len(candidates(user)) == 42
    factory.assert_not_called()


def test_invalid_screening_refuses_entire_plan(users):
    user = users()
    bootstrap(user)
    seed(user, range(8))
    result = ResearchCandidateAutomationService(
        SessionLocal,
        settings=settings(user),
        registration_factory=screening_factory(("PASS", "INVALID")),
    ).run_cycle(apply=True)
    assert result.status == "INVALID_RESEARCH_DATA"
    assert result.invalid_candidate_count == 1
    assert not candidates(user)


def test_postgres_advisory_lock_excludes_second_worker_and_releases(capsys):
    session_factory = MagicMock()
    with PostgresAdvisoryLock(LOCK_KEY) as lock:
        assert lock.acquired
        assert (
            worker.run_worker(
                once=True, settings=settings(), session_factory=session_factory
            )
            == 0
        )
    assert "LOCK_NOT_ACQUIRED" in capsys.readouterr().out
    session_factory.assert_not_called()

    session_factory.side_effect = RuntimeError("sensitive-connection-text")
    assert (
        worker.run_worker(
            once=True, settings=settings(), session_factory=session_factory
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "RuntimeError" in output and "sensitive-connection-text" not in output
    with PostgresAdvisoryLock(LOCK_KEY) as lock:
        assert lock.acquired
