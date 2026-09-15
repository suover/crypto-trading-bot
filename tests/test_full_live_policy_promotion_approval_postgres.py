from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    FullLivePolicyPromotionApproval,
    LivePolicyCanaryActivation,
    LivePolicyCanarySafetyBinding,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    ShadowPolicyPromotionApproval,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.full_live_policy_promotion_approval_service import (
    ALREADY_APPROVED,
    CONFIRMATION_MISMATCH,
    CREATED,
    FullLivePolicyPromotionApprovalService,
    FullLivePolicyPromotionApprovalWorkflow,
    REVIEW_NOT_ELIGIBLE,
    load_and_validate_full_live_policy_promotion_approval,
)
from crypto_trading_bot.services.live_canary_evidence_service import (
    live_canary_evidence_signature,
)
from crypto_trading_bot.services.live_canary_review_gate_service import (
    INSUFFICIENT_DATA,
    LiveCanaryReviewGateService,
)
from tests.test_full_live_policy_promotion_approval_service import (
    eligible_review as unit_eligible_review,
)
from tests.test_live_canary_review_gate_service import NOW, _payload, _report


def _persist_lineage(session):
    now = datetime.now(UTC)
    suffix = uuid4().hex
    scenario = f"full-live-{suffix}"
    user = User(name=scenario)
    session.add(user)
    session.flush()
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
        exchange="UPBIT",
        quote_asset="KRW",
        dataset_schema_version="strategy-replay-dataset-v1",
        policy_signature=f"baseline-{suffix}",
        policy_data={},
        research_candidate_count=1,
        prefilter_candidate_count=1,
        ranked_candidate_count=1,
        final_candidate_count=1,
        captured_at=now - timedelta(days=10),
    )
    session.add(snapshot)
    session.flush()
    candidate = ResearchPolicyCandidate(
        candidate_schema_version="research-policy-candidate-v1",
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name=scenario,
        scenario_definition_signature=f"scenario-{suffix}",
        component_weights={"liquidity": "1"},
        reference_snapshot_id=snapshot.id,
        reference_snapshot_captured_at=snapshot.captured_at,
        dataset_schema_version=snapshot.dataset_schema_version,
        baseline_policy_signature=snapshot.policy_signature,
        effective_top_n=1,
        registered_at=now - timedelta(days=9),
        registration_snapshot_id_watermark=snapshot.id,
        registration_captured_at_watermark=snapshot.captured_at,
    )
    session.add(candidate)
    session.flush()
    enrollment = ShadowPolicyEnrollment(
        enrollment_schema_version="shadow-policy-enrollment-v1",
        candidate_id=candidate.id,
        candidate_schema_version=candidate.candidate_schema_version,
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name=scenario,
        scenario_definition_signature=candidate.scenario_definition_signature,
        component_weights=candidate.component_weights,
        dataset_schema_version=candidate.dataset_schema_version,
        baseline_policy_signature=candidate.baseline_policy_signature,
        effective_top_n=1,
        candidate_registered_at=candidate.registered_at,
        candidate_registration_snapshot_id_watermark=snapshot.id,
        candidate_registration_captured_at_watermark=snapshot.captured_at,
        gate_result_type="POLICY_PROMOTION_GATE_V1_DECISION",
        gate_policy_schema_version="policy-promotion-gate-v1",
        gate_policy_signature=f"gate-{suffix}",
        gate_policy_definition={},
        gate_status="ELIGIBLE_FOR_REVIEW",
        gate_evaluated_at=now - timedelta(days=8),
        gate_forward_snapshot_id_ceiling=snapshot.id,
        gate_checks=[],
        gate_evidence_provenance={},
        gate_decision_signature=f"decision-{suffix}",
        shadow_enrolled_at=now - timedelta(days=7),
        shadow_snapshot_id_watermark=snapshot.id,
        shadow_captured_at_watermark=now - timedelta(days=7),
    )
    session.add(enrollment)
    session.flush()
    promotion = ShadowPolicyPromotionApproval(
        approval_schema_version="human-approved-promotion-v1",
        candidate_id=candidate.id,
        shadow_enrollment_id=enrollment.id,
        candidate_schema_version=candidate.candidate_schema_version,
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name=scenario,
        scenario_definition_signature=candidate.scenario_definition_signature,
        component_weights=candidate.component_weights,
        dataset_schema_version=candidate.dataset_schema_version,
        baseline_policy_signature=candidate.baseline_policy_signature,
        effective_top_n=1,
        shadow_enrolled_at=enrollment.shadow_enrolled_at,
        shadow_snapshot_id_watermark=snapshot.id,
        shadow_captured_at_watermark=enrollment.shadow_captured_at_watermark,
        pre_shadow_gate_decision_signature=enrollment.gate_decision_signature,
        review_result_type="SHADOW_REVIEW_GATE_V1_DECISION",
        review_policy_schema_version="shadow-review-gate-v1",
        review_policy_signature=f"review-policy-{suffix}",
        review_policy_definition={},
        review_status="ELIGIBLE_FOR_PROMOTION_REVIEW",
        review_evaluated_at=now - timedelta(days=6),
        review_decision_signature=f"review-decision-{suffix}",
        review_decision_payload={},
        performance_evidence_as_of=now - timedelta(days=6),
        shadow_evaluation_snapshot_id_ceiling=snapshot.id,
        review_checks=[],
        review_evidence_provenance={},
        approval_source="MANUAL_CLI",
        human_approved_at=now - timedelta(days=5),
        approval_signature=f"promotion-{suffix}",
    )
    session.add(promotion)
    session.flush()
    activation = LivePolicyCanaryActivation(
        canary_schema_version="limited-live-canary-v1a",
        canary_policy_definition={},
        canary_policy_definition_signature=f"canary-policy-definition-{suffix}",
        promotion_approval_id=promotion.id,
        promotion_approval_signature=promotion.approval_signature,
        candidate_id=candidate.id,
        shadow_enrollment_id=enrollment.id,
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name=scenario,
        scenario_definition_signature=candidate.scenario_definition_signature,
        component_weights=candidate.component_weights,
        dataset_schema_version=candidate.dataset_schema_version,
        baseline_policy_signature=candidate.baseline_policy_signature,
        canary_policy_signature=f"canary-policy-{suffix}",
        effective_top_n=1,
        activation_source="MANUAL_CLI",
        started_at=now - timedelta(days=4),
        expires_at=now - timedelta(days=2),
        max_analysis_runs=6,
        activation_signature=f"activation-{suffix}",
    )
    session.add(activation)
    session.flush()
    binding = LivePolicyCanarySafetyBinding(
        binding_schema_version="limited-live-canary-safety-binding-v1",
        canary_activation_id=activation.id,
        activation_signature=activation.activation_signature,
        promotion_approval_id=promotion.id,
        promotion_approval_signature=promotion.approval_signature,
        candidate_id=candidate.id,
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        order_safety_policy_schema_version="limited-live-canary-order-safety-v1",
        order_safety_policy_definition={},
        order_safety_policy_signature=f"order-safety-{suffix}",
        max_buy_order_amount_krw=10_000,
        daily_max_buy_amount_krw=30_000,
        bound_at=activation.started_at,
        binding_signature=f"binding-{suffix}",
    )
    session.add(binding)
    session.flush()
    return now, user, candidate, promotion, activation, binding


