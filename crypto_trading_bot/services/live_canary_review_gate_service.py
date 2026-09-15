from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from crypto_trading_bot.services.canary_trade_provenance_service import (
    CANARY_RECOMMENDATION,
)
from crypto_trading_bot.services.live_canary_evidence_service import (
    ACTIVE,
    EVIDENCE_AVAILABLE,
    EVIDENCE_SCHEMA_VERSION,
    EXHAUSTED,
    EXPIRED,
    INVALID_CANARY_EVIDENCE,
    NO_CANARY_ACTIVATION,
    NO_CANARY_RUNS,
    REPORT_TYPE as EVIDENCE_REPORT_TYPE,
    STOPPED,
    LiveCanaryEvidenceService,
    live_canary_evidence_signature,
)
from crypto_trading_bot.services.live_order_execution_service import (
    COUNTED_DAILY_LIVE_ORDER_STATUSES,
    KST,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
)


RESULT_TYPE = "LIVE_CANARY_REVIEW_GATE_V1_DECISION"
PASS = "PASS"
INSUFFICIENT = "INSUFFICIENT"
FAIL = "FAIL"
INVALID = "INVALID"
NO_CANARY = "NO_CANARY"
INVALID_CANARY_DATA = "INVALID_CANARY_DATA"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
ELIGIBLE_FOR_FULL_LIVE_REVIEW = "ELIGIBLE_FOR_FULL_LIVE_REVIEW"

_TERMINAL_EXECUTED_STATUSES = {
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
}
_INTEGRITY_FLAGS = (
    "activation_provenance_verified",
    "safety_binding_verified",
    "promotion_provenance_verified",
    "termination_provenance_verified",
    "all_canary_runs_verified",
    "all_recommendation_lineages_verified",
    "all_canary_order_lineages_verified",
    "snapshot_consistency_verified",
)
_EVIDENCE_FALSE_SAFETY_FLAGS = (
    "database_write",
    "external_calls",
    "live_policy_change",
    "live_order_change",
    "ranking_runtime_changed",
    "canary_state_changed",
    "promotion_performed",
    "full_live_promotion_performed",
    "sample_sufficiency_assessed",
    "policy_decision_performed",
    "statistical_inference_performed",
)


class LiveCanaryReviewGateError(ValueError):
    pass


@dataclass(frozen=True)
class LiveCanaryReviewGatePolicy:
    schema_version: str
    required_evidence_schema_version: str
    expected_canary_max_analysis_runs: int
    expected_canary_duration_hours: int
    required_reserved_run_count: int
    required_successful_run_count: int
    max_failed_run_count: int
    max_unfinished_run_count: int
    min_observation_span_hours: int
    min_successful_run_recommendation_coverage: Decimal
    min_submitted_canary_buy_order_count: int
    min_terminal_executed_canary_buy_order_count: int
    max_pipeline_failure_alert_count: int
    max_invalid_provenance_alert_count: int
    max_live_failed_order_count: int
    max_live_unknown_order_count: int
    max_pending_order_count: int
    max_unresolved_stale_order_count: int
    max_alert_delivery_failed_count: int
    max_legacy_unstructured_canary_alert_count: int
    max_approval_bypass_count: int
    max_ledger_integrity_failure_count: int
    max_cap_bypass_count: int
    terminal_lifecycle_states: tuple[str, ...]


