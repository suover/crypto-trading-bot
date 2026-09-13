from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentError,
)
from crypto_trading_bot.services.shadow_policy_performance_service import (
    INSUFFICIENT_SHADOW_TRANSITIONS,
    INVALID_SHADOW_OUTCOME_DATA,
    INVALID_SHADOW_PERFORMANCE_EVIDENCE,
    NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS,
    NO_SHADOW_ENROLLMENT as PERFORMANCE_NO_SHADOW_ENROLLMENT,
    NO_SHADOW_EVALUATIONS,
    NO_SUCCESSFUL_SHADOW_SELECTIONS,
    RESULT_TYPE as PERFORMANCE_RESULT_TYPE,
    SHADOW_OUTCOMES_PENDING,
    SUCCESS as PERFORMANCE_SUCCESS,
    ShadowPolicyPerformanceResult,
    ShadowPolicyPerformanceService,
)
from crypto_trading_bot.services.shadow_policy_provenance import (
    load_and_validate_shadow_policy_enrollment,
)


RESULT_TYPE = "SHADOW_REVIEW_GATE_V1_DECISION"
PASS = "PASS"
INSUFFICIENT = "INSUFFICIENT"
FAIL = "FAIL"
INVALID = "INVALID"
NO_SHADOW_ENROLLMENT = "NO_SHADOW_ENROLLMENT"
INVALID_REVIEW_DATA = "INVALID_REVIEW_DATA"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
ELIGIBLE_FOR_PROMOTION_REVIEW = "ELIGIBLE_FOR_PROMOTION_REVIEW"


@dataclass(frozen=True)
class ShadowReviewGatePolicy:
    schema_version: str
    required_horizons: tuple[int, ...]
    fee_rate: Decimal
    spread_cost_rate: Decimal
    slippage_rate: Decimal
    min_shadow_observation_span_hours: int
    min_shadow_successful_selection_count: int
    min_shadow_successful_gross_snapshots: int
    min_shadow_turnover_transitions: int
    min_shadow_cost_adjustable_snapshots: int
    min_shadow_cost_adjustable_coverage: Decimal
    min_supportive_gross_horizons: int
    min_supportive_cost_adjusted_horizons: int
    min_shadow_mean_gross_delta: Decimal
    min_shadow_mean_cost_adjusted_delta: Decimal
    min_shadow_median_cost_adjusted_delta: Decimal
    min_shadow_cost_adjusted_win_rate: Decimal
    catastrophic_mean_gross_delta_floor: Decimal
    catastrophic_mean_cost_adjusted_delta_floor: Decimal
    max_shadow_continuity_break_count: int