def _eligible_review(_now, user, candidate, promotion, activation, binding):
    payload = _payload()
    payload["activation_provenance"].update(
        canary_activation_id=activation.id,
        candidate_id=candidate.id,
        activation_signature=activation.activation_signature,
        user_id=user.id,
        exchange=activation.exchange,
        quote_asset=activation.quote_asset,
        scenario_name=activation.scenario_name,
        scenario_definition_signature=activation.scenario_definition_signature,
        baseline_policy_signature=activation.baseline_policy_signature,
        canary_policy_signature=activation.canary_policy_signature,
        effective_top_n=activation.effective_top_n,
    )
    payload["safety_binding_provenance"].update(
        safety_binding_id=binding.id,
        safety_binding_signature=binding.binding_signature,
    )
    payload["promotion_provenance"].update(
        promotion_approval_id=promotion.id,
        promotion_approval_signature=promotion.approval_signature,
    )
    report = replace(
        _report(payload=payload, activation_id=activation.id),
        candidate_id=candidate.id,
        evidence_signature="",
    )
    unsigned = report.as_dict()
    unsigned.pop("evidence_signature")
    report = replace(
        report, evidence_signature=live_canary_evidence_signature(unsigned)
    )
    source = SimpleNamespace(
        evaluate=lambda **_kwargs: report,
    )
    return LiveCanaryReviewGateService(
        SimpleNamespace(),
        evidence_service=source,
        now_fn=lambda: NOW,
    ).evaluate(canary_activation_id=activation.id)