LIVE_CANARY_REVIEW_GATE_V1 = LiveCanaryReviewGatePolicy(
    schema_version="live-canary-review-gate-v1",
    required_evidence_schema_version="live-canary-evidence-v1",
    expected_canary_max_analysis_runs=6,
    expected_canary_duration_hours=48,
    required_reserved_run_count=6,
    required_successful_run_count=6,
    max_failed_run_count=0,
    max_unfinished_run_count=0,
    min_observation_span_hours=36,
    min_successful_run_recommendation_coverage=Decimal("1"),
    min_submitted_canary_buy_order_count=1,
    min_terminal_executed_canary_buy_order_count=1,
    max_pipeline_failure_alert_count=0,
    max_invalid_provenance_alert_count=0,
    max_live_failed_order_count=0,
    max_live_unknown_order_count=0,
    max_pending_order_count=0,
    max_unresolved_stale_order_count=0,
    max_alert_delivery_failed_count=0,
    max_legacy_unstructured_canary_alert_count=0,
    max_approval_bypass_count=0,
    max_ledger_integrity_failure_count=0,
    max_cap_bypass_count=0,
    terminal_lifecycle_states=(EXPIRED, EXHAUSTED),
)


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise LiveCanaryReviewGateError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, field_name: str, *, minimum: Decimal | None = None):
    if isinstance(value, bool) or value is None:
        raise LiveCanaryReviewGateError(f"{field_name} must be a finite Decimal")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise LiveCanaryReviewGateError(
            f"{field_name} must be a finite Decimal"
        ) from error
    if not parsed.is_finite() or (minimum is not None and parsed < minimum):
        raise LiveCanaryReviewGateError(f"{field_name} is invalid")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise LiveCanaryReviewGateError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def validate_live_canary_review_policy(policy: LiveCanaryReviewGatePolicy) -> None:
    if not isinstance(policy, LiveCanaryReviewGatePolicy):
        raise LiveCanaryReviewGateError("LIVE Canary review policy is invalid")
    if (
        policy.schema_version != "live-canary-review-gate-v1"
        or policy.required_evidence_schema_version != EVIDENCE_SCHEMA_VERSION
        or policy.terminal_lifecycle_states != (EXPIRED, EXHAUSTED)
    ):
        raise LiveCanaryReviewGateError("LIVE Canary review policy schema is invalid")
    positive = (
        policy.expected_canary_max_analysis_runs,
        policy.expected_canary_duration_hours,
        policy.required_reserved_run_count,
        policy.required_successful_run_count,
        policy.min_observation_span_hours,
        policy.min_submitted_canary_buy_order_count,
        policy.min_terminal_executed_canary_buy_order_count,
    )
    maxima = (
        policy.max_failed_run_count,
        policy.max_unfinished_run_count,
        policy.max_pipeline_failure_alert_count,
        policy.max_invalid_provenance_alert_count,
        policy.max_live_failed_order_count,
        policy.max_live_unknown_order_count,
        policy.max_pending_order_count,
        policy.max_unresolved_stale_order_count,
        policy.max_alert_delivery_failed_count,
        policy.max_legacy_unstructured_canary_alert_count,
        policy.max_approval_bypass_count,
        policy.max_ledger_integrity_failure_count,
        policy.max_cap_bypass_count,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in positive
    ):
        raise LiveCanaryReviewGateError("LIVE Canary positive thresholds are invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in maxima
    ):
        raise LiveCanaryReviewGateError("LIVE Canary maximum thresholds are invalid")
    coverage = policy.min_successful_run_recommendation_coverage
    if (
        isinstance(coverage, bool)
        or not isinstance(coverage, Decimal)
        or not coverage.is_finite()
        or coverage < 0
        or coverage > 1
    ):
        raise LiveCanaryReviewGateError("LIVE Canary coverage threshold is invalid")
    if policy.required_successful_run_count > policy.required_reserved_run_count:
        raise LiveCanaryReviewGateError("LIVE Canary run thresholds are inconsistent")
    if policy.required_reserved_run_count != policy.expected_canary_max_analysis_runs:
        raise LiveCanaryReviewGateError("LIVE Canary v1 run contract is inconsistent")


def live_canary_review_policy_definition(
    policy: LiveCanaryReviewGatePolicy = LIVE_CANARY_REVIEW_GATE_V1,
) -> dict:
    validate_live_canary_review_policy(policy)
    return _canonicalize(asdict(policy))


