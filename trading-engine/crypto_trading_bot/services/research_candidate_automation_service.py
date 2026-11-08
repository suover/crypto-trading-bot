"""DB-only orchestration of candidate generation, screening and registration."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    ResearchCandidateAutomationState,
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    CandidateGenerationError,
    DeterministicRankingCandidateGeneratorService,
)
from crypto_trading_bot.services.historical_candidate_screening_gate_service import (
    NO_CANDIDATES_TO_SCREEN,
    SCREENING_INSUFFICIENT_DATA,
)
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    BASELINE,
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.policy_promotion_gate_service import (
    POLICY_PROMOTION_GATE_V1,
)
from crypto_trading_bot.services.reference_bounded_historical_research_batch_service import (
    CANONICAL_INITIAL_RESEARCH_SIZE,
    CANONICAL_VALIDATION_SIZE,
)
from crypto_trading_bot.services.runtime_user_resolver import (
    RuntimeUserConfigurationError,
    RuntimeUserResolver,
)
from crypto_trading_bot.services.screening_gated_research_candidate_registration_service import (
    CREATED,
    INVALID_SCREENING_RESULT,
    NO_PASS_CANDIDATES,
    READY,
    REGISTRATION_CAP_EXCEEDED,
    REGISTRATION_CONFLICT,
    REGISTRATION_FAILED,
    STALE_REFERENCE,
    STALE_REGISTRATION_PLAN,
    STALE_REGISTRATION_STATE,
    ScreeningGatedResearchCandidateRegistrationService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
)


REVIEWED = frozenset(
    {
        "REGISTERED",
        "PREVIEW_READY",
        "NO_NOVEL_CANDIDATES",
        NO_PASS_CANDIDATES,
        REGISTRATION_CAP_EXCEEDED,
    }
)
ERRORS = frozenset(
    {
        "APPLY_DISABLED",
        "INVALID_RESEARCH_DATA",
        "EXECUTION_ERROR",
        STALE_REFERENCE,
        STALE_REGISTRATION_PLAN,
        STALE_REGISTRATION_STATE,
        REGISTRATION_CONFLICT,
    }
)


@dataclass
class ResearchCandidateAutomationResult:
    automation_enabled: bool
    auto_registration_enabled: bool
    apply: bool
    generator_step: Decimal
    registration_cap: int
    context_registration_cap: int
    report_type: str = "RESEARCH_CANDIDATE_AUTOMATION_CYCLE"
    status: str = "DISABLED"
    safe_reason: str | None = None
    reference_snapshot_id: int | None = None
    reference_snapshot_captured_at: datetime | None = None
    reference_snapshot_created_at: datetime | None = None
    new_snapshot_count: int = 0
    generated_candidate_count: int = 0
    already_registered_candidate_count: int = 0
    novel_candidate_count: int = 0
    screened_candidate_count: int = 0
    pass_candidate_count: int = 0
    fail_candidate_count: int = 0
    insufficient_candidate_count: int = 0
    invalid_candidate_count: int = 0
    registration_candidate_count: int = 0
    created_candidate_count: int = 0
    existing_registered_count: int | None = None
    registration_status: str | None = None
    registration_plan_signature: str | None = None
    registered_at: datetime | None = None
    database_write: bool = False
    state_write: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    live_orders_created: int = 0

    @classmethod
    def from_settings(cls, settings: Settings, *, apply: bool):
        return cls(
            settings.research_candidate_automation_enabled,
            settings.research_candidate_auto_registration_enabled,
            apply,
            settings.research_candidate_generation_step,
            settings.research_candidate_max_registrations_per_cycle,
            settings.research_candidate_max_registered_per_context,
        )


@dataclass(frozen=True)
class _Context:
    user_id: int
    exchange: str
    quote_asset: str
    signature: str
    top_n: int

    @property
    def key(self) -> tuple:
        return (
            self.user_id,
            self.exchange,
            self.quote_asset,
            DATASET_SCHEMA_VERSION,
            self.signature,
            self.top_n,
        )

    @property
    def snapshot_scope(self) -> tuple:
        row = StrategyReplaySnapshot
        return (
            row.user_id == self.user_id,
            row.exchange == self.exchange,
            row.quote_asset == self.quote_asset,
            row.dataset_schema_version == DATASET_SCHEMA_VERSION,
        )


class ResearchCandidateAutomationService:
    def __init__(
        self,
        session_factory,
        *,
        settings: Settings,
        registration_factory=ScreeningGatedResearchCandidateRegistrationService,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.registration_factory = registration_factory

    def run_cycle(
        self, *, apply: bool = False, scheduled: bool = False
    ) -> ResearchCandidateAutomationResult:
        settings = self.settings
        result = ResearchCandidateAutomationResult.from_settings(settings, apply=apply)
        if apply and not (
            result.automation_enabled and result.auto_registration_enabled
        ):
            result.status = "APPLY_DISABLED"
            result.safe_reason = "apply requires both research candidate enable flags"
            return result
        if scheduled and not result.automation_enabled:
            return result

        context = None
        with self.session_factory() as session:
            try:
                if not apply:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                user_id = (
                    RuntimeUserResolver(session)
                    .resolve_configured(settings.trading_user_id)
                    .id
                )
                exchange = settings.market_universe_exchange.strip().upper()
                quote = settings.market_universe_quote_asset.strip().upper()
                resolution = LiveRankingPolicyResolver(
                    session, settings=settings
                ).inspect(user_id=user_id, exchange=exchange, quote_asset=quote)
                if (
                    settings.market_universe_mode != "DYNAMIC"
                    or (exchange, quote) != ("UPBIT", "KRW")
                    or resolution.mode != BASELINE
                ):
                    result.status = "RUNTIME_CONTEXT_MISMATCH"
                    result.safe_reason = "DYNAMIC UPBIT KRW baseline context required"
                    return result
                context = _Context(
                    user_id,
                    exchange,
                    quote,
                    resolution.baseline_policy_signature,
                    settings.market_universe_top_n,
                )
                self._evaluate(
                    session,
                    context,
                    build_policy_data(settings, resolution.ranking_policy),
                    result,
                    scheduled=scheduled,
                )
                if result.status == "REGISTERED":
                    if not self._record(session, context, result):
                        raise RuntimeError("registration checkpoint is missing")
                    session.commit()
                    result.database_write = result.state_write = True
                    return result
                session.rollback()
            except (CandidateGenerationError, RuntimeUserConfigurationError) as error:
                session.rollback()
                result.status = "INVALID_RESEARCH_DATA"
                result.safe_reason = type(error).__name__
            except Exception as error:
                session.rollback()
                result.status = "EXECUTION_ERROR"
                result.safe_reason = type(error).__name__
                result.created_candidate_count = 0
                result.registered_at = None
                if result.registration_status == CREATED:
                    result.registration_status = REGISTRATION_FAILED

            # Periodic cycles remember diagnostics in a separate state-only
            # transaction after the evaluation/registration transaction rolls back.
            if (
                scheduled
                and context is not None
                and result.reference_snapshot_id is not None
                and result.status != "RUNTIME_CONTEXT_MISMATCH"
            ):
                try:
                    if self._record(session, context, result):
                        session.commit()
                        result.state_write = result.database_write = True
                except Exception as error:
                    session.rollback()
                    result.status = "EXECUTION_ERROR"
                    result.safe_reason = (
                        f"checkpoint write failed: {type(error).__name__}"
                    )
        return result

    def _evaluate(
        self,
        session: Session,
        context: _Context,
        policy_data: dict,
        result: ResearchCandidateAutomationResult,
        *,
        scheduled: bool,
    ) -> None:
        row = StrategyReplaySnapshot
        reference = session.scalar(
            select(row)
            .where(*context.snapshot_scope)
            .order_by(row.captured_at.desc(), row.id.desc())
            .limit(1)
        )
        if reference is None:
            result.status = "NO_NEW_SNAPSHOTS"
            return
        result.reference_snapshot_id = reference.id
        result.reference_snapshot_captured_at = reference.captured_at
        result.reference_snapshot_created_at = reference.created_at
        if reference.policy_signature != context.signature:
            result.status = "RUNTIME_CONTEXT_MISMATCH"
            result.safe_reason = (
                "latest replay snapshot does not match the runtime baseline"
            )
            return
        now = datetime.now(UTC)
        if (
            reference.id < 1
            or reference.policy_data != policy_data
            or reference.captured_at.utcoffset() is None
            or reference.created_at.utcoffset() is None
            or max(reference.captured_at, reference.created_at) > now
        ):
            result.status = "INVALID_RESEARCH_DATA"
            result.safe_reason = "invalid reference policy or time boundary"
            return

        state = session.get(ResearchCandidateAutomationState, context.key)
        compatible = (
            *context.snapshot_scope,
            row.policy_signature == context.signature,
            row.policy_data == policy_data,
            row.id <= reference.id,
            row.captured_at <= reference.captured_at,
            row.created_at <= reference.created_at,
            row.prefilter_candidate_count >= context.top_n,
            row.ranked_candidate_count >= context.top_n,
        )
        qualified = select(row.id).where(
            *compatible,
            select(func.count())
            .select_from(StrategyReplayCandidate)
            .where(*self._rankable_rows(context))
            .correlate(row)
            .scalar_subquery()
            >= context.top_n,
        )
        if state is None:
            if session.scalar(qualified.where(row.id == reference.id)) is None:
                result.status = "INSUFFICIENT_RESEARCH_DATA"
                result.safe_reason = "latest reference has no complete ranking pool"
                return
            result.status = "BOOTSTRAPPED" if scheduled else "BOOTSTRAP_REQUIRED"
            result.safe_reason = "scheduled worker anchors the initial growth window at the latest compatible snapshot"
            return
        if (
            reference.id < state.last_observed_snapshot_id
            or reference.captured_at < state.reviewed_captured_at
            or reference.created_at < state.reviewed_created_at
        ):
            result.status = STALE_REFERENCE
            result.safe_reason = "reference is behind the recorded checkpoint"
            return
        new = qualified.where(
            row.id > state.reviewed_snapshot_id,
            row.captured_at > state.reviewed_captured_at,
            row.created_at > state.reviewed_created_at,
        )
        result.new_snapshot_count = session.execute(
            select(func.count()).select_from(new.subquery())
        ).scalar_one()
        if (
            state.last_reference_snapshot_id == reference.id
            and state.last_status == "INSUFFICIENT_RESEARCH_DATA"
        ):
            result.status = "NO_NEW_SNAPSHOTS"
            result.safe_reason = "insufficient evidence needs a newer reference cutoff"
            return

        pending_apply = result.apply and state.review_status == "PREVIEW_READY"
        cap_changed = state.review_status == REGISTRATION_CAP_EXCEEDED and (
            state.review_cycle_cap != result.registration_cap
            or state.review_context_cap != result.context_registration_cap
        )
        if not (
            pending_apply
            or cap_changed
            or result.new_snapshot_count
            >= self.settings.research_candidate_min_new_snapshots
        ):
            result.status = "NO_NEW_SNAPSHOTS"
            result.safe_reason = "compatible new snapshot minimum not met"
            return
        if session.scalar(qualified.where(row.id == reference.id)) is None:
            result.status = "INSUFFICIENT_RESEARCH_DATA"
            result.safe_reason = "latest reference has no complete ranking pool"
            return

        generated = DeterministicRankingCandidateGeneratorService(session).generate(
            reference_snapshot_id=reference.id, step=result.generator_step
        )
        result.generated_candidate_count = generated.generated_valid_count
        result.already_registered_candidate_count = generated.already_registered_count
        result.novel_candidate_count = generated.novel_candidate_count
        if not generated.novel_candidate_count:
            result.status = "NO_NOVEL_CANDIDATES"
            return
        if not self._has_mature_outcomes(session, context, reference, compatible):
            result.status = "INSUFFICIENT_RESEARCH_DATA"
            result.safe_reason = (
                "not enough complete outcomes as of the reference cutoff"
            )
            return

        registration = self.registration_factory(session)
        preview = registration.preview(
            reference_snapshot_id=reference.id, step=result.generator_step
        )
        result.registration_status = preview.status
        result.registration_plan_signature = preview.registration_plan_signature
        result.screened_candidate_count = preview.screened_candidate_count
        result.pass_candidate_count = preview.pass_candidate_count
        result.fail_candidate_count = preview.fail_candidate_count
        result.insufficient_candidate_count = preview.insufficient_candidate_count
        result.invalid_candidate_count = preview.invalid_candidate_count
        result.registration_candidate_count = preview.registration_candidate_count
        if preview.status != READY:
            self._handle_registration_status(preview, result)
            return

        plan = preview.registration_plan
        if (
            plan is None
            or not preview.registration_plan_signature
            or plan.reference_snapshot_id != reference.id
            or (
                plan.user_id,
                plan.exchange,
                plan.quote_asset,
                plan.dataset_schema_version,
                plan.baseline_policy_signature,
                plan.effective_top_n,
            )
            != context.key
            or not (0 < len(plan.candidates) == preview.registration_candidate_count)
        ):
            result.status = "INVALID_RESEARCH_DATA"
            result.safe_reason = (
                "registration preview does not match the selected context"
            )
            return
        result.existing_registered_count = registration.registered_count(plan)
        if (
            len(plan.candidates) > result.registration_cap
            or result.existing_registered_count + len(plan.candidates)
            > result.context_registration_cap
        ):
            result.status = REGISTRATION_CAP_EXCEEDED
            result.safe_reason = "whole plan would exceed a registration cap"
            return
        if not result.apply:
            result.status = "PREVIEW_READY"
            return

        applied = registration.apply(
            reference_snapshot_id=reference.id,
            step=result.generator_step,
            expected_plan_signature=preview.registration_plan_signature,
            max_registered_per_context=result.context_registration_cap,
        )
        result.registration_status = applied.status
        if applied.status != CREATED:
            self._handle_registration_status(applied, result)
            # A READY preview followed by no work is a change in registration state.
            if result.status in {
                "NO_NOVEL_CANDIDATES",
                NO_PASS_CANDIDATES,
                "INSUFFICIENT_RESEARCH_DATA",
            }:
                result.status = STALE_REGISTRATION_STATE
            return
        if (
            applied.registration_plan_signature != preview.registration_plan_signature
            or applied.created_candidate_count != len(plan.candidates)
            or not applied.database_write
        ):
            result.registration_status = REGISTRATION_FAILED
            result.status = "INVALID_RESEARCH_DATA"
            result.safe_reason = "registration result does not match the signed plan"
            return
        resolution = LiveRankingPolicyResolver(session, settings=self.settings).inspect(
            user_id=context.user_id,
            exchange=context.exchange,
            quote_asset=context.quote_asset,
        )
        if (
            resolution.mode != BASELINE
            or resolution.effective_policy_signature != context.signature
        ):
            result.registration_status = STALE_REFERENCE
            result.status = STALE_REFERENCE
            result.safe_reason = "runtime ranking changed during registration"
            return
        result.status = "REGISTERED"
        result.created_candidate_count = applied.created_candidate_count
        result.registered_at = applied.shared_registered_at

    @staticmethod
    def _rankable_rows(context: _Context) -> tuple:
        row = StrategyReplaySnapshot
        candidate = StrategyReplayCandidate
        return (
            candidate.strategy_replay_snapshot_id == row.id,
            candidate.analysis_run_id == row.analysis_run_id,
            candidate.user_id == context.user_id,
            candidate.exchange == context.exchange,
            candidate.quote_asset == context.quote_asset,
            candidate.in_prefilter.is_(True),
            candidate.buy_eligible.is_(True),
            candidate.feature_data["enough_candles"].as_boolean().is_(True),
            candidate.original_rank.is_not(None),
        )

    def _has_mature_outcomes(self, session, context, reference, compatible) -> bool:
        # A necessary lower bound only; the existing screening policy still
        # validates exact selection, windows, coverage, prices and chronology.
        row = StrategyReplaySnapshot
        candidate = StrategyReplayCandidate
        outcome = StrategyReplayCandidateOutcome
        policy = POLICY_PROMOTION_GATE_V1
        minimum = CANONICAL_INITIAL_RESEARCH_SIZE + CANONICAL_VALIDATION_SIZE * max(
            policy.min_historical_gross_fold_count,
            policy.min_historical_cost_fold_count,
        )
        cutoff = reference.created_at
        pools = (
            select(row.id, outcome.horizon_minutes)
            .select_from(row)
            .join(candidate, candidate.strategy_replay_snapshot_id == row.id)
            .join(outcome, outcome.strategy_replay_candidate_id == candidate.id)
            .where(
                *compatible,
                *self._rankable_rows(context),
                outcome.strategy_replay_snapshot_id == row.id,
                outcome.user_id == context.user_id,
                outcome.exchange == context.exchange,
                outcome.market == candidate.market,
                outcome.snapshot_at == row.captured_at,
                outcome.horizon_minutes.in_(policy.required_horizons),
                outcome.evaluation_status == "COMPLETE",
                outcome.target_at <= cutoff,
                outcome.evaluated_at <= cutoff,
                outcome.created_at <= cutoff,
                outcome.updated_at <= cutoff,
            )
            .group_by(row.id, outcome.horizon_minutes)
            .having(func.count() >= context.top_n)
            .subquery()
        )
        counts = dict(
            session.execute(
                select(pools.c.horizon_minutes, func.count()).group_by(
                    pools.c.horizon_minutes
                )
            ).all()
        )
        return all(
            counts.get(horizon, 0) >= minimum for horizon in policy.required_horizons
        )

    @staticmethod
    def _handle_registration_status(value, result) -> None:
        if value.screening_status == NO_CANDIDATES_TO_SCREEN:
            result.status = "NO_NOVEL_CANDIDATES"
        elif value.screening_status == SCREENING_INSUFFICIENT_DATA or (
            value.status == NO_PASS_CANDIDATES and value.insufficient_candidate_count
        ):
            result.status = "INSUFFICIENT_RESEARCH_DATA"
        elif value.status == INVALID_SCREENING_RESULT:
            result.status = "INVALID_RESEARCH_DATA"
        elif value.status == REGISTRATION_FAILED:
            result.status = "EXECUTION_ERROR"
        else:
            result.status = value.status
        # Upstream exceptions can include connection parameters or stored data.
        result.safe_reason = f"registration status: {value.status}"

    @staticmethod
    def _record(
        session: Session, context: _Context, result: ResearchCandidateAutomationResult
    ) -> bool:
        state = session.get(ResearchCandidateAutomationState, context.key)
        if state is None:
            if result.status != "BOOTSTRAPPED":
                return False
            state = ResearchCandidateAutomationState(
                user_id=context.user_id,
                exchange=context.exchange,
                quote_asset=context.quote_asset,
                dataset_schema_version=DATASET_SCHEMA_VERSION,
                baseline_policy_signature=context.signature,
                effective_top_n=context.top_n,
            )
            session.add(state)
        observed = result.reference_snapshot_id
        observed_new = state.last_observed_snapshot_id is None or (
            observed > state.last_observed_snapshot_id
        )
        if observed_new:
            state.last_observed_snapshot_id = observed
        if result.status == "NO_NEW_SNAPSHOTS":
            return observed_new
        if result.status != "BOOTSTRAPPED":
            state.last_reference_snapshot_id = observed
            state.last_status = result.status
            state.last_attempt_at = datetime.now(UTC)
        if result.status in REVIEWED or result.status == "BOOTSTRAPPED":
            state.reviewed_snapshot_id = observed
            state.reviewed_captured_at = result.reference_snapshot_captured_at
            state.reviewed_created_at = result.reference_snapshot_created_at
            state.review_status = result.status
            state.review_cycle_cap = result.registration_cap
            state.review_context_cap = result.context_registration_cap
        if result.apply and result.registration_status is not None:
            state.last_registration_status = result.status
        if result.status == "REGISTERED":
            state.last_registered_snapshot_id = observed
            state.last_registered_at = result.registered_at
        return True
