from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchPolicyCandidate,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ALREADY_REGISTERED,
    CREATED,
    ResearchPolicyCandidateConflictError,
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.screening_gated_research_candidate_registration_service import (
    CREATED as BATCH_CREATED,
    READY,
    ScreeningGatedResearchCandidateRegistrationService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


def _scenario(name="candidate-a", *, momentum="0.15", liquidity="0.35"):
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": name,
                    "component_weights": {
                        "liquidity": liquidity,
                        "trend_alignment": "0.20",
                        "momentum": momentum,
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                }
            ],
        }
    )[0]


def _policy_data(*, top_n=7):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=top_n,
        market_universe_prefilter_n=max(top_n, 10),
    )
    return build_policy_data(settings, HeuristicMarketRankingPolicy())


def _snapshot(
    session,
    user,
    *,
    captured_at,
    policy_data,
    signature=None,
    exchange="UPBIT",
    quote_asset="KRW",
    schema=DATASET_SCHEMA_VERSION,
):
    run = AnalysisRun(
        user_id=user.id,
        pipeline_run_id=str(uuid4()),
        run_type="MARKET_UNIVERSE",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    session.add(run)
    session.flush()
    snapshot = StrategyReplaySnapshot(
        analysis_run_id=run.id,
        pipeline_run_id=run.pipeline_run_id,
        user_id=user.id,
        exchange=exchange,
        quote_asset=quote_asset,
        dataset_schema_version=schema,
        policy_signature=signature or policy_signature(policy_data),
        policy_data=policy_data,
        research_candidate_count=10,
        prefilter_candidate_count=10,
        ranked_candidate_count=7,
        final_candidate_count=7,
        captured_at=captured_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _copy_candidate(source, **changes):
    values = {
        column.name: getattr(source, column.name)
        for column in ResearchPolicyCandidate.__table__.columns
        if column.name != "id"
    }
    values.update(changes)
    return ResearchPolicyCandidate(**values)


def _screening_result(*scenarios):
    candidates = tuple(
        SimpleNamespace(
            scenario_name=item.name,
            scenario_definition_signature=item.definition_signature,
            component_weights=item.component_weights,
            donor_field="liquidity",
            receiver_field="trend_alignment",
            transfer_step=Decimal("0.05"),
            status="PASS",
        )
        for item in scenarios
    )
    return SimpleNamespace(
        status="SUCCESS",
        safe_reason=None,
        invalid_count=0,
        pass_count=len(candidates),
        candidate_count=len(candidates),
        candidate_results=candidates,
        historical_evidence_as_of=datetime(2062, 1, 1, tzinfo=UTC),
        gate_schema_version="historical-candidate-screening-gate-v1",
        screening_policy_schema_version="historical-candidate-screening-policy-v1",
        screening_policy_signature="screening-policy:test",
        source_promotion_gate_policy_schema_version="policy-promotion-gate-v1",
        source_promotion_gate_policy_signature="promotion-policy:test",
    )


def test_postgresql_screening_gated_batch_registration_shares_atomic_anchor():
    with SessionLocal() as session:
        user = User(name=f"screened-batch-{uuid4()}")
        session.add(user)
        session.flush()
        reference_time = datetime(2062, 1, 2, tzinfo=UTC)
        reference = _snapshot(
            session,
            user,
            captured_at=reference_time,
            policy_data=_policy_data(top_n=7),
        )
        scenarios = (
            _scenario(name="screened-a"),
            _scenario(name="screened-b", momentum="0.20", liquidity="0.30"),
        )
        screening = MagicMock()
        screening.evaluate.return_value = _screening_result(*scenarios)
        registered_at = datetime(2062, 1, 3, tzinfo=UTC)
        registry = ResearchPolicyCandidateRegistryService(
            session, now_fn=lambda: registered_at
        )
        service = ScreeningGatedResearchCandidateRegistrationService(
            session, screening_service=screening, registry_service=registry
        )

        preview = service.preview(reference_snapshot_id=reference.id)
        assert preview.status == READY
        assert preview.registration_candidate_count == 2

        applied = service.apply(
            reference_snapshot_id=reference.id,
            expected_plan_signature=preview.registration_plan_signature,
        )

        assert applied.status == BATCH_CREATED
        assert applied.created_candidate_count == 2
        rows = tuple(
            session.scalars(
                select(ResearchPolicyCandidate)
                .where(ResearchPolicyCandidate.user_id == user.id)
                .order_by(ResearchPolicyCandidate.id)
            )
        )
        assert len(rows) == 2
        assert {item.registered_at for item in rows} == {registered_at}
        assert {item.registration_snapshot_id_watermark for item in rows} == {
            reference.id
        }
        assert {item.registration_captured_at_watermark for item in rows} == {
            reference_time
        }
        session.rollback()


@pytest.mark.parametrize(
    "failure_mode,expected_status",
    [("already", "STALE_REGISTRATION_STATE"), ("error", "REGISTRATION_FAILED")],
)
def test_postgresql_screening_gated_batch_rolls_back_partial_creation(
    failure_mode, expected_status
):
    class FailingSecondRegistry(ResearchPolicyCandidateRegistryService):
        calls = 0

        def register_with_shared_anchor(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                if failure_mode == "error":
                    raise RuntimeError("injected second insert failure")
                return SimpleNamespace(
                    registration_status=ALREADY_REGISTERED,
                    registration_created=False,
                    candidate=None,
                    plan=None,
                )
            return super().register_with_shared_anchor(**kwargs)

    with SessionLocal() as session:
        user = User(name=f"screened-rollback-{uuid4()}")
        session.add(user)
        session.flush()
        reference = _snapshot(
            session,
            user,
            captured_at=datetime(2063, 1, 2, tzinfo=UTC),
            policy_data=_policy_data(top_n=7),
        )
        scenarios = (
            _scenario(name="rollback-a"),
            _scenario(name="rollback-b", momentum="0.20", liquidity="0.30"),
        )
        screening = MagicMock()
        screening.evaluate.return_value = _screening_result(*scenarios)
        registry = FailingSecondRegistry(
            session, now_fn=lambda: datetime(2063, 1, 3, tzinfo=UTC)
        )
        service = ScreeningGatedResearchCandidateRegistrationService(
            session, screening_service=screening, registry_service=registry
        )
        preview = service.preview(reference_snapshot_id=reference.id)
        applied = service.apply(
            reference_snapshot_id=reference.id,
            expected_plan_signature=preview.registration_plan_signature,
        )
        assert applied.status == expected_status
        assert not applied.database_write
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchPolicyCandidate)
                .where(ResearchPolicyCandidate.user_id == user.id)
            )
            == 0
        )
        session.rollback()