SHADOW_REVIEW_GATE_V1 = ShadowReviewGatePolicy(
    schema_version="shadow-review-gate-v1",
    required_horizons=(60, 240, 1440),
    fee_rate=Decimal("0.0005"),
    spread_cost_rate=Decimal("0.0005"),
    slippage_rate=Decimal("0.001"),
    min_shadow_observation_span_hours=336,
    min_shadow_successful_selection_count=42,
    min_shadow_successful_gross_snapshots=35,
    min_shadow_turnover_transitions=40,
    min_shadow_cost_adjustable_snapshots=35,
    min_shadow_cost_adjustable_coverage=Decimal("0.80"),
    min_supportive_gross_horizons=2,
    min_supportive_cost_adjusted_horizons=2,
    min_shadow_mean_gross_delta=Decimal("0"),
    min_shadow_mean_cost_adjusted_delta=Decimal("0"),
    min_shadow_median_cost_adjusted_delta=Decimal("0"),
    min_shadow_cost_adjusted_win_rate=Decimal("0.55"),
    catastrophic_mean_gross_delta_floor=Decimal("-0.50"),
    catastrophic_mean_cost_adjusted_delta_floor=Decimal("-0.50"),
    max_shadow_continuity_break_count=0,
)


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ReplayInputError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonicalize(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ReplayInputError("signature Decimal must be finite")
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def validate_shadow_review_policy(policy: ShadowReviewGatePolicy) -> None:
    if not isinstance(policy, ShadowReviewGatePolicy):
        raise ReplayInputError("Shadow review policy is invalid")
    if policy.schema_version != "shadow-review-gate-v1":
        raise ReplayInputError("Shadow review policy schema is unsupported")
    if policy.required_horizons != (60, 240, 1440):
        raise ReplayInputError("Shadow review required horizons are invalid")
    counts = (
        policy.min_shadow_observation_span_hours,
        policy.min_shadow_successful_selection_count,
        policy.min_shadow_successful_gross_snapshots,
        policy.min_shadow_turnover_transitions,
        policy.min_shadow_cost_adjustable_snapshots,
        policy.min_supportive_gross_horizons,
        policy.min_supportive_cost_adjusted_horizons,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in counts
    ):
        raise ReplayInputError("Shadow review count thresholds must be positive")
    if (
        isinstance(policy.max_shadow_continuity_break_count, bool)
        or not isinstance(policy.max_shadow_continuity_break_count, int)
        or policy.max_shadow_continuity_break_count < 0
    ):
        raise ReplayInputError("Shadow review continuity threshold is invalid")
    decimal_names = (
        "fee_rate",
        "spread_cost_rate",
        "slippage_rate",
        "min_shadow_cost_adjustable_coverage",
        "min_shadow_mean_gross_delta",
        "min_shadow_mean_cost_adjusted_delta",
        "min_shadow_median_cost_adjusted_delta",
        "min_shadow_cost_adjusted_win_rate",
        "catastrophic_mean_gross_delta_floor",
        "catastrophic_mean_cost_adjusted_delta_floor",
    )
    for name in decimal_names:
        value = getattr(policy, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, Decimal)
            or not value.is_finite()
        ):
            raise ReplayInputError(
                f"Shadow review policy {name} must be finite Decimal"
            )
    if any(
        value < 0 or value >= 1
        for value in (policy.fee_rate, policy.spread_cost_rate, policy.slippage_rate)
    ):
        raise ReplayInputError("Shadow review cost rates must be in [0, 1)")
    if any(
        value < 0 or value > 1
        for value in (
            policy.min_shadow_cost_adjustable_coverage,
            policy.min_shadow_cost_adjusted_win_rate,
        )
    ):
        raise ReplayInputError("Shadow review coverage and win rate must be in [0, 1]")
    horizon_count = len(policy.required_horizons)
    if not (
        1 <= policy.min_supportive_gross_horizons <= horizon_count
        and 1 <= policy.min_supportive_cost_adjusted_horizons <= horizon_count
    ):
        raise ReplayInputError("Shadow review supportive horizon threshold is invalid")


def shadow_review_policy_definition(policy: ShadowReviewGatePolicy) -> dict:
    validate_shadow_review_policy(policy)
    return _canonicalize(asdict(policy))


