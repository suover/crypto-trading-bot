"""Absolute, read-only screening of reference-bounded historical candidates."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DEFAULT_STEP,
)
from crypto_trading_bot.services.historical_candidate_screening_policy import (
    FAIL,
    INSUFFICIENT,
    INVALID,
    PASS,
    SCREENING_POLICY_SCHEMA_VERSION,
    HistoricalCandidateScreeningEvaluator,
    HistoricalScreeningCheckResult,
    HistoricalScreeningEvidence,
    HistoricalScreeningThresholds,
    screening_policy_signature,
    thresholds_from_promotion_policy,
)
from crypto_trading_bot.services.policy_promotion_gate_service import (
    POLICY_PROMOTION_GATE_V1,
    gate_policy_signature,
)
from crypto_trading_bot.services.reference_bounded_historical_research_batch_service import (
    BATCH_SCHEMA_VERSION,
    INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH,
    NO_HISTORICAL_CONTEXT_SNAPSHOTS,
    NO_NOVEL_CANDIDATES,
    RESEARCH_PROFILE_SCHEMA_VERSION,
    SUCCESS as BATCH_SUCCESS,
    HistoricalResearchCandidateResult,
    ReferenceBoundedHistoricalResearchBatchResult,
    ReferenceBoundedHistoricalResearchBatchService,
)


REPORT_TYPE = "HISTORICAL_CANDIDATE_SCREENING_GATE"
GATE_SCHEMA_VERSION = "historical-candidate-screening-gate-v1"
SUCCESS = "SUCCESS"
NO_CANDIDATES_TO_SCREEN = "NO_CANDIDATES_TO_SCREEN"
SCREENING_INSUFFICIENT_DATA = "SCREENING_INSUFFICIENT_DATA"
INVALID_SCREENING_DATA = "INVALID_SCREENING_DATA"


@dataclass(frozen=True)
class HistoricalCandidateScreeningResult:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    donor_field: str
    receiver_field: str
    transfer_step: Decimal
    reference_snapshot_id: int
    reference_policy_signature: str
    status: str
    safe_reason: str | None
    passed_checks: tuple[HistoricalScreeningCheckResult, ...]
    insufficient_checks: tuple[HistoricalScreeningCheckResult, ...]
    failed_checks: tuple[HistoricalScreeningCheckResult, ...]
    invalid_checks: tuple[HistoricalScreeningCheckResult, ...]
    all_checks: tuple[HistoricalScreeningCheckResult, ...]


@dataclass(frozen=True)
class HistoricalCandidateScreeningGateResult:
    report_type: str
    gate_schema_version: str
    screening_policy_schema_version: str
    screening_policy_signature: str
    screening_policy: HistoricalScreeningThresholds
    source_promotion_gate_policy_schema_version: str
    source_promotion_gate_policy_signature: str
    source_batch_schema_version: str
    source_research_profile_schema_version: str
    reference_snapshot_id: int
    historical_evidence_as_of: datetime | None
    user_id: int | None
    exchange: str | None
    quote_asset: str | None
    baseline_policy_signature: str | None
    effective_top_n: int | None
    candidate_count: int
    pass_count: int
    fail_count: int
    insufficient_count: int
    invalid_count: int
    candidate_results: tuple[HistoricalCandidateScreeningResult, ...]
    passed_candidate_signatures: tuple[str, ...]
    status: str
    safe_reason: str | None
    research_only: bool = True
    historical_screening_performed: bool = True
    policy_decision_performed: bool = True
    automatic_policy_selection: bool = False
    candidate_registration_performed: bool = False
    promotion_performed: bool = False
    shadow_policy_created: bool = False
    full_live_activation_performed: bool = False
    database_write: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    live_order_change: bool = False
    sample_sufficiency_assessed: bool = True
    statistical_inference_performed: bool = False


class _InvalidScreeningData(ValueError):
    pass


class HistoricalCandidateScreeningGateService:
    def __init__(self, session: Session, *, batch_service=None) -> None:
        self.session = session
        self.batch_service = (
            batch_service or ReferenceBoundedHistoricalResearchBatchService(session)
        )
        self.promotion_policy = POLICY_PROMOTION_GATE_V1
        self.screening_policy = thresholds_from_promotion_policy(self.promotion_policy)
        self.evaluator = HistoricalCandidateScreeningEvaluator(self.screening_policy)

    def evaluate(
        self,
        *,
        reference_snapshot_id: int,
        step: Decimal | str = DEFAULT_STEP,
    ) -> HistoricalCandidateScreeningGateResult:
        batch = self.batch_service.evaluate(
            reference_snapshot_id=reference_snapshot_id, step=step
        )
        return self.evaluate_from_batch(batch)

    def evaluate_from_batch(
        self, batch: ReferenceBoundedHistoricalResearchBatchResult
    ) -> HistoricalCandidateScreeningGateResult:
        try:
            self._validate_batch(batch)
        except (AttributeError, TypeError, _InvalidScreeningData, ValueError) as error:
            return self._result(
                batch,
                (),
                status=INVALID_SCREENING_DATA,
                safe_reason=str(error),
                screening_performed=False,
            )
        if batch.status == INVALID_REFERENCE_BOUNDED_HISTORICAL_RESEARCH_BATCH:
            return self._result(
                batch,
                (),
                status=INVALID_SCREENING_DATA,
                safe_reason=batch.safe_reason or "source batch is invalid",
                screening_performed=False,
            )
        if batch.status == NO_NOVEL_CANDIDATES:
            return self._result(
                batch, (), status=NO_CANDIDATES_TO_SCREEN, screening_performed=False
            )
        if batch.status == NO_HISTORICAL_CONTEXT_SNAPSHOTS:
            return self._result(
                batch,
                (),
                status=SCREENING_INSUFFICIENT_DATA,
                safe_reason=batch.safe_reason
                or "source batch has no historical context snapshots",
                screening_performed=False,
            )
        if batch.status != BATCH_SUCCESS:
            return self._result(
                batch,
                (),
                status=INVALID_SCREENING_DATA,
                safe_reason="source batch status is unsupported",
                screening_performed=False,
            )
        results = tuple(
            self._candidate(batch, item) for item in batch.candidate_results
        )
        return self._result(batch, results, status=SUCCESS)

    def _validate_batch(self, batch) -> None:
        if not isinstance(batch, ReferenceBoundedHistoricalResearchBatchResult):
            raise _InvalidScreeningData("source batch type is invalid")
        if batch.batch_schema_version != BATCH_SCHEMA_VERSION:
            raise _InvalidScreeningData("source batch schema is unsupported")
        if batch.research_profile.schema_version != RESEARCH_PROFILE_SCHEMA_VERSION:
            raise _InvalidScreeningData("source research profile schema is unsupported")
        profile = batch.research_profile
        policy = self.promotion_policy
        assumptions = profile.assumptions
        if (
            profile.horizons != policy.required_horizons
            or profile.initial_research_size != policy.historical_initial_research_size
            or profile.validation_size != policy.historical_validation_size
            or assumptions.fee_rate != policy.fee_rate
            or assumptions.spread_cost_rate != policy.spread_cost_rate
            or assumptions.slippage_rate != policy.slippage_rate
        ):
            raise _InvalidScreeningData(
                "source research profile does not match promotion historical policy"
            )
        if not all(
            (
                batch.research_only,
                batch.reference_time_bounded,
                batch.post_reference_snapshots_excluded,
                batch.post_reference_outcomes_excluded,
            )
        ) or any(
            (
                batch.automatic_policy_selection,
                batch.policy_decision_performed,
                batch.candidate_registration_performed,
                batch.promotion_performed,
                batch.shadow_policy_created,
                batch.full_live_activation_performed,
                batch.database_write,
                batch.external_calls,
                batch.live_policy_change,
                batch.live_order_change,
            )
        ):
            raise _InvalidScreeningData("source batch safety provenance is invalid")
        if (
            isinstance(batch.reference_snapshot_id, bool)
            or not isinstance(batch.reference_snapshot_id, int)
            or batch.reference_snapshot_id < 1
        ):
            raise _InvalidScreeningData("reference snapshot identity is invalid")
        if batch.status in (BATCH_SUCCESS, NO_NOVEL_CANDIDATES) and (
            not isinstance(batch.reference_policy_signature, str)
            or not batch.reference_policy_signature
            or isinstance(batch.effective_top_n, bool)
            or not isinstance(batch.effective_top_n, int)
            or batch.effective_top_n < 1
        ):
            raise _InvalidScreeningData("reference policy identity is invalid")
        if batch.status in (BATCH_SUCCESS, NO_NOVEL_CANDIDATES) and (
            isinstance(batch.user_id, bool)
            or not isinstance(batch.user_id, int)
            or batch.user_id < 1
            or not isinstance(batch.exchange, str)
            or not batch.exchange
            or not isinstance(batch.quote_asset, str)
            or not batch.quote_asset
            or not isinstance(batch.historical_evidence_as_of, datetime)
            or batch.historical_evidence_as_of.tzinfo is None
            or batch.historical_evidence_as_of.utcoffset() is None
        ):
            raise _InvalidScreeningData("source batch cohort identity is invalid")
        if batch.status == BATCH_SUCCESS:
            generator = batch.generator
            if generator is None:
                raise _InvalidScreeningData("source generator result is missing")
            novel = tuple(
                item for item in generator.all_candidates if not item.already_registered
            )
            if (
                batch.evaluated_candidate_count != len(batch.candidate_results)
                or batch.novel_candidate_count != len(novel)
                or len(batch.candidate_results) != len(novel)
            ):
                raise _InvalidScreeningData("source candidate counts are inconsistent")
            expected = tuple(item.scenario_definition_signature for item in novel)
            actual = tuple(
                item.scenario_definition_signature for item in batch.candidate_results
            )
            if actual != expected:
                raise _InvalidScreeningData(
                    "source candidate order or identity does not match generator"
                )
            if any(
                result.scenario_name != source.scenario_name
                or result.scenario_definition_signature
                != source.scenario_definition_signature
                or result.component_weights != source.component_weights
                or result.reference_snapshot_id != source.reference_snapshot_id
                or result.reference_policy_signature
                != source.reference_policy_signature
                or result.already_registered
                for result, source in zip(batch.candidate_results, novel, strict=True)
            ):
                raise _InvalidScreeningData(
                    "source candidate fields do not match generator provenance"
                )

    def _candidate(self, batch, candidate):
        checks = []
        try:
            self._validate_candidate_identity(batch, candidate)
            horizons = self._validated_horizons(batch, candidate)
            upstream = self._upstream_check(candidate, horizons)
            checks.append(upstream)
            screening = self.evaluator.evaluate(
                self._screening_evidence(item) for item in horizons
            )
            checks.extend(screening.all_checks)
        except (AttributeError, TypeError, _InvalidScreeningData) as error:
            checks.append(
                HistoricalScreeningCheckResult(
                    "CANDIDATE_EVIDENCE_INTEGRITY",
                    "INTEGRITY",
                    INVALID,
                    None,
                    None,
                    None,
                    None,
                    str(error),
                )
            )
        values = tuple(checks)
        status = (
            INVALID
            if any(item.status == INVALID for item in values)
            else INSUFFICIENT
            if any(item.status == INSUFFICIENT for item in values)
            else FAIL
            if any(item.status == FAIL for item in values)
            else PASS
        )
        reasons = {
            INVALID: "historical candidate evidence integrity validation failed",
            INSUFFICIENT: "historical evidence does not meet minimum sample requirements",
            FAIL: "sufficient historical evidence failed stability requirements",
            PASS: None,
        }
        return HistoricalCandidateScreeningResult(
            candidate.scenario_name,
            candidate.scenario_definition_signature,
            dict(candidate.component_weights),
            candidate.donor_field,
            candidate.receiver_field,
            candidate.transfer_step,
            candidate.reference_snapshot_id,
            candidate.reference_policy_signature,
            status,
            reasons[status],
            tuple(item for item in values if item.status == PASS),
            tuple(item for item in values if item.status == INSUFFICIENT),
            tuple(item for item in values if item.status == FAIL),
            tuple(item for item in values if item.status == INVALID),
            values,
        )

    @staticmethod
    def _validate_candidate_identity(batch, candidate) -> None:
        if not isinstance(candidate, HistoricalResearchCandidateResult):
            raise _InvalidScreeningData("candidate result type is invalid")
        if (
            candidate.already_registered
            or candidate.reference_snapshot_id != batch.reference_snapshot_id
            or candidate.reference_policy_signature != batch.reference_policy_signature
            or not candidate.scenario_name
            or not candidate.scenario_definition_signature
            or not isinstance(candidate.component_weights, dict)
            or not candidate.component_weights
        ):
            raise _InvalidScreeningData("candidate identity is invalid")

    def _validated_horizons(self, batch, candidate):
        values = candidate.horizons
        indexed = {item.horizon_minutes: item for item in values}
        if len(indexed) != len(values) or set(indexed) != set(
            self.screening_policy.required_horizons
        ):
            raise _InvalidScreeningData(
                "candidate horizons are missing, duplicated, or unexpected"
            )
        ordered = tuple(
            indexed[value] for value in self.screening_policy.required_horizons
        )
        if any(
            item.baseline_policy_signature != batch.reference_policy_signature
            or item.effective_top_n != batch.effective_top_n
            for item in ordered
        ):
            raise _InvalidScreeningData("candidate horizon identity is invalid")
        return ordered

    @staticmethod
    def _upstream_check(candidate, horizons):
        statuses = [candidate.turnover_status]
        for item in horizons:
            statuses.extend(
                (
                    item.gross_status,
                    item.gross_walk_forward_status,
                    item.gross_robustness_status,
                    item.cost_adjusted_status,
                    item.cost_walk_forward_status,
                    item.cost_robustness_status,
                )
            )
        if any(not isinstance(value, str) or not value for value in statuses):
            status = INVALID
            reason = "upstream status is missing or invalid"
        elif any(value.startswith("INVALID_") for value in statuses):
            status = INVALID
            reason = "upstream historical evidence is invalid"
        elif any(
            value.startswith("INSUFFICIENT_") or value.startswith("NO_")
            for value in statuses
        ):
            status = INSUFFICIENT
            reason = "upstream historical evidence is insufficient"
        elif any(value != BATCH_SUCCESS for value in statuses):
            status = INVALID
            reason = "upstream historical evidence status is unsupported"
        else:
            status = PASS
            reason = None
        return HistoricalScreeningCheckResult(
            "HISTORICAL_UPSTREAM_STATUS",
            "INTEGRITY" if status == INVALID else "SUFFICIENCY",
            status,
            None,
            None,
            None,
            None,
            reason,
        )

    @staticmethod
    def _screening_evidence(item):
        stats = (
            item.cost_robustness.fold_statistics.statistics
            if item.cost_robustness is not None
            else None
        )
        return HistoricalScreeningEvidence(
            item.horizon_minutes,
            item.gross_fold_count,
            item.cost_fold_count,
            item.cost_adjustable_coverage_rate,
            stats.mean_delta if stats is not None else None,
            stats.positive_rate if stats is not None else None,
        )

    def _result(
        self,
        batch,
        candidates,
        *,
        status,
        safe_reason=None,
        screening_performed=True,
    ):
        values = tuple(candidates)
        return HistoricalCandidateScreeningGateResult(
            REPORT_TYPE,
            GATE_SCHEMA_VERSION,
            SCREENING_POLICY_SCHEMA_VERSION,
            screening_policy_signature(self.screening_policy),
            self.screening_policy,
            self.promotion_policy.schema_version,
            gate_policy_signature(self.promotion_policy),
            getattr(batch, "batch_schema_version", ""),
            getattr(getattr(batch, "research_profile", None), "schema_version", ""),
            getattr(batch, "reference_snapshot_id", 0),
            getattr(batch, "historical_evidence_as_of", None),
            getattr(batch, "user_id", None),
            getattr(batch, "exchange", None),
            getattr(batch, "quote_asset", None),
            getattr(batch, "reference_policy_signature", None),
            getattr(batch, "effective_top_n", None),
            len(values),
            sum(item.status == PASS for item in values),
            sum(item.status == FAIL for item in values),
            sum(item.status == INSUFFICIENT for item in values),
            sum(item.status == INVALID for item in values),
            values,
            tuple(
                item.scenario_definition_signature
                for item in values
                if item.status == PASS
            ),
            status,
            safe_reason,
            historical_screening_performed=screening_performed,
            policy_decision_performed=screening_performed,
            sample_sufficiency_assessed=screening_performed,
        )


__all__ = [
    "GATE_SCHEMA_VERSION",
    "INVALID_SCREENING_DATA",
    "NO_CANDIDATES_TO_SCREEN",
    "REPORT_TYPE",
    "SCREENING_INSUFFICIENT_DATA",
    "SUCCESS",
    "HistoricalCandidateScreeningGateResult",
    "HistoricalCandidateScreeningGateService",
    "HistoricalCandidateScreeningResult",
]
