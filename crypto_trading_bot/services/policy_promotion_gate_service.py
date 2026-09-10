from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.candidate_registration_bounded_historical_evidence_service import (
    INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE,
    NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS,
    SUCCESS as HISTORICAL_SUCCESS,
    CandidateRegistrationBoundedHistoricalEvidenceResult,
    CandidateRegistrationBoundedHistoricalEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_cost_adjusted_evidence_service import (
    INVALID_FORWARD_COST_ADJUSTED_EVIDENCE,
    SUCCESS as FORWARD_COST_SUCCESS,
    ForwardCandidateCostAdjustedEvidenceResult,
    ForwardCandidateCostAdjustedEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    INVALID_FORWARD_EVIDENCE,
    SUCCESS as FORWARD_GROSS_SUCCESS,
    ForwardCandidateGrossEvidenceResult,
    ForwardCandidateGrossEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    InvalidForwardCandidateProvenance,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INVALID_FORWARD_TURNOVER,
    SUCCESS as FORWARD_TURNOVER_SUCCESS,
    ForwardCandidateTurnoverEvidenceResult,
    ForwardCandidateTurnoverEvidenceService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


RESULT_TYPE = "POLICY_PROMOTION_GATE_V1_DECISION"
PASS = "PASS"
INSUFFICIENT = "INSUFFICIENT"
FAIL = "FAIL"
INVALID = "INVALID"
INVALID_PROMOTION_DATA = "INVALID_PROMOTION_DATA"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"


@dataclass(frozen=True)
class PolicyPromotionGatePolicy:
    schema_version: str
    required_horizons: tuple[int, ...]
    historical_initial_research_size: int
    historical_validation_size: int
    fee_rate: Decimal
    spread_cost_rate: Decimal
    slippage_rate: Decimal
    min_historical_gross_fold_count: int
    min_historical_cost_fold_count: int
    min_historical_cost_adjustable_coverage: Decimal
    min_historical_supportive_horizons: int
    historical_supportive_positive_rate: Decimal
    historical_catastrophic_mean_delta_floor: Decimal
    min_forward_successful_gross_snapshots: int
    min_forward_turnover_transitions: int
    min_forward_cost_adjustable_snapshots: int
    min_forward_cost_adjustable_coverage: Decimal
    min_forward_observation_span_hours: int
    min_forward_mean_gross_delta: Decimal
    min_forward_mean_cost_adjusted_delta: Decimal
    min_forward_median_cost_adjusted_delta: Decimal
    min_forward_cost_adjusted_win_rate: Decimal
    max_forward_continuity_break_count: int


POLICY_PROMOTION_GATE_V1 = PolicyPromotionGatePolicy(
    schema_version="policy-promotion-gate-v1",
    required_horizons=(60, 240, 1440),
    historical_initial_research_size=2,
    historical_validation_size=1,
    fee_rate=Decimal("0.0005"),
    spread_cost_rate=Decimal("0.0005"),
    slippage_rate=Decimal("0.001"),
    min_historical_gross_fold_count=5,
    min_historical_cost_fold_count=5,
    min_historical_cost_adjustable_coverage=Decimal("0.70"),
    min_historical_supportive_horizons=2,
    historical_supportive_positive_rate=Decimal("0.50"),
    historical_catastrophic_mean_delta_floor=Decimal("-0.50"),
    min_forward_successful_gross_snapshots=21,
    min_forward_turnover_transitions=20,
    min_forward_cost_adjustable_snapshots=20,
    min_forward_cost_adjustable_coverage=Decimal("0.80"),
    min_forward_observation_span_hours=168,
    min_forward_mean_gross_delta=Decimal("0"),
    min_forward_mean_cost_adjusted_delta=Decimal("0"),
    min_forward_median_cost_adjusted_delta=Decimal("0"),
    min_forward_cost_adjusted_win_rate=Decimal("0.55"),
    max_forward_continuity_break_count=0,
)


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def validate_gate_policy(policy: PolicyPromotionGatePolicy) -> None:
    if not isinstance(policy, PolicyPromotionGatePolicy):
        raise ReplayInputError("gate policy is invalid")
    if policy.schema_version != "policy-promotion-gate-v1":
        raise ReplayInputError("gate policy schema is unsupported")
    if policy.required_horizons != tuple(sorted(set(policy.required_horizons))) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in policy.required_horizons
    ):
        raise ReplayInputError("gate required horizons are invalid")
    decimal_fields = (
        "fee_rate",
        "spread_cost_rate",
        "slippage_rate",
        "min_historical_cost_adjustable_coverage",
        "historical_supportive_positive_rate",
        "historical_catastrophic_mean_delta_floor",
        "min_forward_mean_gross_delta",
        "min_forward_mean_cost_adjusted_delta",
        "min_forward_median_cost_adjusted_delta",
        "min_forward_cost_adjusted_win_rate",
        "min_forward_cost_adjustable_coverage",
    )
    for name in decimal_fields:
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, Decimal):
            raise ReplayInputError(f"gate policy {name} must be Decimal")
        if not value.is_finite():
            raise ReplayInputError(f"gate policy {name} must be finite")
    positive_counts = (
        policy.historical_initial_research_size,
        policy.historical_validation_size,
        policy.min_historical_gross_fold_count,
        policy.min_historical_cost_fold_count,
        policy.min_historical_supportive_horizons,
        policy.min_forward_successful_gross_snapshots,
        policy.min_forward_turnover_transitions,
        policy.min_forward_cost_adjustable_snapshots,
        policy.min_forward_observation_span_hours,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in positive_counts
    ):
        raise ReplayInputError("gate policy count thresholds must be positive integers")
    if (
        isinstance(policy.max_forward_continuity_break_count, bool)
        or not isinstance(policy.max_forward_continuity_break_count, int)
        or policy.max_forward_continuity_break_count < 0
    ):
        raise ReplayInputError("gate continuity threshold is invalid")
    rates = (
        policy.fee_rate,
        policy.spread_cost_rate,
        policy.slippage_rate,
        policy.min_historical_cost_adjustable_coverage,
        policy.historical_supportive_positive_rate,
        policy.min_forward_cost_adjustable_coverage,
        policy.min_forward_cost_adjusted_win_rate,
    )
    if any(value < 0 or value > 1 for value in rates):
        raise ReplayInputError(
            "gate policy rate thresholds must be between zero and one"
        )
    if policy.min_historical_supportive_horizons > len(policy.required_horizons):
        raise ReplayInputError(
            "gate supportive horizon threshold exceeds required horizons"
        )