def shadow_review_policy_signature(policy: ShadowReviewGatePolicy) -> str:
    payload = json.dumps(
        shadow_review_policy_definition(policy),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{policy.schema_version}:{sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class ShadowReviewGateCheckResult:
    check_id: str
    category: str
    status: str
    horizon_minutes: int | None
    observed_value: str | None
    comparator: str | None
    threshold_value: str | None
    reason: str | None


@dataclass(frozen=True)
class ShadowReviewGateResult:
    candidate_id: int
    enrollment: object | None
    review_policy_schema_version: str
    review_policy_signature: str
    review_policy_definition: dict
    review_policy: ShadowReviewGatePolicy
    evaluated_at: datetime
    status: str
    safe_reason: str | None
    eligible_for_promotion_review: bool
    passed_checks: tuple[ShadowReviewGateCheckResult, ...]
    insufficient_checks: tuple[ShadowReviewGateCheckResult, ...]
    failed_checks: tuple[ShadowReviewGateCheckResult, ...]
    invalid_checks: tuple[ShadowReviewGateCheckResult, ...]
    all_checks: tuple[ShadowReviewGateCheckResult, ...]
    performance: ShadowPolicyPerformanceResult | None
    pre_shadow_gate_provenance_verified: bool
    shadow_performance_provenance_verified: bool
    review_decision_signature: str | None
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    promotion_performed: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    shadow_runtime_changed: bool


class _InvalidReviewData(Exception):
    pass


class ShadowReviewGateService:
    """Apply the immutable v1 review policy to read-only Shadow evidence."""

    def __init__(
        self,
        session: Session,
        *,
        policy: ShadowReviewGatePolicy = SHADOW_REVIEW_GATE_V1,
        performance_service: ShadowPolicyPerformanceService | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        validate_shadow_review_policy(policy)
        self.session = session
        self.policy = policy
        self.performance_service = (
            performance_service or ShadowPolicyPerformanceService(session)
        )
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def evaluate(self, *, candidate_id: int) -> ShadowReviewGateResult:
        if (
            isinstance(candidate_id, bool)
            or not isinstance(candidate_id, int)
            or candidate_id < 1
        ):
            raise ReplayInputError("candidate ID must be a positive integer")
        evaluated_at = _utc(self.now_fn(), "Shadow review evaluated_at")
        try:
            validated = load_and_validate_shadow_policy_enrollment(
                self.session, candidate_id
            )
        except (ShadowPolicyEnrollmentError, ValueError, TypeError) as error:
            return self._result(
                candidate_id,
                None,
                evaluated_at,
                (
                    self._check(
                        "pre_shadow_gate_provenance",
                        "INTEGRITY",
                        INVALID,
                        reason=str(error),
                    ),
                ),
                None,
                provenance_verified=False,
                performance_verified=False,
                decision_performed=False,
            )
        if validated is None:
            return self._result(
                candidate_id,
                None,
                evaluated_at,
                (),
                None,
                explicit_status=NO_SHADOW_ENROLLMENT,
                provenance_verified=False,
                performance_verified=False,
                decision_performed=False,
            )
        enrollment = validated.row
        try:
            performance = self.performance_service.evaluate(
                candidate_id=candidate_id,
                horizons=self.policy.required_horizons,
                fee_rate=self.policy.fee_rate,
                spread_cost_rate=self.policy.spread_cost_rate,
                slippage_rate=self.policy.slippage_rate,
            )
            self._validate_performance(candidate_id, enrollment, performance)
            checks = [
                self._check("pre_shadow_gate_provenance", "INTEGRITY", PASS),
                self._check("shadow_performance_provenance", "INTEGRITY", PASS),
            ]
            if performance.status == NO_SHADOW_EVALUATIONS:
                checks.append(
                    self._check(
                        "shadow.performance.available",
                        "SUFFICIENCY",
                        INSUFFICIENT,
                        reason="no Shadow evaluation is available",
                    )
                )
            elif performance.status == PERFORMANCE_SUCCESS:
                self._evidence_checks(performance, checks)
            else:
                raise _InvalidReviewData(
                    f"unsupported Shadow performance status: {performance.status}"
                )
            return self._result(
                candidate_id,
                enrollment,
                evaluated_at,
                tuple(checks),
                performance,
                provenance_verified=True,
                performance_verified=True,
                decision_performed=True,
            )
        except (
            _InvalidReviewData,
            ReplayInputError,
            AttributeError,
            TypeError,
        ) as error:
            return self._result(
                candidate_id,
                enrollment,
                evaluated_at,
                (
                    self._check("pre_shadow_gate_provenance", "INTEGRITY", PASS),
                    self._check(
                        "shadow_performance_provenance",
                        "INTEGRITY",
                        INVALID,
                        reason=str(error),
                    ),
                ),
                locals().get("performance"),
                provenance_verified=True,
                performance_verified=False,
                decision_performed=False,
            )

    def _validate_performance(self, candidate_id, enrollment, value) -> None:
        if value.status == INVALID_SHADOW_PERFORMANCE_EVIDENCE:
            raise _InvalidReviewData(
                f"Shadow performance is invalid: {value.safe_reason}"
            )
        if value.status == PERFORMANCE_NO_SHADOW_ENROLLMENT:
            raise _InvalidReviewData("validated enrollment is missing from performance")
        assumptions = value.cost_assumptions
        enrollment_fields = (
            "id",
            "candidate_id",
            "user_id",
            "exchange",
            "quote_asset",
            "scenario_name",
            "scenario_definition_signature",
            "baseline_policy_signature",
            "effective_top_n",
            "gate_policy_signature",
            "gate_decision_signature",
        )
        if (
            value.result_type != PERFORMANCE_RESULT_TYPE
            or value.candidate_id != candidate_id
            or value.enrollment is None
            or value.enrollment.id != enrollment.id
            or value.enrollment.candidate_id != candidate_id
            or any(
                getattr(value.enrollment, field) != getattr(enrollment, field)
                for field in enrollment_fields
            )
            or value.requested_horizons != self.policy.required_horizons
            or assumptions.fee_rate != self.policy.fee_rate
            or assumptions.spread_cost_rate != self.policy.spread_cost_rate
            or assumptions.slippage_rate != self.policy.slippage_rate
            or value.database_write
            or value.external_calls
            or value.live_policy_change
            or value.offline_replay_performed
            or value.sample_sufficiency_assessed
            or value.statistical_inference_performed
            or value.policy_decision_performed
            or value.promotion_performed
            or value.shadow_runtime_changed
            or not value.shadow_enrollment_verified
            or not value.shadow_boundary_enforced
            or not value.performance_as_of_enforced
            or not value.evaluation_ceiling_enforced
        ):
            raise _InvalidReviewData("Shadow performance provenance does not align")
        _utc(value.performance_evidence_as_of, "performance evidence as-of")
        if value.status == NO_SHADOW_EVALUATIONS:
            if (
                any(
                    (
                        value.timeline_snapshot_ids,
                        value.candidate_context_snapshot_ids,
                        value.successful_selection_snapshot_ids,
                        value.gross,
                        value.cost_adjusted,
                    )
                )
                or value.turnover is not None
                or value.shadow_evaluation_snapshot_id_ceiling is not None
            ):
                raise _InvalidReviewData("no-evaluation performance contains evidence")
            return
        if value.status != PERFORMANCE_SUCCESS:
            raise _InvalidReviewData("Shadow performance status is unsupported")
        if not (
            value.stored_shadow_selection_reused
            and value.outcome_data_used
            and value.performance_evaluated
            and value.cost_model_reused
            and value.turnover_continuity_enforced
        ):
            raise _InvalidReviewData("Shadow performance safety flags are incomplete")
        self._validate_universe(value)

    def _validate_universe(self, value) -> None:
        timeline = tuple(value.timeline_snapshot_ids)
        context = tuple(value.candidate_context_snapshot_ids)
        successful = tuple(value.successful_selection_snapshot_ids)
        for items in (timeline, context, successful):
            if len(items) != len(set(items)) or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 1
                for item in items
            ):
                raise _InvalidReviewData("Shadow performance snapshot IDs are invalid")
        if (
            not set(context).issubset(timeline)
            or not set(successful).issubset(context)
            or value.timeline_evaluation_count != len(timeline)
            or value.successful_selection_count != len(successful)
            or any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0
                for count in (
                    value.context_mismatch_count,
                    value.baseline_integrity_failed_count,
                    value.replay_incompatible_count,
                )
            )
            or value.successful_selection_count
            + value.context_mismatch_count
            + value.baseline_integrity_failed_count
            + value.replay_incompatible_count
            != value.timeline_evaluation_count
        ):
            raise _InvalidReviewData("Shadow performance universe is inconsistent")
        ceiling = value.shadow_evaluation_snapshot_id_ceiling
        if (
            isinstance(ceiling, bool)
            or not isinstance(ceiling, int)
            or ceiling < 1
            or any(snapshot_id > ceiling for snapshot_id in timeline)
        ):
            raise _InvalidReviewData("Shadow performance ceiling is inconsistent")
        self._validate_observation(value)
        gross = self._by_horizon(value.gross, "Gross")
        cost = self._by_horizon(value.cost_adjusted, "Cost")
        turnover = value.turnover
        if turnover is None:
            raise _InvalidReviewData("Shadow turnover is missing")
        if (
            tuple(turnover.timeline_snapshot_ids) != timeline
            or tuple(turnover.candidate_context_snapshot_ids) != context
            or tuple(turnover.successful_selection_snapshot_ids) != successful
            or turnover.transition_count != len(turnover.transitions)
            or turnover.baseline_summary.transition_count != turnover.transition_count
            or turnover.shadow_summary.transition_count != turnover.transition_count
            or turnover.status
            not in {PERFORMANCE_SUCCESS, INSUFFICIENT_SHADOW_TRANSITIONS}
            or (turnover.status == PERFORMANCE_SUCCESS)
            != (turnover.transition_count > 0)
        ):
            raise _InvalidReviewData("Shadow turnover aggregation is inconsistent")
        for summary in (turnover.baseline_summary, turnover.shadow_summary):
            if turnover.transition_count:
                self._finite_decimal(
                    summary.mean_replacement_rate, "Turnover mean replacement rate"
                )
        for horizon in self.policy.required_horizons:
            self._validate_gross(gross[horizon], len(successful))
            self._validate_cost(cost[horizon], successful, turnover.transition_count)
            if (
                cost[horizon].successful_gross_snapshot_count
                != gross[horizon].successful_comparable_snapshot_count
                or cost[horizon].outcome_incomplete_count
                != gross[horizon].outcome_incomplete_count
            ):
                raise _InvalidReviewData(
                    "Gross and cost horizon aggregation is inconsistent"
                )

    def _by_horizon(self, values, label):
        mapped = {item.horizon_minutes: item for item in values}
        if (
            len(mapped) != len(values)
            or tuple(sorted(mapped)) != self.policy.required_horizons
        ):
            raise _InvalidReviewData(f"{label} horizons are missing or duplicated")
        return mapped

    @staticmethod
    def _validate_observation(value) -> None:
        count = value.successful_selection_count
        first = value.first_success_captured_at
        last = value.last_success_captured_at
        span = value.observation_span_hours
        if count == 0:
            if first is not None or last is not None or span is not None:
                raise _InvalidReviewData(
                    "empty observation timestamps are inconsistent"
                )
            return
        first_at = _utc(first, "first success captured_at")
        last_at = _utc(last, "last success captured_at")
        expected = Decimal(str((last_at - first_at).total_seconds())) / Decimal("3600")
        if (
            last_at < first_at
            or span != expected
            or (count == 1 and first_at != last_at)
        ):
            raise _InvalidReviewData("observation span is inconsistent")

    @staticmethod
    def _finite_decimal(value, field_name, *, minimum=None, maximum=None):
        if (
            isinstance(value, bool)
            or not isinstance(value, Decimal)
            or not value.is_finite()
            or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)
        ):
            raise _InvalidReviewData(f"{field_name} is invalid")
        return value

    def _validate_gross(self, item, eligible) -> None:
        counts = (
            item.eligible_shadow_snapshot_count,
            item.successful_comparable_snapshot_count,
            item.outcome_incomplete_count,
            item.invalid_outcome_count,
            item.shadow_win_count,
            item.shadow_loss_count,
            item.tie_count,
        )
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts):
            raise _InvalidReviewData("Gross counts are invalid")
        if (
            item.eligible_shadow_snapshot_count != eligible
            or item.successful_comparable_snapshot_count > eligible
            or item.successful_comparable_snapshot_count
            + item.outcome_incomplete_count
            + item.invalid_outcome_count
            != eligible
            or item.shadow_win_count + item.shadow_loss_count + item.tie_count
            != item.successful_comparable_snapshot_count
            or item.status
            not in {
                PERFORMANCE_SUCCESS,
                SHADOW_OUTCOMES_PENDING,
                NO_SUCCESSFUL_SHADOW_SELECTIONS,
                INVALID_SHADOW_OUTCOME_DATA,
            }
        ):
            raise _InvalidReviewData("Gross aggregation is inconsistent")
        if item.status == INVALID_SHADOW_OUTCOME_DATA or item.invalid_outcome_count:
            raise _InvalidReviewData("Gross outcome data is invalid")
        if item.status == PERFORMANCE_SUCCESS:
            self._finite_decimal(item.mean_return_delta, "Gross mean delta")
            expected_win_rate = (
                Decimal(item.shadow_win_count)
                / Decimal(item.successful_comparable_snapshot_count)
                if item.successful_comparable_snapshot_count
                else None
            )
            if item.shadow_win_rate != expected_win_rate:
                raise _InvalidReviewData("Gross win rate is inconsistent")

    def _validate_cost(
        self, item, successful_snapshot_ids, turnover_transition_count
    ) -> None:
        eligible = len(successful_snapshot_ids)
        counts = (
            item.eligible_shadow_snapshot_count,
            item.successful_gross_snapshot_count,
            item.shadow_transition_count,
            item.cost_adjustable_shadow_snapshot_count,
            item.outcome_incomplete_count,
            item.cost_adjusted_shadow_win_count,
            item.cost_adjusted_shadow_loss_count,
            item.tie_count,
        )
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts):
            raise _InvalidReviewData("Cost counts are invalid")
        ids = tuple(item.cost_adjustable_shadow_snapshot_ids)
        expected_coverage = Decimal(len(ids)) / Decimal(eligible) if eligible else None
        if (
            item.eligible_shadow_snapshot_count != eligible
            or item.successful_gross_snapshot_count + item.outcome_incomplete_count
            != eligible
            or item.shadow_transition_count != turnover_transition_count
            or item.cost_adjustable_shadow_snapshot_count != len(ids)
            or len(ids) != len(set(ids))
            or any(
                isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1
                for item_id in ids
            )
            or not set(ids).issubset(successful_snapshot_ids)
            or item.cost_adjustable_shadow_snapshot_count
            > item.successful_gross_snapshot_count
            or item.successful_gross_snapshot_count > eligible
            or item.cost_adjustable_coverage_rate != expected_coverage
            or item.cost_adjusted_shadow_win_count
            + item.cost_adjusted_shadow_loss_count
            + item.tie_count
            != item.cost_adjustable_shadow_snapshot_count
            or item.status
            not in {
                PERFORMANCE_SUCCESS,
                INSUFFICIENT_SHADOW_TRANSITIONS,
                SHADOW_OUTCOMES_PENDING,
                NO_SHADOW_COST_ADJUSTABLE_SNAPSHOTS,
            }
        ):
            raise _InvalidReviewData("Cost aggregation is inconsistent")
        if item.cost_adjustable_coverage_rate is not None:
            self._finite_decimal(
                item.cost_adjustable_coverage_rate,
                "Cost coverage",
                minimum=Decimal("0"),
                maximum=Decimal("1"),
            )
        if item.status == PERFORMANCE_SUCCESS:
            self._finite_decimal(
                item.mean_cost_adjusted_return_delta, "Cost mean delta"
            )
            self._finite_decimal(
                item.median_cost_adjusted_return_delta, "Cost median delta"
            )
            self._finite_decimal(
                item.cost_adjusted_shadow_win_rate,
                "Cost win rate",
                minimum=Decimal("0"),
                maximum=Decimal("1"),
            )
            expected_win_rate = (
                Decimal(item.cost_adjusted_shadow_win_count)
                / Decimal(item.cost_adjustable_shadow_snapshot_count)
                if item.cost_adjustable_shadow_snapshot_count
                else None
            )
            if item.cost_adjusted_shadow_win_rate != expected_win_rate:
                raise _InvalidReviewData("Cost win rate is inconsistent")

    def _evidence_checks(self, value, checks) -> None:
        policy = self.policy
        self._threshold(
            checks,
            "shadow.observation_span_hours",
            "SUFFICIENCY",
            value.observation_span_hours,
            ">=",
            policy.min_shadow_observation_span_hours,
            insufficient=True,
        )
        self._threshold(
            checks,
            "shadow.successful_selection_count",
            "SUFFICIENCY",
            value.successful_selection_count,
            ">=",
            policy.min_shadow_successful_selection_count,
            insufficient=True,
        )
        turnover = value.turnover
        self._threshold(
            checks,
            "shadow.turnover.transition_count",
            "SUFFICIENCY",
            turnover.transition_count,
            ">=",
            policy.min_shadow_turnover_transitions,
            insufficient=True,
        )
        self._threshold(
            checks,
            "shadow.turnover.continuity_break_count",
            "STABILITY",
            turnover.continuity_break_count,
            "<=",
            policy.max_shadow_continuity_break_count,
        )
        gross_by_horizon = {item.horizon_minutes: item for item in value.gross}
        gross_supportive = 0
        for horizon in policy.required_horizons:
            item = gross_by_horizon[horizon]
            self._threshold(
                checks,
                f"shadow.gross.{horizon}.successful_snapshot_count",
                "SUFFICIENCY",
                item.successful_comparable_snapshot_count,
                ">=",
                policy.min_shadow_successful_gross_snapshots,
                horizon=horizon,
                insufficient=True,
            )
            mean = (
                item.mean_return_delta if item.status == PERFORMANCE_SUCCESS else None
            )
            mean_check = self._threshold(
                checks,
                f"shadow.gross.{horizon}.mean_delta",
                "PERFORMANCE",
                mean,
                ">=",
                policy.min_shadow_mean_gross_delta,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            self._threshold(
                checks,
                f"shadow.gross.{horizon}.catastrophic_floor",
                "RISK",
                mean,
                ">=",
                policy.catastrophic_mean_gross_delta_floor,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            gross_supportive += mean_check.status == PASS
        self._threshold(
            checks,
            "shadow.gross.supportive_horizon_count",
            "PERFORMANCE",
            gross_supportive,
            ">=",
            policy.min_supportive_gross_horizons,
        )
        cost_by_horizon = {item.horizon_minutes: item for item in value.cost_adjusted}
        cost_supportive = 0
        for horizon in policy.required_horizons:
            item = cost_by_horizon[horizon]
            self._threshold(
                checks,
                f"shadow.cost.{horizon}.cost_adjustable_count",
                "SUFFICIENCY",
                item.cost_adjustable_shadow_snapshot_count,
                ">=",
                policy.min_shadow_cost_adjustable_snapshots,
                horizon=horizon,
                insufficient=True,
            )
            self._threshold(
                checks,
                f"shadow.cost.{horizon}.coverage",
                "SUFFICIENCY",
                item.cost_adjustable_coverage_rate,
                ">=",
                policy.min_shadow_cost_adjustable_coverage,
                horizon=horizon,
                insufficient=True,
            )
            available = item.status == PERFORMANCE_SUCCESS
            mean = item.mean_cost_adjusted_return_delta if available else None
            median_value = item.median_cost_adjusted_return_delta if available else None
            win_rate = item.cost_adjusted_shadow_win_rate if available else None
            mean_check = self._threshold(
                checks,
                f"shadow.cost.{horizon}.mean_delta",
                "PERFORMANCE",
                mean,
                ">",
                policy.min_shadow_mean_cost_adjusted_delta,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            median_check = self._threshold(
                checks,
                f"shadow.cost.{horizon}.median_delta",
                "PERFORMANCE",
                median_value,
                ">=",
                policy.min_shadow_median_cost_adjusted_delta,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            win_check = self._threshold(
                checks,
                f"shadow.cost.{horizon}.win_rate",
                "PERFORMANCE",
                win_rate,
                ">=",
                policy.min_shadow_cost_adjusted_win_rate,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            self._threshold(
                checks,
                f"shadow.cost.{horizon}.catastrophic_floor",
                "RISK",
                mean,
                ">=",
                policy.catastrophic_mean_cost_adjusted_delta_floor,
                horizon=horizon,
                unavailable_insufficient=True,
            )
            cost_supportive += all(
                item.status == PASS for item in (mean_check, median_check, win_check)
            )
        self._threshold(
            checks,
            "shadow.cost.supportive_horizon_count",
            "PERFORMANCE",
            cost_supportive,
            ">=",
            policy.min_supportive_cost_adjusted_horizons,
        )

    def _threshold(
        self,
        checks,
        check_id,
        category,
        observed,
        comparator,
        threshold,
        *,
        horizon=None,
        insufficient=False,
        unavailable_insufficient=False,
    ):
        if observed is None:
            status = (
                INSUFFICIENT if (insufficient or unavailable_insufficient) else INVALID
            )
            result = self._check(
                check_id,
                category,
                status,
                horizon,
                comparator=comparator,
                threshold=threshold,
                reason="required evidence is unavailable",
            )
            checks.append(result)
            return result
        if isinstance(observed, Decimal):
            self._finite_decimal(observed, check_id)
        elif isinstance(observed, bool) or not isinstance(observed, int):
            raise _InvalidReviewData(f"{check_id} value is invalid")
        if comparator == ">=":
            passed = observed >= threshold
        elif comparator == ">":
            passed = observed > threshold
        elif comparator == "<=":
            passed = observed <= threshold
        else:
            raise _InvalidReviewData(f"{check_id} comparator is invalid")
        status = PASS if passed else INSUFFICIENT if insufficient else FAIL
        result = self._check(
            check_id,
            category,
            status,
            horizon,
            observed,
            comparator,
            threshold,
        )
        checks.append(result)
        return result

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
        return ShadowReviewGateCheckResult(
            check_id=check_id,
            category=category,
            status=status,
            horizon_minutes=horizon,
            observed_value=None if observed is None else str(observed),
            comparator=comparator,
            threshold_value=None if threshold is None else str(threshold),
            reason=reason,
        )

    def _result(
        self,
        candidate_id,
        enrollment,
        evaluated_at,
        checks,
        performance,
        *,
        provenance_verified,
        performance_verified,
        decision_performed,
        explicit_status=None,
    ):
        values = tuple(checks)
        status = explicit_status or (
            INVALID_REVIEW_DATA
            if any(item.status == INVALID for item in values)
            else INSUFFICIENT_DATA
            if any(item.status == INSUFFICIENT for item in values)
            else NOT_ELIGIBLE
            if any(item.status == FAIL for item in values)
            else ELIGIBLE_FOR_PROMOTION_REVIEW
        )
        reasons = {
            NO_SHADOW_ENROLLMENT: "candidate has no Shadow enrollment",
            INVALID_REVIEW_DATA: "Shadow review evidence integrity validation failed",
            INSUFFICIENT_DATA: "Shadow evidence does not meet v1 sample requirements",
            NOT_ELIGIBLE: "sufficient Shadow evidence failed v1 review checks",
            ELIGIBLE_FOR_PROMOTION_REVIEW: None,
        }
        result = ShadowReviewGateResult(
            candidate_id=candidate_id,
            enrollment=enrollment,
            review_policy_schema_version=self.policy.schema_version,
            review_policy_signature=shadow_review_policy_signature(self.policy),
            review_policy_definition=shadow_review_policy_definition(self.policy),
            review_policy=self.policy,
            evaluated_at=evaluated_at,
            status=status,
            safe_reason=reasons[status],
            eligible_for_promotion_review=status == ELIGIBLE_FOR_PROMOTION_REVIEW,
            passed_checks=tuple(item for item in values if item.status == PASS),
            insufficient_checks=tuple(
                item for item in values if item.status == INSUFFICIENT
            ),
            failed_checks=tuple(item for item in values if item.status == FAIL),
            invalid_checks=tuple(item for item in values if item.status == INVALID),
            all_checks=values,
            performance=performance,
            pre_shadow_gate_provenance_verified=provenance_verified,
            shadow_performance_provenance_verified=performance_verified,
            review_decision_signature=None,
            sample_sufficiency_assessed=decision_performed,
            statistical_inference_performed=False,
            policy_decision_performed=decision_performed,
            promotion_performed=False,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
            shadow_runtime_changed=False,
        )
        if enrollment is not None and performance_verified:
            result = replace(
                result,
                review_decision_signature=shadow_review_decision_signature(result),
            )
        return result


def shadow_review_decision_signature(result: ShadowReviewGateResult) -> str:
    enrollment = result.enrollment
    performance = result.performance
    if enrollment is None or performance is None:
        raise ReplayInputError("review decision signature requires verified evidence")
    gross = [
        {
            "horizon_minutes": item.horizon_minutes,
            "eligible_count": item.eligible_shadow_snapshot_count,
            "successful_count": item.successful_comparable_snapshot_count,
            "outcome_incomplete_count": item.outcome_incomplete_count,
            "mean_return_delta": item.mean_return_delta,
            "status": item.status,
        }
        for item in performance.gross
    ]
    turnover = performance.turnover
    turnover_summary = (
        None
        if turnover is None
        else {
            "transition_count": turnover.transition_count,
            "continuity_break_count": turnover.continuity_break_count,
            "baseline_mean_replacement_rate": turnover.baseline_summary.mean_replacement_rate,
            "shadow_mean_replacement_rate": turnover.shadow_summary.mean_replacement_rate,
            "mean_replacement_rate_delta": turnover.mean_replacement_rate_delta_vs_baseline,
            "status": turnover.status,
        }
    )
    cost = [
        {
            "horizon_minutes": item.horizon_minutes,
            "eligible_count": item.eligible_shadow_snapshot_count,
            "successful_gross_count": item.successful_gross_snapshot_count,
            "cost_adjustable_count": item.cost_adjustable_shadow_snapshot_count,
            "cost_adjustable_snapshot_ids": item.cost_adjustable_shadow_snapshot_ids,
            "coverage": item.cost_adjustable_coverage_rate,
            "mean_delta": item.mean_cost_adjusted_return_delta,
            "median_delta": item.median_cost_adjusted_return_delta,
            "win_rate": item.cost_adjusted_shadow_win_rate,
            "status": item.status,
        }
        for item in performance.cost_adjusted
    ]
    payload = {
        "result_type": RESULT_TYPE,
        "candidate": {
            "candidate_id": result.candidate_id,
            "shadow_enrollment_id": enrollment.id,
            "user_id": enrollment.user_id,
            "exchange": enrollment.exchange,
            "quote_asset": enrollment.quote_asset,
            "scenario_name": enrollment.scenario_name,
            "scenario_definition_signature": enrollment.scenario_definition_signature,
            "baseline_policy_signature": enrollment.baseline_policy_signature,
            "effective_top_n": enrollment.effective_top_n,
        },
        "stored_pre_shadow_gate_decision_signature": enrollment.gate_decision_signature,
        "review_policy_signature": result.review_policy_signature,
        "review_policy_definition": result.review_policy_definition,
        "performance_evidence_as_of": performance.performance_evidence_as_of,
        "shadow_evaluation_snapshot_id_ceiling": performance.shadow_evaluation_snapshot_id_ceiling,
        "successful_selection_snapshot_ids": performance.successful_selection_snapshot_ids,
        "gross": gross,
        "turnover": turnover_summary,
        "cost": cost,
        "checks": [asdict(item) for item in result.all_checks],
        "status": result.status,
        "review_evaluated_at": result.evaluated_at,
    }
    encoded = json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{result.review_policy_schema_version}:{sha256(encoded).hexdigest()}"


__all__ = [
    "ELIGIBLE_FOR_PROMOTION_REVIEW",
    "FAIL",
    "INSUFFICIENT",
    "INSUFFICIENT_DATA",
    "INVALID",
    "INVALID_REVIEW_DATA",
    "NOT_ELIGIBLE",
    "NO_SHADOW_ENROLLMENT",
    "PASS",
    "RESULT_TYPE",
    "SHADOW_REVIEW_GATE_V1",
    "ShadowReviewGateCheckResult",
    "ShadowReviewGatePolicy",
    "ShadowReviewGateResult",
    "ShadowReviewGateService",
    "shadow_review_decision_signature",
    "shadow_review_policy_definition",
    "shadow_review_policy_signature",
    "validate_shadow_review_policy",
]
