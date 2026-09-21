"""Shared absolute historical evidence checks for research policy candidates."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable


SCREENING_POLICY_SCHEMA_VERSION = "historical-candidate-screening-policy-v1"
PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT = "INSUFFICIENT"
INVALID = "INVALID"


@dataclass(frozen=True)
class HistoricalScreeningThresholds:
    required_horizons: tuple[int, ...]
    min_gross_fold_count: int
    min_cost_fold_count: int
    min_cost_adjustable_coverage: Decimal
    min_supportive_horizons: int
    supportive_positive_rate: Decimal
    catastrophic_mean_delta_floor: Decimal


@dataclass(frozen=True)
class HistoricalScreeningEvidence:
    horizon_minutes: int
    gross_fold_count: int | None
    cost_fold_count: int | None
    cost_adjustable_coverage_rate: Decimal | None
    cost_mean_delta: Decimal | None
    cost_positive_rate: Decimal | None


@dataclass(frozen=True)
class HistoricalScreeningCheckResult:
    check_id: str
    category: str
    status: str
    horizon_minutes: int | None
    observed_value: str | None
    comparator: str | None
    threshold_value: str | None
    reason: str | None


@dataclass(frozen=True)
class HistoricalScreeningEvaluation:
    sufficiency_checks: tuple[HistoricalScreeningCheckResult, ...]
    stability_checks: tuple[HistoricalScreeningCheckResult, ...]

    @property
    def all_checks(self) -> tuple[HistoricalScreeningCheckResult, ...]:
        return self.sufficiency_checks + self.stability_checks


def thresholds_from_promotion_policy(policy) -> HistoricalScreeningThresholds:
    """Create the historical-only view without duplicating threshold values."""
    return HistoricalScreeningThresholds(
        required_horizons=policy.required_horizons,
        min_gross_fold_count=policy.min_historical_gross_fold_count,
        min_cost_fold_count=policy.min_historical_cost_fold_count,
        min_cost_adjustable_coverage=policy.min_historical_cost_adjustable_coverage,
        min_supportive_horizons=policy.min_historical_supportive_horizons,
        supportive_positive_rate=policy.historical_supportive_positive_rate,
        catastrophic_mean_delta_floor=(policy.historical_catastrophic_mean_delta_floor),
    )


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def screening_policy_signature(thresholds: HistoricalScreeningThresholds) -> str:
    validate_thresholds(thresholds)
    canonical = {
        key: (
            _canonical_decimal(value)
            if isinstance(value, Decimal)
            else list(value)
            if isinstance(value, tuple)
            else value
        )
        for key, value in asdict(thresholds).items()
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"{SCREENING_POLICY_SCHEMA_VERSION}:{digest}"


def validate_thresholds(thresholds: HistoricalScreeningThresholds) -> None:
    if not isinstance(thresholds, HistoricalScreeningThresholds):
        raise ValueError("historical screening thresholds are invalid")
    horizons = thresholds.required_horizons
    if horizons != tuple(sorted(set(horizons))) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in horizons
    ):
        raise ValueError("historical screening horizons are invalid")
    counts = (
        thresholds.min_gross_fold_count,
        thresholds.min_cost_fold_count,
        thresholds.min_supportive_horizons,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in counts
    ) or thresholds.min_supportive_horizons > len(horizons):
        raise ValueError("historical screening count thresholds are invalid")
    decimals = (
        thresholds.min_cost_adjustable_coverage,
        thresholds.supportive_positive_rate,
        thresholds.catastrophic_mean_delta_floor,
    )
    if any(
        isinstance(value, bool)
        or not isinstance(value, Decimal)
        or not value.is_finite()
        for value in decimals
    ):
        raise ValueError("historical screening Decimal thresholds are invalid")
    if not (
        Decimal("0") <= thresholds.min_cost_adjustable_coverage <= Decimal("1")
        and Decimal("0") <= thresholds.supportive_positive_rate <= Decimal("1")
    ):
        raise ValueError("historical screening rate thresholds are invalid")


class HistoricalCandidateScreeningEvaluator:
    """Apply the promotion policy's historical checks without relative ranking."""

    def __init__(self, thresholds: HistoricalScreeningThresholds) -> None:
        validate_thresholds(thresholds)
        self.thresholds = thresholds

    def evaluate(
        self, evidence: Iterable[HistoricalScreeningEvidence]
    ) -> HistoricalScreeningEvaluation:
        values = tuple(evidence)
        indexed = {item.horizon_minutes: item for item in values}
        if len(indexed) != len(values) or set(indexed) != set(
            self.thresholds.required_horizons
        ):
            return HistoricalScreeningEvaluation(
                (),
                (
                    self._check(
                        "HISTORICAL_HORIZON_ALIGNMENT",
                        "INTEGRITY",
                        INVALID,
                        reason="required horizon evidence is missing, duplicated, or unexpected",
                    ),
                ),
            )

        sufficiency = []
        supportive = 0
        catastrophic_values = []
        invalid_checks = []
        for horizon in self.thresholds.required_horizons:
            item = indexed[horizon]
            sufficiency.extend(
                (
                    self._threshold(
                        "HISTORICAL_GROSS_FOLD_COUNT",
                        horizon,
                        item.gross_fold_count,
                        self.thresholds.min_gross_fold_count,
                    ),
                    self._threshold(
                        "HISTORICAL_COST_FOLD_COUNT",
                        horizon,
                        item.cost_fold_count,
                        self.thresholds.min_cost_fold_count,
                    ),
                    self._threshold(
                        "HISTORICAL_COST_COVERAGE",
                        horizon,
                        item.cost_adjustable_coverage_rate,
                        self.thresholds.min_cost_adjustable_coverage,
                    ),
                )
            )
            mean = item.cost_mean_delta
            positive_rate = item.cost_positive_rate
            stats_missing_with_sufficient_folds = (
                item.cost_fold_count is not None
                and self._valid_number(item.cost_fold_count)
                and item.cost_fold_count >= self.thresholds.min_cost_fold_count
                and (mean is None or positive_rate is None)
            )
            stats_invalid = any(
                value is not None and not self._valid_number(value)
                for value in (mean, positive_rate)
            ) or (
                positive_rate is not None
                and self._valid_number(positive_rate)
                and not Decimal("0") <= positive_rate <= Decimal("1")
            )
            if stats_missing_with_sufficient_folds or stats_invalid:
                invalid_checks.append(
                    self._check(
                        "HISTORICAL_STABILITY_STATISTICS",
                        "INTEGRITY",
                        INVALID,
                        horizon=horizon,
                        reason=(
                            "historical stability statistics are missing"
                            if stats_missing_with_sufficient_folds
                            else "historical stability statistics are invalid"
                        ),
                    )
                )
            if (
                self._valid_number(mean)
                and self._valid_number(positive_rate)
                and mean > 0
                and positive_rate >= self.thresholds.supportive_positive_rate
            ):
                supportive += 1
            catastrophic_values.append(mean)

        catastrophic = min(
            (
                value
                for value in catastrophic_values
                if value is not None and self._valid_number(value)
            ),
            default=None,
        )
        stability = [
            self._threshold(
                "HISTORICAL_SUPPORTIVE_HORIZONS",
                None,
                supportive,
                self.thresholds.min_supportive_horizons,
                category="HISTORICAL_STABILITY",
                failure=FAIL,
            ),
            self._threshold(
                "HISTORICAL_CATASTROPHIC_DEGRADATION",
                None,
                catastrophic,
                self.thresholds.catastrophic_mean_delta_floor,
                category="HISTORICAL_STABILITY",
                failure=FAIL,
            ),
        ]
        return HistoricalScreeningEvaluation(
            tuple(sufficiency), tuple(invalid_checks + stability)
        )

    def _threshold(
        self,
        check_id,
        horizon,
        observed,
        threshold,
        *,
        category="SUFFICIENCY",
        failure=INSUFFICIENT,
    ):
        if observed is None:
            status = INSUFFICIENT
        elif not self._valid_number(observed):
            status = INVALID
        else:
            status = PASS if observed >= threshold else failure
        return self._check(
            check_id, category, status, horizon, observed, ">=", threshold
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
        return HistoricalScreeningCheckResult(
            check_id,
            category,
            status,
            horizon,
            None if observed is None else str(observed),
            comparator,
            None if threshold is None else str(threshold),
            reason,
        )


__all__ = [
    "FAIL",
    "INSUFFICIENT",
    "INVALID",
    "PASS",
    "SCREENING_POLICY_SCHEMA_VERSION",
    "HistoricalCandidateScreeningEvaluator",
    "HistoricalScreeningCheckResult",
    "HistoricalScreeningEvaluation",
    "HistoricalScreeningEvidence",
    "HistoricalScreeningThresholds",
    "screening_policy_signature",
    "thresholds_from_promotion_policy",
    "validate_thresholds",
]