def gate_policy_signature(policy: PolicyPromotionGatePolicy) -> str:
    validate_gate_policy(policy)
    values = asdict(policy)
    canonical = {
        key: (
            _canonical_decimal(value)
            if isinstance(value, Decimal)
            else list(value)
            if isinstance(value, tuple)
            else value
        )
        for key, value in values.items()
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return f"{policy.schema_version}:{sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class PromotionGateCheckResult:
    check_id: str
    category: str
    status: str
    horizon_minutes: int | None
    observed_value: str | None
    comparator: str | None
    threshold_value: str | None
    reason: str | None


@dataclass(frozen=True)
class PolicyPromotionGateResult:
    candidate_id: int
    candidate: ForwardCandidateMetadata | None
    gate_policy_schema_version: str
    gate_policy_signature: str
    gate_policy: PolicyPromotionGatePolicy
    evaluated_at: datetime
    forward_snapshot_id_ceiling: int
    status: str
    safe_reason: str | None
    passed_checks: tuple[PromotionGateCheckResult, ...]
    insufficient_checks: tuple[PromotionGateCheckResult, ...]
    failed_checks: tuple[PromotionGateCheckResult, ...]
    invalid_checks: tuple[PromotionGateCheckResult, ...]
    all_checks: tuple[PromotionGateCheckResult, ...]
    historical: CandidateRegistrationBoundedHistoricalEvidenceResult | None
    forward_gross: ForwardCandidateGrossEvidenceResult | None
    forward_turnover: ForwardCandidateTurnoverEvidenceResult | None
    forward_cost_adjusted: ForwardCandidateCostAdjustedEvidenceResult | None
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    promotion_performed: bool
    shadow_policy_created: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool


class _InvalidGateData(Exception):
    pass


class PolicyPromotionGateService:
    def __init__(
        self,
        session: Session,
        *,
        policy: PolicyPromotionGatePolicy = POLICY_PROMOTION_GATE_V1,
        historical_service=None,
        forward_gross_service=None,
        forward_turnover_service=None,
        forward_cost_service=None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        validate_gate_policy(policy)
        self.session = session
        self.policy = policy
        self.historical_service = (
            historical_service
            or CandidateRegistrationBoundedHistoricalEvidenceService(session)
        )
        self.forward_gross_service = (
            forward_gross_service or ForwardCandidateGrossEvidenceService(session)
        )
        self.forward_turnover_service = (
            forward_turnover_service or ForwardCandidateTurnoverEvidenceService(session)
        )
        self.forward_cost_service = (
            forward_cost_service or ForwardCandidateCostAdjustedEvidenceService(session)
        )
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def evaluate(self, *, candidate_id: int) -> PolicyPromotionGateResult:
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        evaluated_at = self.now_fn()
        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ReplayInputError("gate evaluated_at must be timezone-aware")
        evaluated_at = evaluated_at.astimezone(UTC)
        try:
            validated = load_and_validate_forward_candidate(self.session, candidate_id)
        except InvalidForwardCandidateProvenance as error:
            return self._result(
                None,
                evaluated_at,
                0,
                [
                    self._check(
                        "CANDIDATE_METADATA_ALIGNMENT",
                        "INTEGRITY",
                        INVALID,
                        reason=str(error),
                    )
                ],
                None,
                None,
                None,
                None,
                candidate_id=candidate_id,
            )
        ceiling = self._forward_ceiling(validated.metadata)
        policy = self.policy
        historical = self.historical_service.evaluate(
            candidate_id=candidate_id,
            horizons=policy.required_horizons,
            initial_research_size=policy.historical_initial_research_size,
            validation_size=policy.historical_validation_size,
            fee_rate=policy.fee_rate,
            spread_cost_rate=policy.spread_cost_rate,
            slippage_rate=policy.slippage_rate,
        )
        gross = self.forward_gross_service.evaluate(
            candidate_id=candidate_id,
            horizons=policy.required_horizons,
            snapshot_id_ceiling=ceiling,
        )
        turnover = self.forward_turnover_service.evaluate(
            candidate_id=candidate_id, snapshot_id_ceiling=ceiling
        )
        cost = self.forward_cost_service.evaluate_from_results(
            gross,
            turnover,
            fee_rate=policy.fee_rate,
            spread_cost_rate=policy.spread_cost_rate,
            slippage_rate=policy.slippage_rate,
        )
        return self.evaluate_from_results(
            historical,
            gross,
            turnover,
            cost,
            forward_snapshot_id_ceiling=ceiling,
            evaluated_at=evaluated_at,
        )

    def _forward_ceiling(self, candidate: ForwardCandidateMetadata) -> int:
        value = self.session.scalar(
            select(func.max(StrategyReplaySnapshot.id))
            .where(
                StrategyReplaySnapshot.user_id == candidate.user_id,
                StrategyReplaySnapshot.exchange == candidate.exchange,
                StrategyReplaySnapshot.quote_asset == candidate.quote_asset,
                StrategyReplaySnapshot.dataset_schema_version
                == candidate.dataset_schema_version,
                StrategyReplaySnapshot.id
                > candidate.registration_snapshot_id_watermark,
                StrategyReplaySnapshot.captured_at > candidate.registered_at,
                StrategyReplaySnapshot.captured_at
                > candidate.registration_captured_at_watermark,
            )
            .execution_options(autoflush=False)
        )
        return (
            value if value is not None else candidate.registration_snapshot_id_watermark
        )

    def evaluate_from_results(
        self,
        historical,
        gross,
        turnover,
        cost,
        *,
        forward_snapshot_id_ceiling: int,
        evaluated_at: datetime,
    ) -> PolicyPromotionGateResult:
        checks: list[PromotionGateCheckResult] = []
        try:
            candidate = self._integrity_checks(
                historical, gross, turnover, cost, forward_snapshot_id_ceiling, checks
            )
            self._evidence_checks(historical, gross, turnover, cost, checks)
        except (_InvalidGateData, AttributeError, TypeError, InvalidOperation) as error:
            checks.append(
                self._check(
                    "UPSTREAM_EVIDENCE_INTEGRITY",
                    "INTEGRITY",
                    INVALID,
                    reason=str(error),
                )
            )
            candidate = historical.candidate if historical is not None else None
        return self._result(
            candidate,
            evaluated_at,
            forward_snapshot_id_ceiling,
            checks,
            historical,
            gross,
            turnover,
            cost,
            candidate_id=(historical.candidate_id if historical is not None else None),
        )

    def _integrity_checks(self, historical, gross, turnover, cost, ceiling, checks):
        if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 1:
            raise _InvalidGateData("forward snapshot ID ceiling is invalid")
        invalid_statuses = (
            historical.status == INVALID_REGISTRATION_BOUNDED_HISTORICAL_EVIDENCE,
            gross.status == INVALID_FORWARD_EVIDENCE,
            turnover.status == INVALID_FORWARD_TURNOVER,
            cost.status == INVALID_FORWARD_COST_ADJUSTED_EVIDENCE,
        )
        candidates = (
            historical.candidate,
            gross.candidate,
            turnover.candidate,
            cost.candidate,
        )
        aligned = (
            not any(invalid_statuses)
            and candidates[0] is not None
            and all(value == candidates[0] for value in candidates)
        )
        checks.append(
            self._check(
                "CANDIDATE_METADATA_ALIGNMENT",
                "INTEGRITY",
                PASS if aligned else INVALID,
            )
        )
        if not aligned:
            raise _InvalidGateData("candidate metadata or upstream status is invalid")
        candidate = candidates[0]
        historical_flags = (
            historical.candidate_registration_verified,
            historical.registration_time_evidence_enforced,
            historical.post_registration_snapshots_excluded,
            historical.post_registration_outcomes_excluded,
            historical.snapshot_created_at_cutoff_enforced,
            historical.outcome_evaluated_at_cutoff_enforced,
            historical.outcome_created_at_cutoff_enforced,
            historical.outcome_updated_at_cutoff_enforced,
            historical.historical_forward_snapshot_disjointness_verified,
        )
        provenance = (
            all(historical_flags)
            and historical.historical_evidence_as_of == candidate.registered_at
            and historical.historical_strict_unseen_validation == "not_verified"
        )
        checks.append(
            self._check(
                "HISTORICAL_REGISTRATION_PROVENANCE",
                "INTEGRITY",
                PASS if provenance else INVALID,
            )
        )
        forward_flags = all(
            (
                gross.candidate_registration_verified,
                gross.forward_anchor_enforced,
                gross.pre_registration_snapshots_excluded,
                gross.registration_time_provenance_verified,
                gross.future_snapshot_cutoff_verified,
                gross.scenario_definition_frozen_at_registration,
                gross.forward_validation_performed,
                turnover.candidate_registration_verified,
                turnover.forward_anchor_enforced,
                turnover.pre_registration_snapshots_excluded,
                turnover.pre_registration_transition_excluded,
                turnover.forward_continuity_enforced,
                turnover.first_forward_snapshot_has_no_prior_forward_transition,
                cost.candidate_registration_verified,
                cost.forward_anchor_enforced,
                cost.pre_registration_snapshots_excluded,
                cost.pre_registration_transition_excluded,
                cost.forward_continuity_enforced,
                cost.cost_model_reused,
            )
        )
        if not provenance or not forward_flags:
            raise _InvalidGateData("evidence provenance flags are incomplete")
        historical_values = historical.historical_candidate_snapshot_ids
        gross_values = gross.eligible_forward_snapshot_ids
        turnover_values = turnover.forward_timeline_snapshot_ids
        if any(
            len(values) != len(set(values))
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in values
            )
            for values in (historical_values, gross_values, turnover_values)
        ):
            raise _InvalidGateData("snapshot ID evidence is invalid")
        historical_ids = set(historical_values)
        forward_ids = set(gross_values) | set(turnover_values)
        boundary = (
            all(
                value <= candidate.registration_snapshot_id_watermark
                for value in historical_ids
            )
            and all(
                candidate.registration_snapshot_id_watermark < value <= ceiling
                for value in forward_ids
            )
            and historical_ids.isdisjoint(forward_ids)
        )
        checks.append(
            self._check(
                "HISTORICAL_FORWARD_DISJOINTNESS",
                "INTEGRITY",
                PASS if boundary else INVALID,
            )
        )
        assumptions = (historical.assumptions, cost.assumptions)
        cost_aligned = all(
            value.fee_rate == self.policy.fee_rate
            and value.spread_cost_rate == self.policy.spread_cost_rate
            and value.slippage_rate == self.policy.slippage_rate
            for value in assumptions
        )
        forward_cost_aligned = (
            cost.gross_status == gross.status
            and cost.turnover_status == turnover.status
            and cost.gross_forward_snapshot_ids == gross.eligible_forward_snapshot_ids
            and cost.turnover_transition_count == turnover.transition_count
            and cost.continuity_break_count == turnover.continuity_break_count
        )
        checks.append(
            self._check(
                "COST_ASSUMPTIONS_ALIGNMENT",
                "INTEGRITY",
                PASS if cost_aligned and forward_cost_aligned else INVALID,
            )
        )
        horizons = (
            historical.requested_horizons,
            gross.requested_horizons,
            cost.requested_horizons,
        )
        horizon_aligned = all(
            value == self.policy.required_horizons for value in horizons
        )
        checks.append(
            self._check(
                "REQUIRED_HORIZONS_ALIGNMENT",
                "INTEGRITY",
                PASS if horizon_aligned else INVALID,
            )
        )
        if (
            not boundary
            or not cost_aligned
            or not forward_cost_aligned
            or not horizon_aligned
        ):
            raise _InvalidGateData(
                "registration bounds, costs, or horizons do not align"
            )
        return candidate

    def _evidence_checks(self, historical, gross, turnover, cost, checks):
        policy = self.policy
        if historical.status == NO_REGISTRATION_BOUNDED_HISTORICAL_SNAPSHOTS:
            checks.append(
                self._check(
                    "HISTORICAL_EVIDENCE_AVAILABLE", "SUFFICIENCY", INSUFFICIENT
                )
            )
            return
        if historical.status != HISTORICAL_SUCCESS:
            raise _InvalidGateData("historical evidence status is unsupported")
        safe_forward = (
            gross.status != FORWARD_GROSS_SUCCESS
            or turnover.status != FORWARD_TURNOVER_SUCCESS
            or cost.status != FORWARD_COST_SUCCESS
        )
        sufficiency: list[PromotionGateCheckResult] = []
        stability: list[PromotionGateCheckResult] = []
        performance: list[PromotionGateCheckResult] = []
        if safe_forward:
            sufficiency.append(
                self._check("FORWARD_EVIDENCE_AVAILABLE", "SUFFICIENCY", INSUFFICIENT)
            )
        gross_historical = self._cohorts(historical.gross_robustness.cohorts)
        cost_historical = self._cohorts(historical.cost_robustness.cohorts)
        gross_forward = {item.horizon_minutes: item for item in gross.horizons}
        cost_forward = {item.horizon_minutes: item for item in cost.horizons}
        if (
            set(gross_historical) != set(policy.required_horizons)
            or set(cost_historical) != set(policy.required_horizons)
            or set(gross_forward) != set(policy.required_horizons)
            or set(cost_forward) != set(policy.required_horizons)
        ):
            raise _InvalidGateData("required horizon evidence is missing or duplicated")
        supportive = 0
        catastrophic_values = []
        for horizon in policy.required_horizons:
            hg = gross_historical[horizon]
            hc = cost_historical[horizon]
            fg = gross_forward[horizon]
            fc = cost_forward[horizon]
            self._threshold(
                sufficiency,
                "HISTORICAL_GROSS_FOLD_COUNT",
                "SUFFICIENCY",
                horizon,
                hg.fold_count,
                ">=",
                policy.min_historical_gross_fold_count,
            )
            self._threshold(
                sufficiency,
                "HISTORICAL_COST_FOLD_COUNT",
                "SUFFICIENCY",
                horizon,
                hc.fold_count,
                ">=",
                policy.min_historical_cost_fold_count,
            )
            self._threshold(
                sufficiency,
                "HISTORICAL_COST_COVERAGE",
                "SUFFICIENCY",
                horizon,
                hc.cost_adjustable_coverage_rate,
                ">=",
                policy.min_historical_cost_adjustable_coverage,
            )
            stats = (
                hc.scenario_results[0].fold_statistics.statistics
                if hc.scenario_results
                else None
            )
            mean = stats.mean_delta if stats else None
            positive_rate = stats.positive_rate if stats else None
            if stats is None and hc.fold_count >= policy.min_historical_cost_fold_count:
                raise _InvalidGateData("historical stability statistics are missing")
            if any(
                value is not None and not self._valid_number(value)
                for value in (mean, positive_rate)
            ) or (
                positive_rate is not None
                and (positive_rate < Decimal("0") or positive_rate > Decimal("1"))
            ):
                raise _InvalidGateData("historical stability statistics are invalid")
            if (
                mean is not None
                and positive_rate is not None
                and mean > 0
                and positive_rate >= policy.historical_supportive_positive_rate
            ):
                supportive += 1
            catastrophic_values.append(mean)
            self._threshold(
                sufficiency,
                "FORWARD_GROSS_SAMPLE_COUNT",
                "SUFFICIENCY",
                horizon,
                fg.successful_comparable_snapshot_count,
                ">=",
                policy.min_forward_successful_gross_snapshots,
            )
            self._threshold(
                sufficiency,
                "FORWARD_COST_SAMPLE_COUNT",
                "SUFFICIENCY",
                horizon,
                fc.cost_adjustable_forward_snapshot_count,
                ">=",
                policy.min_forward_cost_adjustable_snapshots,
            )
            self._threshold(
                sufficiency,
                "FORWARD_COST_COVERAGE",
                "SUFFICIENCY",
                horizon,
                fc.cost_adjustable_coverage_rate,
                ">=",
                policy.min_forward_cost_adjustable_coverage,
            )
            self._performance(
                performance,
                "FORWARD_GROSS_MEAN_DELTA",
                horizon,
                fg.mean_return_delta,
                ">=",
                policy.min_forward_mean_gross_delta,
                fg.successful_comparable_snapshot_count
                >= policy.min_forward_successful_gross_snapshots,
            )
            self._performance(
                performance,
                "FORWARD_COST_MEAN_DELTA",
                horizon,
                fc.mean_cost_adjusted_return_delta,
                ">",
                policy.min_forward_mean_cost_adjusted_delta,
                fc.cost_adjustable_forward_snapshot_count
                >= policy.min_forward_cost_adjustable_snapshots,
            )
            self._performance(
                performance,
                "FORWARD_COST_MEDIAN_DELTA",
                horizon,
                fc.median_cost_adjusted_return_delta,
                ">=",
                policy.min_forward_median_cost_adjusted_delta,
                fc.cost_adjustable_forward_snapshot_count
                >= policy.min_forward_cost_adjustable_snapshots,
            )
            self._performance(
                performance,
                "FORWARD_COST_WIN_RATE",
                horizon,
                fc.cost_adjusted_win_rate,
                ">=",
                policy.min_forward_cost_adjusted_win_rate,
                fc.cost_adjustable_forward_snapshot_count
                >= policy.min_forward_cost_adjustable_snapshots,
            )
        self._threshold(
            stability,
            "HISTORICAL_SUPPORTIVE_HORIZONS",
            "HISTORICAL_STABILITY",
            None,
            supportive,
            ">=",
            policy.min_historical_supportive_horizons,
            failure=FAIL,
        )
        catastrophic = min(
            (value for value in catastrophic_values if value is not None), default=None
        )
        self._threshold(
            stability,
            "HISTORICAL_CATASTROPHIC_DEGRADATION",
            "HISTORICAL_STABILITY",
            None,
            catastrophic,
            ">=",
            policy.historical_catastrophic_mean_delta_floor,
            failure=FAIL,
        )
        self._threshold(
            sufficiency,
            "FORWARD_TURNOVER_TRANSITION_COUNT",
            "SUFFICIENCY",
            None,
            turnover.transition_count,
            ">=",
            policy.min_forward_turnover_transitions,
        )
        span = self._observation_span_hours(gross)
        self._threshold(
            sufficiency,
            "FORWARD_OBSERVATION_SPAN",
            "SUFFICIENCY",
            None,
            span,
            ">=",
            policy.min_forward_observation_span_hours,
        )
        self._threshold(
            performance,
            "FORWARD_CONTINUITY",
            "FORWARD_PERFORMANCE",
            None,
            turnover.continuity_break_count,
            "<=",
            policy.max_forward_continuity_break_count,
            failure=FAIL,
        )
        checks.extend(sufficiency)
        checks.extend(stability)
        checks.extend(performance)

    @staticmethod
    def _cohorts(values):
        indexed = {item.horizon_minutes: item for item in values}
        if len(indexed) != len(values):
            raise _InvalidGateData("duplicate historical horizon cohort")
        return indexed

    @staticmethod
    def _observation_span_hours(gross):
        if not gross.horizons:
            return None
        timestamps = [
            item.captured_at
            for item in gross.horizons[0].snapshots
            if item.captured_at is not None
        ]
        if len(timestamps) < 2:
            return None
        if any(
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
            for value in timestamps
        ):
            raise _InvalidGateData("forward captured_at is not timezone-aware")
        return Decimal(
            str((max(timestamps) - min(timestamps)).total_seconds())
        ) / Decimal("3600")

    def _threshold(
        self,
        checks,
        check_id,
        category,
        horizon,
        observed,
        comparator,
        threshold,
        failure=INSUFFICIENT,
    ):
        if observed is None:
            status = INSUFFICIENT
        elif not self._valid_number(observed):
            status = INVALID
        elif comparator == ">=":
            status = PASS if observed >= threshold else failure
        elif comparator == ">":
            status = PASS if observed > threshold else failure
        else:
            status = PASS if observed <= threshold else failure
        checks.append(
            self._check(
                check_id, category, status, horizon, observed, comparator, threshold
            )
        )

    def _performance(
        self,
        checks,
        check_id,
        horizon,
        observed,
        comparator,
        threshold,
        sample_sufficient,
    ):
        if not sample_sufficient:
            status = INSUFFICIENT
        elif observed is None:
            status = INVALID
        elif not self._valid_number(observed):
            status = INVALID
        elif comparator == ">":
            status = PASS if observed > threshold else FAIL
        else:
            status = PASS if observed >= threshold else FAIL
        checks.append(
            self._check(
                check_id,
                "FORWARD_PERFORMANCE",
                status,
                horizon,
                observed,
                comparator,
                threshold,
            )
        )

    @staticmethod
    def _valid_number(value) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            return False
        return not isinstance(value, Decimal) or value.is_finite()

    @staticmethod
    def _check(
        check_id,
        category,
        status,
        horizon=None,
        observed=None,
        comparator=None,
        threshold=None,
        reason=None,
    ):
        return PromotionGateCheckResult(
            check_id,
            category,
            status,
            horizon,
            None if observed is None else str(observed),
            comparator,
            None if threshold is None else str(threshold),
            reason,
        )

    def _result(
        self,
        candidate,
        evaluated_at,
        ceiling,
        checks,
        historical,
        gross,
        turnover,
        cost,
        *,
        candidate_id=None,
    ):
        values = tuple(checks)
        status = (
            INVALID_PROMOTION_DATA
            if any(item.status == INVALID for item in values)
            else INSUFFICIENT_DATA
            if any(item.status == INSUFFICIENT for item in values)
            else NOT_ELIGIBLE
            if any(item.status == FAIL for item in values)
            else ELIGIBLE_FOR_REVIEW
        )
        reasons = {
            INVALID_PROMOTION_DATA: "promotion evidence integrity validation failed",
            INSUFFICIENT_DATA: "evidence does not meet v1 minimum sample requirements",
            NOT_ELIGIBLE: "sufficient evidence failed one or more v1 performance checks",
            ELIGIBLE_FOR_REVIEW: None,
        }
        return PolicyPromotionGateResult(
            (
                candidate.candidate_id
                if candidate
                else historical.candidate_id
                if historical is not None
                else candidate_id
            ),
            candidate,
            self.policy.schema_version,
            gate_policy_signature(self.policy),
            self.policy,
            evaluated_at,
            ceiling,
            status,
            reasons[status],
            tuple(item for item in values if item.status == PASS),
            tuple(item for item in values if item.status == INSUFFICIENT),
            tuple(item for item in values if item.status == FAIL),
            tuple(item for item in values if item.status == INVALID),
            values,
            historical,
            gross,
            turnover,
            cost,
            True,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
        )


__all__ = [
    "ELIGIBLE_FOR_REVIEW",
    "FAIL",
    "INSUFFICIENT",
    "INSUFFICIENT_DATA",
    "INVALID",
    "INVALID_PROMOTION_DATA",
    "NOT_ELIGIBLE",
    "PASS",
    "POLICY_PROMOTION_GATE_V1",
    "RESULT_TYPE",
    "PolicyPromotionGatePolicy",
    "PolicyPromotionGateResult",
    "PolicyPromotionGateService",
    "PromotionGateCheckResult",
    "gate_policy_signature",
    "validate_gate_policy",
]