def live_canary_review_policy_signature(
    policy: LiveCanaryReviewGatePolicy = LIVE_CANARY_REVIEW_GATE_V1,
) -> str:
    encoded = json.dumps(
        live_canary_review_policy_definition(policy),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{policy.schema_version}:{sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class LiveCanaryReviewGateCheckResult:
    check_id: str
    category: str
    status: str
    observed_value: str | None
    comparator: str | None
    threshold_value: str | None
    reason: str | None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class LiveCanaryReviewGateResult:
    result_type: str
    canary_activation_id: int
    candidate_id: int | None
    evidence: object | None
    evidence_schema_version: str | None
    evidence_signature: str | None
    evidence_as_of: datetime | None
    review_policy_schema_version: str
    review_policy_signature: str
    review_policy_definition: dict
    review_policy: LiveCanaryReviewGatePolicy
    evaluated_at: datetime
    status: str
    safe_reason: str | None
    eligible_for_full_live_review: bool
    passed_checks: tuple[LiveCanaryReviewGateCheckResult, ...]
    insufficient_checks: tuple[LiveCanaryReviewGateCheckResult, ...]
    failed_checks: tuple[LiveCanaryReviewGateCheckResult, ...]
    invalid_checks: tuple[LiveCanaryReviewGateCheckResult, ...]
    all_checks: tuple[LiveCanaryReviewGateCheckResult, ...]
    review_decision_signature: str | None
    evidence_provenance_verified: bool
    sample_sufficiency_assessed: bool
    statistical_inference_performed: bool
    policy_decision_performed: bool
    promotion_performed: bool
    full_live_promotion_performed: bool
    database_write: bool
    external_calls: bool
    live_policy_change: bool
    live_order_change: bool
    canary_state_changed: bool
    ranking_runtime_changed: bool


class LiveCanaryReviewGateService:
    """Evaluate one exact, signed Canary Evidence v1 report without side effects."""

    def __init__(
        self,
        session: Session,
        *,
        policy: LiveCanaryReviewGatePolicy = LIVE_CANARY_REVIEW_GATE_V1,
        evidence_service: LiveCanaryEvidenceService | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        validate_live_canary_review_policy(policy)
        self.policy = policy
        self.evidence_service = evidence_service or LiveCanaryEvidenceService(session)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def evaluate(self, *, canary_activation_id: int) -> LiveCanaryReviewGateResult:
        if (
            isinstance(canary_activation_id, bool)
            or not isinstance(canary_activation_id, int)
            or canary_activation_id < 1
        ):
            raise LiveCanaryReviewGateError(
                "canary_activation_id must be a positive integer"
            )

        # This must be the first operation: Evidence opens the frozen DB snapshot.
        evidence = self.evidence_service.evaluate(
            canary_activation_id=canary_activation_id
        )
        evaluated_at = _utc(self.now_fn(), "LIVE Canary review evaluated_at")
        evidence_status = getattr(evidence, "status", None)
        try:
            data = self._evidence_data(evidence, canary_activation_id)
        except (
            LiveCanaryReviewGateError,
            AttributeError,
            KeyError,
            TypeError,
        ) as error:
            return self._invalid_evidence_result(
                canary_activation_id, evidence, evaluated_at, str(error)
            )
        if evidence_status == NO_CANARY_ACTIVATION:
            return self._result(
                canary_activation_id,
                evidence,
                evaluated_at,
                (),
                explicit_status=NO_CANARY,
                provenance_verified=False,
                decision_performed=False,
            )
        if evidence_status == INVALID_CANARY_EVIDENCE:
            return self._result(
                canary_activation_id,
                evidence,
                evaluated_at,
                (
                    self._check(
                        "evidence.integrity",
                        "INTEGRITY",
                        INVALID,
                        reason="Evidence reported structural integrity failure",
                    ),
                ),
                explicit_status=INVALID_CANARY_DATA,
                provenance_verified=False,
                decision_performed=False,
            )
        if evidence_status not in {NO_CANARY_RUNS, EVIDENCE_AVAILABLE}:
            return self._invalid_evidence_result(
                canary_activation_id,
                evidence,
                evaluated_at,
                f"unsupported Evidence status: {evidence_status}",
            )
        try:
            checks = self._checks(data)
        except (
            LiveCanaryReviewGateError,
            AttributeError,
            KeyError,
            TypeError,
        ) as error:
            return self._invalid_evidence_result(
                canary_activation_id, evidence, evaluated_at, str(error)
            )
        has_invalid = any(item.status == INVALID for item in checks)
        return self._result(
            canary_activation_id,
            evidence,
            evaluated_at,
            checks,
            provenance_verified=not has_invalid,
            decision_performed=not has_invalid,
        )

    def _evidence_data(self, evidence, requested_id: int) -> dict[str, Any]:
        data = evidence.as_dict()
        if not isinstance(data, dict):
            raise LiveCanaryReviewGateError("Evidence payload must be a dict")
        if data.get("report_type") != EVIDENCE_REPORT_TYPE:
            raise LiveCanaryReviewGateError("Evidence report_type is invalid")
        if (
            data.get("evidence_schema_version")
            != self.policy.required_evidence_schema_version
        ):
            raise LiveCanaryReviewGateError("Evidence schema version is invalid")
        if data.get("canary_activation_id") != requested_id:
            raise LiveCanaryReviewGateError("Evidence activation identity is invalid")
        signature = data.get("evidence_signature")
        if not isinstance(signature, str):
            raise LiveCanaryReviewGateError("Evidence signature is missing")
        unsigned = dict(data)
        unsigned.pop("evidence_signature", None)
        if signature != live_canary_evidence_signature(unsigned):
            raise LiveCanaryReviewGateError("Evidence signature verification failed")
        if data.get("snapshot_consistency") != "REPEATABLE_READ_READ_ONLY":
            raise LiveCanaryReviewGateError("Evidence snapshot contract is invalid")
        safety = self._mapping(data, "safety_flags")
        if any(safety.get(name) is not False for name in _EVIDENCE_FALSE_SAFETY_FLAGS):
            raise LiveCanaryReviewGateError("Evidence safety flags are invalid")
        return data

    def _checks(self, data: dict[str, Any]):
        policy = self.policy
        lifecycle = data.get("lifecycle_state")
        activation = self._mapping(data, "activation_provenance")
        binding = self._mapping(data, "safety_binding_provenance")
        runs = self._list(data, "runs")
        run_summary = self._mapping(data, "run_summary")
        recommendations = self._list(
            self._mapping(data, "recommendation_evidence"), "recommendations"
        )
        approvals = self._list(
            self._mapping(data, "approval_evidence"), "approval_requests"
        )
        order_evidence = self._mapping(data, "order_evidence")
        orders = self._list(order_evidence, "orders")
        order_summary = self._mapping(order_evidence, "summary")
        operational = self._mapping(data, "operational_evidence")
        alerts = self._list(operational, "alerts")
        alert_summary = self._mapping(operational, "summary")
        integrity = self._mapping(data, "integrity")
        safety = self._mapping(data, "safety_flags")
        checks: list[LiveCanaryReviewGateCheckResult] = []

        identity_ok = activation.get("canary_activation_id") == data.get(
            "canary_activation_id"
        ) and activation.get("candidate_id") == data.get("candidate_id")
        checks.append(
            self._check(
                "evidence.identity",
                "INTEGRITY",
                PASS if identity_ok else INVALID,
                observed=identity_ok,
                comparator="==",
                threshold=True,
                reason=None
                if identity_ok
                else "Evidence activation or candidate identity is inconsistent",
            )
        )
        checks.append(self._check("evidence.signature", "INTEGRITY", PASS))
        snapshot_ok = (
            data.get("snapshot_consistency") == "REPEATABLE_READ_READ_ONLY"
            and integrity.get("snapshot_consistency_verified") is True
        )
        checks.append(
            self._check(
                "evidence.snapshot",
                "INTEGRITY",
                PASS if snapshot_ok else INVALID,
                observed=data.get("snapshot_consistency"),
                comparator="==",
                threshold="REPEATABLE_READ_READ_ONLY",
                reason=None if snapshot_ok else "Evidence snapshot contract is invalid",
            )
        )
        integrity_ok = (
            all(integrity.get(name) is True for name in _INTEGRITY_FLAGS)
            and integrity.get("findings") == []
            and all(safety.get(name) is False for name in _EVIDENCE_FALSE_SAFETY_FLAGS)
        )
        checks.append(
            self._check(
                "evidence.integrity",
                "INTEGRITY",
                PASS if integrity_ok else INVALID,
                observed=integrity_ok,
                comparator="==",
                threshold=True,
                reason=None
                if integrity_ok
                else "Evidence integrity or safety flags are invalid",
            )
        )

        started = _utc(activation.get("started_at"), "Canary started_at")
        expires = _utc(activation.get("expires_at"), "Canary expires_at")
        max_runs = activation.get("max_analysis_runs")
        policy_compatible = (
            max_runs == policy.expected_canary_max_analysis_runs
            and expires - started
            == timedelta(hours=policy.expected_canary_duration_hours)
        )
        checks.append(
            self._check(
                "canary.policy_compatibility",
                "INTEGRITY",
                PASS if policy_compatible else INVALID,
                observed={
                    "max_runs": max_runs,
                    "duration_seconds": (expires - started).total_seconds(),
                },
                comparator="==",
                threshold={
                    "max_runs": policy.expected_canary_max_analysis_runs,
                    "duration_seconds": policy.expected_canary_duration_hours * 3600,
                },
                reason=None
                if policy_compatible
                else "Canary activation is incompatible with Review Gate v1",
            )
        )
        if lifecycle == ACTIVE:
            lifecycle_status = INSUFFICIENT
            lifecycle_reason = "Canary is still active"
        elif lifecycle == STOPPED:
            lifecycle_status = FAIL
            lifecycle_reason = "Manually stopped Canary requires a fresh Canary"
        elif lifecycle in policy.terminal_lifecycle_states:
            lifecycle_status = PASS
            lifecycle_reason = None
        else:
            lifecycle_status = INVALID
            lifecycle_reason = "Canary lifecycle is invalid"
        checks.append(
            self._check(
                "canary.lifecycle_terminal",
                "LIFECYCLE",
                lifecycle_status,
                observed=lifecycle,
                comparator="in",
                threshold=policy.terminal_lifecycle_states,
                reason=lifecycle_reason,
            )
        )

        reserved = self._nonnegative_int(
            run_summary.get("reserved_run_count"), "reserved run count"
        )
        successful = self._nonnegative_int(
            run_summary.get("successful_market_universe_run_count"),
            "successful run count",
        )
        failed = self._nonnegative_int(
            run_summary.get("failed_market_universe_run_count"), "failed run count"
        )
        unfinished = self._nonnegative_int(
            run_summary.get("unfinished_market_universe_run_count"),
            "unfinished run count",
        )
        if reserved != len(runs) or successful + failed + unfinished != reserved:
            checks.append(
                self._check(
                    "sample.run_summary_integrity",
                    "INTEGRITY",
                    INVALID,
                    reason="Run summary does not match Evidence runs",
                )
            )
        reserved_status = (
            PASS
            if reserved == policy.required_reserved_run_count
            else INVALID
            if reserved > policy.required_reserved_run_count
            else INSUFFICIENT
        )
        checks.append(
            self._threshold(
                "sample.reserved_runs",
                "SUFFICIENCY",
                reserved,
                "==",
                policy.required_reserved_run_count,
                reserved_status,
            )
        )
        successful_status = (
            PASS
            if successful == policy.required_successful_run_count
            else INVALID
            if successful > reserved
            else INSUFFICIENT
        )
        checks.append(
            self._threshold(
                "sample.successful_runs",
                "SUFFICIENCY",
                successful,
                "==",
                policy.required_successful_run_count,
                successful_status,
            )
        )
        checks.append(
            self._threshold(
                "sample.failed_runs",
                "PIPELINE",
                failed,
                "<=",
                policy.max_failed_run_count,
                PASS if failed <= policy.max_failed_run_count else FAIL,
            )
        )
        unfinished_status = (
            PASS
            if unfinished <= policy.max_unfinished_run_count
            else INSUFFICIENT
            if lifecycle == ACTIVE
            else FAIL
        )
        checks.append(
            self._threshold(
                "sample.unfinished_runs",
                "PIPELINE",
                unfinished,
                "<=",
                policy.max_unfinished_run_count,
                unfinished_status,
            )
        )

        reserved_times = [
            _utc(item.get("reserved_at"), "Canary run reserved_at") for item in runs
        ]
        span_hours = Decimal("0")
        if len(reserved_times) >= 2:
            span_hours = Decimal(
                str((max(reserved_times) - min(reserved_times)).total_seconds())
            ) / Decimal("3600")
        span_status = (
            PASS if span_hours >= policy.min_observation_span_hours else INSUFFICIENT
        )
        checks.append(
            self._threshold(
                "sample.observation_span",
                "SUFFICIENCY",
                span_hours,
                ">=",
                policy.min_observation_span_hours,
                span_status,
            )
        )

        successful_pipeline_ids = {
            item.get("pipeline_run_id")
            for item in runs
            if item.get("market_universe_analysis_status") == "SUCCESS"
        }
        covered = {
            item.get("pipeline_run_id")
            for item in recommendations
            if item.get("provenance_mode") == CANARY_RECOMMENDATION
            and item.get("provenance_valid") is True
            and item.get("pipeline_run_id") in successful_pipeline_ids
        }
        coverage = (
            Decimal(len(covered)) / Decimal(len(successful_pipeline_ids))
            if successful_pipeline_ids
            else Decimal("0")
        )
        coverage_ok = coverage >= policy.min_successful_run_recommendation_coverage
        coverage_status = (
            PASS if coverage_ok else INSUFFICIENT if lifecycle == ACTIVE else FAIL
        )
        checks.append(
            self._threshold(
                "recommendation.run_coverage",
                "PIPELINE",
                coverage,
                ">=",
                policy.min_successful_run_recommendation_coverage,
                coverage_status,
            )
        )

        submitted = [
            item
            for item in orders
            if item.get("side") == "BUY"
            and not item.get("preflight_failed_before_canary_audit", False)
        ]
        submitted_count = len(submitted)
        checks.append(
            self._threshold(
                "live_sample.submitted_buy",
                "SUFFICIENCY",
                submitted_count,
                ">=",
                policy.min_submitted_canary_buy_order_count,
                PASS
                if submitted_count >= policy.min_submitted_canary_buy_order_count
                else INSUFFICIENT,
            )
        )
        executed = [item for item in submitted if self._is_terminal_executed_buy(item)]
        checks.append(
            self._threshold(
                "live_sample.executed_buy",
                "SUFFICIENCY",
                len(executed),
                ">=",
                policy.min_terminal_executed_canary_buy_order_count,
                PASS
                if len(executed) >= policy.min_terminal_executed_canary_buy_order_count
                else INSUFFICIENT,
            )
        )

        approvals_by_id = {item.get("approval_request_id"): item for item in approvals}
        approval_bypass = sum(
            not self._approved_order(item, approvals_by_id) for item in submitted
        )
        checks.append(
            self._threshold(
                "approval.no_bypass",
                "APPROVAL_SAFETY",
                approval_bypass,
                "<=",
                policy.max_approval_bypass_count,
                PASS if approval_bypass <= policy.max_approval_bypass_count else FAIL,
            )
        )

        per_order_cap = _decimal(
            binding.get("max_buy_order_amount_krw"),
            "per-order cap",
            minimum=Decimal("0"),
        )
        daily_cap = _decimal(
            binding.get("daily_max_buy_amount_krw"), "daily cap", minimum=Decimal("0")
        )
        if per_order_cap <= 0 or daily_cap <= 0:
            raise LiveCanaryReviewGateError("Stored Canary BUY caps must be positive")
        per_order_bypass = sum(
            _decimal(
                item.get("amount_krw"), "submitted BUY amount", minimum=Decimal("0")
            )
            > per_order_cap
            for item in submitted
        )
        checks.append(
            self._threshold(
                "safety.per_order_cap",
                "ORDER_SAFETY",
                per_order_bypass,
                "<=",
                policy.max_cap_bypass_count,
                PASS if per_order_bypass <= policy.max_cap_bypass_count else FAIL,
            )
        )
        daily_bypass = self._daily_cap_bypass_count(submitted, daily_cap)
        checks.append(
            self._threshold(
                "safety.daily_cap",
                "ORDER_SAFETY",
                daily_bypass,
                "<=",
                policy.max_cap_bypass_count,
                PASS if daily_bypass <= policy.max_cap_bypass_count else FAIL,
            )
        )

        invalid_provenance = self._summary_count(
            alert_summary, "invalid_provenance_alert_count"
        )
        pipeline_failures = self._summary_count(
            alert_summary, "pipeline_failure_alert_count"
        )
        live_failed = self._summary_count(order_summary, "live_failed_count")
        live_unknown = self._summary_count(order_summary, "live_unknown_count")
        pending = self._summary_count(order_summary, "pending_order_count")
        unresolved_stale = sum(
            item.get("alert_type") == "STALE_LIVE_ORDER"
            and item.get("resolved_at") is None
            for item in alerts
        )
        delivery_failed = self._summary_count(
            alert_summary, "alert_delivery_failed_count"
        )
        legacy = self._summary_count(
            alert_summary, "legacy_unstructured_canary_alert_count"
        )
        started_alerts = self._summary_count(
            alert_summary, "canary_started_alert_count"
        )
        checks.extend(
            (
                self._maximum(
                    "safety.invalid_provenance",
                    "ORDER_SAFETY",
                    invalid_provenance,
                    policy.max_invalid_provenance_alert_count,
                ),
                self._maximum(
                    "pipeline.failures",
                    "PIPELINE",
                    pipeline_failures,
                    policy.max_pipeline_failure_alert_count,
                ),
                self._maximum(
                    "orders.live_failed",
                    "EXECUTION_INTEGRITY",
                    live_failed,
                    policy.max_live_failed_order_count,
                ),
                self._maximum(
                    "orders.live_unknown",
                    "EXECUTION_INTEGRITY",
                    live_unknown,
                    policy.max_live_unknown_order_count,
                ),
                self._threshold(
                    "orders.pending",
                    "EXECUTION_INTEGRITY",
                    pending,
                    "<=",
                    policy.max_pending_order_count,
                    PASS
                    if pending <= policy.max_pending_order_count
                    else INSUFFICIENT
                    if lifecycle == ACTIVE
                    else FAIL,
                ),
                self._maximum(
                    "orders.unresolved_stale",
                    "OPERATIONAL_OBSERVABILITY",
                    unresolved_stale,
                    policy.max_unresolved_stale_order_count,
                ),
                self._maximum(
                    "alerts.delivery",
                    "OPERATIONAL_OBSERVABILITY",
                    delivery_failed,
                    policy.max_alert_delivery_failed_count,
                ),
                self._maximum(
                    "alerts.structured",
                    "OPERATIONAL_OBSERVABILITY",
                    legacy,
                    policy.max_legacy_unstructured_canary_alert_count,
                ),
                self._threshold(
                    "alerts.canary_started",
                    "OPERATIONAL_OBSERVABILITY",
                    started_alerts,
                    "==",
                    1,
                    PASS if started_alerts == 1 else FAIL,
                ),
            )
        )
        ledger_ok = integrity.get("order_fill_integrity_verified") is True
        checks.append(
            self._threshold(
                "ledger.integrity",
                "EXECUTION_INTEGRITY",
                0 if ledger_ok else 1,
                "<=",
                policy.max_ledger_integrity_failure_count,
                PASS if ledger_ok else INVALID,
                reason=None
                if ledger_ok
                else "OrderLog and OrderFill integrity is invalid",
            )
        )
        return tuple(checks)

    @staticmethod
    def _mapping(value: dict, name: str) -> dict:
        result = value.get(name)
        if not isinstance(result, dict):
            raise LiveCanaryReviewGateError(f"Evidence {name} must be a dict")
        return result

    @staticmethod
    def _list(value: dict, name: str) -> list:
        result = value.get(name)
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise LiveCanaryReviewGateError(f"Evidence {name} must be a list of dicts")
        return result

    @staticmethod
    def _nonnegative_int(value, field_name):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LiveCanaryReviewGateError(f"{field_name} is invalid")
        return value

    @staticmethod
    def _summary_count(summary, name):
        return LiveCanaryReviewGateService._nonnegative_int(summary.get(name), name)

    @staticmethod
    def _is_terminal_executed_buy(item):
        if item.get("status") not in _TERMINAL_EXECUTED_STATUSES:
            return False
        try:
            return (
                _decimal(item.get("executed_quantity"), "executed quantity") > 0
                and _decimal(item.get("executed_funds_krw"), "executed funds") > 0
            )
        except LiveCanaryReviewGateError:
            return False

    @staticmethod
    def _approved_order(item, approvals_by_id):
        approval = approvals_by_id.get(item.get("approval_request_id"))
        return (
            approval is not None
            and approval.get("recommendation_id") == item.get("recommendation_id")
            and approval.get("status") == "APPROVED"
        )

    @staticmethod
    def _daily_cap_bypass_count(orders, daily_cap):
        totals: dict[object, Decimal] = {}
        for item in orders:
            if item.get("status") not in COUNTED_DAILY_LIVE_ORDER_STATUSES:
                continue
            created_at = _utc(item.get("created_at"), "Order created_at")
            day = created_at.astimezone(KST).date()
            totals[day] = totals.get(day, Decimal("0")) + _decimal(
                item.get("amount_krw"), "counted BUY amount", minimum=Decimal("0")
            )
        return sum(total > daily_cap for total in totals.values())

    def _maximum(self, check_id, category, observed, maximum):
        return self._threshold(
            check_id,
            category,
            observed,
            "<=",
            maximum,
            PASS if observed <= maximum else FAIL,
        )

    def _threshold(
        self,
        check_id,
        category,
        observed,
        comparator,
        threshold,
        status,
        *,
        reason=None,
    ):
        return self._check(
            check_id,
            category,
            status,
            observed=observed,
            comparator=comparator,
            threshold=threshold,
            reason=reason,
        )

    @staticmethod
    def _check(
        check_id,
        category,
        status,
        *,
        observed=None,
        comparator=None,
        threshold=None,
        reason=None,
        details=None,
    ):
        if status not in {PASS, INSUFFICIENT, FAIL, INVALID}:
            raise LiveCanaryReviewGateError("Review check status is invalid")

        def text(value):
            if value is None:
                return None
            canonical = _canonicalize(value)
            return (
                canonical
                if isinstance(canonical, str)
                else json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            )

        return LiveCanaryReviewGateCheckResult(
            check_id=check_id,
            category=category,
            status=status,
            observed_value=text(observed),
            comparator=comparator,
            threshold_value=text(threshold),
            reason=reason,
            details=details,
        )

    def _invalid_evidence_result(self, activation_id, evidence, evaluated_at, reason):
        return self._result(
            activation_id,
            evidence,
            evaluated_at,
            (self._check("evidence.identity", "INTEGRITY", INVALID, reason=reason),),
            explicit_status=INVALID_CANARY_DATA,
            provenance_verified=False,
            decision_performed=False,
        )

    def _result(
        self,
        activation_id,
        evidence,
        evaluated_at,
        checks,
        *,
        provenance_verified,
        decision_performed,
        explicit_status=None,
    ):
        values = tuple(checks)
        status = explicit_status or (
            INVALID_CANARY_DATA
            if any(item.status == INVALID for item in values)
            else NOT_ELIGIBLE
            if any(item.status == FAIL for item in values)
            else INSUFFICIENT_DATA
            if any(item.status == INSUFFICIENT for item in values)
            else ELIGIBLE_FOR_FULL_LIVE_REVIEW
        )
        reasons = {
            NO_CANARY: "Canary activation does not exist",
            INVALID_CANARY_DATA: "Canary Evidence integrity validation failed",
            INSUFFICIENT_DATA: "Canary evidence does not meet v1 sample requirements",
            NOT_ELIGIBLE: "Canary evidence failed v1 runtime safety checks",
            ELIGIBLE_FOR_FULL_LIVE_REVIEW: None,
        }
        result = LiveCanaryReviewGateResult(
            result_type=RESULT_TYPE,
            canary_activation_id=activation_id,
            candidate_id=getattr(evidence, "candidate_id", None),
            evidence=evidence,
            evidence_schema_version=(
                EVIDENCE_SCHEMA_VERSION if evidence is not None else None
            ),
            evidence_signature=getattr(evidence, "evidence_signature", None),
            evidence_as_of=getattr(evidence, "evidence_as_of", None),
            review_policy_schema_version=self.policy.schema_version,
            review_policy_signature=live_canary_review_policy_signature(self.policy),
            review_policy_definition=live_canary_review_policy_definition(self.policy),
            review_policy=self.policy,
            evaluated_at=evaluated_at,
            status=status,
            safe_reason=reasons[status],
            eligible_for_full_live_review=status == ELIGIBLE_FOR_FULL_LIVE_REVIEW,
            passed_checks=tuple(item for item in values if item.status == PASS),
            insufficient_checks=tuple(
                item for item in values if item.status == INSUFFICIENT
            ),
            failed_checks=tuple(item for item in values if item.status == FAIL),
            invalid_checks=tuple(item for item in values if item.status == INVALID),
            all_checks=values,
            review_decision_signature=None,
            evidence_provenance_verified=provenance_verified,
            sample_sufficiency_assessed=decision_performed,
            statistical_inference_performed=False,
            policy_decision_performed=decision_performed,
            promotion_performed=False,
            full_live_promotion_performed=False,
            database_write=False,
            external_calls=False,
            live_policy_change=False,
            live_order_change=False,
            canary_state_changed=False,
            ranking_runtime_changed=False,
        )
        if decision_performed and evidence is not None:
            result = replace(
                result,
                review_decision_signature=live_canary_review_decision_signature(result),
            )
        return result


def live_canary_review_decision_payload(result: LiveCanaryReviewGateResult) -> dict:
    if result.evidence_signature is None or not result.policy_decision_performed:
        raise LiveCanaryReviewGateError(
            "Review decision signature requires verified Evidence"
        )
    return _canonicalize(
        {
            "result_type": result.result_type,
            "canary_activation_id": result.canary_activation_id,
            "candidate_id": result.candidate_id,
            "evidence_schema_version": result.evidence_schema_version,
            "evidence_signature": result.evidence_signature,
            "evidence_as_of": result.evidence_as_of,
            "review_policy_schema_version": result.review_policy_schema_version,
            "review_policy_signature": result.review_policy_signature,
            "review_policy_definition": result.review_policy_definition,
            "checks": [asdict(item) for item in result.all_checks],
            "status": result.status,
            "eligible_for_full_live_review": result.eligible_for_full_live_review,
            "review_evaluated_at": result.evaluated_at,
        }
    )


def live_canary_review_decision_signature(result: LiveCanaryReviewGateResult) -> str:
    encoded = json.dumps(
        live_canary_review_decision_payload(result),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{result.review_policy_schema_version}:{sha256(encoded).hexdigest()}"


__all__ = [
    "ELIGIBLE_FOR_FULL_LIVE_REVIEW",
    "FAIL",
    "INSUFFICIENT",
    "INSUFFICIENT_DATA",
    "INVALID",
    "INVALID_CANARY_DATA",
    "LIVE_CANARY_REVIEW_GATE_V1",
    "LiveCanaryReviewGateCheckResult",
    "LiveCanaryReviewGateError",
    "LiveCanaryReviewGatePolicy",
    "LiveCanaryReviewGateResult",
    "LiveCanaryReviewGateService",
    "NOT_ELIGIBLE",
    "NO_CANARY",
    "PASS",
    "RESULT_TYPE",
    "live_canary_review_decision_payload",
    "live_canary_review_decision_signature",
    "live_canary_review_policy_definition",
    "live_canary_review_policy_signature",
    "validate_live_canary_review_policy",
]