def test_postgresql_registration_watermarks_retry_and_constraints():
    with SessionLocal() as session:
        user = User(name=f"registry-{uuid4()}")
        other_user = User(name=f"registry-other-{uuid4()}")
        session.add_all((user, other_user))
        session.flush()
        start = datetime(2060, 1, 1, tzinfo=UTC)
        policy_data = _policy_data(top_n=7)
        baseline_signature = policy_signature(policy_data)
        reference = _snapshot(
            session,
            user,
            captured_at=start,
            policy_data=policy_data,
        )
        matching_middle = _snapshot(
            session,
            user,
            captured_at=start + timedelta(hours=2),
            policy_data=policy_data,
        )
        matching_latest = _snapshot(
            session,
            user,
            captured_at=start + timedelta(hours=1),
            policy_data=policy_data,
        )
        exclusions = (
            _snapshot(
                session,
                other_user,
                captured_at=start + timedelta(days=10),
                policy_data=policy_data,
            ),
            _snapshot(
                session,
                user,
                captured_at=start + timedelta(days=11),
                policy_data=policy_data,
                exchange="BITHUMB",
            ),
            _snapshot(
                session,
                user,
                captured_at=start + timedelta(days=12),
                policy_data=policy_data,
                quote_asset="USDT",
            ),
            _snapshot(
                session,
                user,
                captured_at=start + timedelta(days=13),
                policy_data=_policy_data(top_n=5),
            ),
            _snapshot(
                session,
                user,
                captured_at=start + timedelta(days=14),
                policy_data={
                    **policy_data,
                    "market_universe": {
                        **policy_data["market_universe"],
                        "top_n": 5,
                    },
                },
                signature=baseline_signature,
            ),
            _snapshot(
                session,
                user,
                captured_at=start + timedelta(days=15),
                policy_data=policy_data,
                schema="unsupported",
            ),
        )
        before = session.scalar(
            select(func.count()).select_from(ResearchPolicyCandidate)
        )
        registered_at = datetime(2060, 2, 1, tzinfo=UTC)
        service = ResearchPolicyCandidateRegistryService(
            session, now_fn=lambda: registered_at
        )
        scenario = _scenario()

        created = service.register(
            reference_snapshot_id=reference.id, scenario=scenario
        )

        assert created.registration_status == CREATED
        assert created.candidate is not None
        candidate = created.candidate
        assert candidate.registration_snapshot_id_watermark == matching_latest.id
        assert candidate.registration_snapshot_id_watermark > matching_middle.id
        assert (
            candidate.registration_captured_at_watermark == matching_middle.captured_at
        )
        assert all(
            item.id > candidate.registration_snapshot_id_watermark
            for item in exclusions
        )
        assert (
            session.scalar(select(func.count()).select_from(ResearchPolicyCandidate))
            == before + 1
        )

        anchor = (
            candidate.id,
            candidate.registered_at,
            candidate.registration_snapshot_id_watermark,
            candidate.registration_captured_at_watermark,
        )
        new_snapshot = _snapshot(
            session,
            user,
            captured_at=start + timedelta(days=20),
            policy_data=policy_data,
        )
        assert new_snapshot.id > anchor[2]
        retry = service.register(reference_snapshot_id=reference.id, scenario=scenario)
        assert retry.registration_status == ALREADY_REGISTERED
        assert retry.registration_created is False
        assert (
            retry.candidate.id,
            retry.candidate.registered_at,
            retry.candidate.registration_snapshot_id_watermark,
            retry.candidate.registration_captured_at_watermark,
        ) == anchor
        assert (
            session.scalar(select(func.count()).select_from(ResearchPolicyCandidate))
            == before + 1
        )

        with pytest.raises(ResearchPolicyCandidateConflictError):
            service.register(
                reference_snapshot_id=reference.id,
                scenario=_scenario(
                    name=scenario.name, momentum="0.30", liquidity="0.20"
                ),
            )
        with pytest.raises(ResearchPolicyCandidateConflictError):
            service.register(
                reference_snapshot_id=reference.id,
                scenario=_scenario(name="candidate-alias"),
            )

        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    _copy_candidate(
                        candidate,
                        scenario_definition_signature="different-definition",
                    )
                )
                session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(_copy_candidate(candidate, scenario_name="different-alias"))
                session.flush()
        session.rollback()