def test_postgresql_approval_persistence_idempotency_and_stored_validation(
    monkeypatch,
):
    with engine.connect() as connection:
        transaction = connection.begin()
        sessions = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            with sessions() as setup:
                lineage = _persist_lineage(setup)
                setup.flush()
                setup.commit()
            now, user, candidate, promotion, activation, binding = lineage
            review = _eligible_review(
                now, user, candidate, promotion, activation, binding
            )
            validated = SimpleNamespace(
                row=promotion,
                candidate=SimpleNamespace(candidate_id=candidate.id),
            )
            monkeypatch.setattr(
                "crypto_trading_bot.services.full_live_policy_promotion_approval_service.validate_stored_canary_activation",
                lambda *_args: (validated, object(), object()),
            )
            monkeypatch.setattr(
                "crypto_trading_bot.services.full_live_policy_promotion_approval_service.load_and_validate_canary_safety_binding",
                lambda *_args: binding,
            )
            monkeypatch.setattr(
                "crypto_trading_bot.services.full_live_policy_promotion_approval_service.load_and_validate_canary_termination",
                lambda *_args: None,
            )
            review_service = SimpleNamespace(
                evaluate=lambda **_kwargs: review,
            )
            settings = Settings(
                _env_file=None,
                database_url="postgresql://test:test@localhost/test",
                market_universe_mode="DYNAMIC",
                market_universe_top_n=1,
            )

            def approval_factory(session):
                return FullLivePolicyPromotionApprovalService(
                    session,
                    settings=settings,
                    now_fn=lambda: review.evaluated_at + timedelta(seconds=1),
                )

            workflow = FullLivePolicyPromotionApprovalWorkflow(
                sessions,
                review_service_factory=lambda _session: review_service,
                approval_service_factory=approval_factory,
            )
            first = workflow.execute(
                canary_activation_id=activation.id,
                apply=True,
                interactive=True,
                confirmation_fn=lambda item: item.review_decision_signature,
            )
            assert first.approval_status == CREATED, first.safe_reason
            assert first.full_live_policy_activated is False
            second = workflow.execute(
                canary_activation_id=activation.id,
                apply=True,
                interactive=True,
                confirmation_fn=lambda item: item.review_decision_signature,
            )
            assert second.approval_status == ALREADY_APPROVED
            with sessions() as verify:
                assert (
                    verify.scalar(
                        select(func.count())
                        .select_from(FullLivePolicyPromotionApproval)
                        .where(
                            FullLivePolicyPromotionApproval.canary_activation_id
                            == activation.id
                        )
                    )
                    == 1
                )
                loaded = load_and_validate_full_live_policy_promotion_approval(
                    verify,
                    first.approval.id,
                    settings=settings,
                )
                assert loaded is not None
                assert (
                    loaded.row.approval_signature == first.approval.approval_signature
                )
                verify.rollback()
        finally:
            transaction.rollback()


def test_postgresql_noneligible_review_and_wrong_confirmation_write_no_rows():
    eligible = unit_eligible_review()
    noneligible = replace(
        eligible,
        status=INSUFFICIENT_DATA,
        eligible_for_full_live_review=False,
    )
    for review, confirmation, expected in (
        (noneligible, None, REVIEW_NOT_ELIGIBLE),
        (eligible, lambda _item: "wrong", CONFIRMATION_MISMATCH),
    ):
        with engine.connect() as connection:
            transaction = connection.begin()
            sessions = sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                before = connection.scalar(
                    select(func.count()).select_from(FullLivePolicyPromotionApproval)
                )
                review_service = SimpleNamespace(
                    evaluate=lambda **_kwargs: review,
                )
                result = FullLivePolicyPromotionApprovalWorkflow(
                    sessions,
                    review_service_factory=lambda _session: review_service,
                ).execute(
                    canary_activation_id=7,
                    apply=True,
                    interactive=True,
                    confirmation_fn=confirmation,
                )
                assert result.approval_status == expected
                after = connection.scalar(
                    select(func.count()).select_from(FullLivePolicyPromotionApproval)
                )
                assert after == before
            finally:
                transaction.rollback()