def test_postgresql_different_baseline_topn_and_user_contexts_are_independent():
    with SessionLocal() as session:
        first_user = User(name=f"registry-context-a-{uuid4()}")
        second_user = User(name=f"registry-context-b-{uuid4()}")
        session.add_all((first_user, second_user))
        session.flush()
        captured_at = datetime(2061, 1, 1, tzinfo=UTC)
        first = _snapshot(
            session,
            first_user,
            captured_at=captured_at,
            policy_data=_policy_data(top_n=7),
        )
        other_top_n = _snapshot(
            session,
            first_user,
            captured_at=captured_at,
            policy_data=_policy_data(top_n=5),
        )
        other_baseline_policy = deepcopy(_policy_data(top_n=7))
        other_baseline_policy["ranking"]["weights"]["liquidity"] = "0.34"
        other_baseline_policy["ranking"]["weights"]["trend_alignment"] = "0.21"
        other_baseline = _snapshot(
            session,
            first_user,
            captured_at=captured_at,
            policy_data=other_baseline_policy,
        )
        other_user = _snapshot(
            session,
            second_user,
            captured_at=captured_at,
            policy_data=_policy_data(top_n=7),
        )
        scenario = _scenario()
        service = ResearchPolicyCandidateRegistryService(
            session, now_fn=lambda: datetime(2061, 2, 1, tzinfo=UTC)
        )
        results = tuple(
            service.register(reference_snapshot_id=item.id, scenario=scenario)
            for item in (first, other_top_n, other_baseline, other_user)
        )
        assert all(item.registration_status == CREATED for item in results)
        assert len({item.candidate.id for item in results}) == 4
        assert {item.candidate.effective_top_n for item in results} == {5, 7}
        assert len({item.candidate.user_id for item in results}) == 2
        assert len({item.candidate.baseline_policy_signature for item in results}) == 3
        session.rollback()


def test_postgresql_registry_schema_has_expected_constraints_indexes_and_types():
    with SessionLocal() as session:
        inspector = inspect(session.get_bind())
        table = "research_policy_candidates"
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        assert isinstance(columns["component_weights"]["type"], JSONB)
        assert columns["registered_at"]["type"].timezone is True
        assert columns["reference_snapshot_captured_at"]["type"].timezone is True
        assert columns["registration_captured_at_watermark"]["type"].timezone is True
        assert {item["name"] for item in inspector.get_unique_constraints(table)} == {
            "uq_rpc_context_definition_signature",
            "uq_rpc_context_scenario_name",
        }
        assert {item["name"] for item in inspector.get_check_constraints(table)} == {
            "ck_rpc_effective_top_n_positive",
            "ck_rpc_snapshot_watermark_positive",
        }
        assert {item["name"] for item in inspector.get_indexes(table)} >= {
            "ix_research_policy_candidates_reference_snapshot_id",
            "ix_research_policy_candidates_user_id",
            "ix_rpc_context_registered_at",
        }
        foreign_keys = {
            tuple(item["constrained_columns"]): tuple(item["referred_columns"])
            for item in inspector.get_foreign_keys(table)
        }
        assert foreign_keys == {
            ("reference_snapshot_id",): ("id",),
            ("user_id",): ("id",),
        }
