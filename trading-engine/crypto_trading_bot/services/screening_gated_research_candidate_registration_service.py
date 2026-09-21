"""Fresh-screening-gated, atomic research candidate registration."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import StrategyReplaySnapshot
from crypto_trading_bot.services.deterministic_ranking_candidate_generator_service import (
    DEFAULT_STEP,
)
from crypto_trading_bot.services.historical_candidate_screening_gate_service import (
    SUCCESS as SCREENING_SUCCESS,
    HistoricalCandidateScreeningGateService,
)
from crypto_trading_bot.services.historical_candidate_screening_policy import PASS
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ALREADY_REGISTERED,
    CREATED as REGISTRY_CREATED,
    DRY_RUN,
    ResearchPolicyCandidateConflictError,
    ResearchPolicyCandidateRegistrationAnchor,
    ResearchPolicyCandidateRegistrationError,
    ResearchPolicyCandidateRegistryService,
    restore_component_weights,
)


REPORT_TYPE = "SCREENING_GATED_RESEARCH_CANDIDATE_REGISTRATION"
SCHEMA_VERSION_V1 = "screening-gated-research-candidate-registration-v1"
READY = "READY"
CREATED = "CREATED"
NO_PASS_CANDIDATES = "NO_PASS_CANDIDATES"
STALE_REFERENCE = "STALE_REFERENCE"
STALE_REGISTRATION_PLAN = "STALE_REGISTRATION_PLAN"
STALE_REGISTRATION_STATE = "STALE_REGISTRATION_STATE"
INVALID_SCREENING_RESULT = "INVALID_SCREENING_RESULT"
REGISTRATION_CONFLICT = "REGISTRATION_CONFLICT"
REGISTRATION_FAILED = "REGISTRATION_FAILED"


@dataclass(frozen=True)
class ScreeningGatedRegistrationCandidatePlan:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    donor_field: str
    receiver_field: str
    transfer_step: Decimal


@dataclass(frozen=True)
class ScreeningGatedRegistrationPlan:
    schema_version: str
    reference_snapshot_id: int
    reference_snapshot_captured_at: datetime
    historical_evidence_as_of: datetime
    user_id: int
    exchange: str
    quote_asset: str
    dataset_schema_version: str
    baseline_policy_signature: str
    effective_top_n: int
    generator_step: Decimal
    screening_gate_schema_version: str
    screening_policy_schema_version: str
    screening_policy_signature: str
    source_promotion_policy_schema_version: str
    source_promotion_policy_signature: str
    expected_registration_snapshot_id_watermark: int
    expected_registration_captured_at_watermark: datetime
    screened_candidate_count: int
    pass_candidate_count: int
    candidates: tuple[ScreeningGatedRegistrationCandidatePlan, ...]


@dataclass(frozen=True)
class ScreeningGatedRegistrationCandidateResult:
    scenario_name: str
    scenario_definition_signature: str
    registration_status: str
    candidate_id: int | None
    registered_at: datetime | None
    registration_snapshot_id_watermark: int
    registration_captured_at_watermark: datetime


@dataclass(frozen=True)
class ScreeningGatedResearchCandidateRegistrationResult:
    report_type: str
    schema_version: str
    apply_requested: bool
    status: str
    safe_reason: str | None
    registration_plan: ScreeningGatedRegistrationPlan | None
    registration_plan_signature: str | None
    screened_candidate_count: int
    pass_candidate_count: int
    registration_candidate_count: int
    created_candidate_count: int
    candidate_results: tuple[ScreeningGatedRegistrationCandidateResult, ...]
    shared_registered_at: datetime | None
    shared_registration_snapshot_id_watermark: int | None
    shared_registration_captured_at_watermark: datetime | None
    research_only: bool = True
    historical_screening_performed: bool = True
    registration_preview_performed: bool = True
    historical_policy_decision_verified: bool = True
    registration_decision_performed: bool = True
    automatic_policy_selection: bool = False
    candidate_registration_performed: bool = False
    database_write: bool = False
    forward_evidence_generated: bool = False
    forward_validation_performed: bool = False
    promotion_performed: bool = False
    shadow_policy_created: bool = False
    full_live_activation_performed: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    live_order_change: bool = False


@dataclass(frozen=True)
class _PreparedRegistration:
    plan: ScreeningGatedRegistrationPlan
    signature: str
    scenarios: tuple[object, ...]
    preview_results: tuple[ScreeningGatedRegistrationCandidateResult, ...]


class _RegistrationAbort(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value):
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration plan contains a non-aware datetime")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def registration_plan_signature(plan: ScreeningGatedRegistrationPlan) -> str:
    payload = json.dumps(
        _canonical(asdict(plan)), sort_keys=True, separators=(",", ":")
    )
    return f"{SCHEMA_VERSION_V1}:{sha256(payload.encode('utf-8')).hexdigest()}"


class ScreeningGatedResearchCandidateRegistrationService:
    def __init__(
        self,
        session: Session,
        *,
        screening_service=None,
        registry_service=None,
        freshness_fn: Callable[[int], ResearchPolicyCandidateRegistrationAnchor]
        | None = None,
        snapshot_lock_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.screening_service = screening_service or (
            HistoricalCandidateScreeningGateService(session)
        )
        self.registry_service = registry_service or (
            ResearchPolicyCandidateRegistryService(session)
        )
        self.freshness_fn = freshness_fn or self._load_fresh_anchor
        self.snapshot_lock_fn = snapshot_lock_fn or self._lock_snapshot_writes

    def preview(
        self,
        *,
        reference_snapshot_id: int,
        step: Decimal | str = DEFAULT_STEP,
    ) -> ScreeningGatedResearchCandidateRegistrationResult:
        prepared, result = self._prepare(
            reference_snapshot_id=reference_snapshot_id, step=step, apply=False
        )
        if result is not None:
            return result
        return self._result(
            False,
            READY,
            prepared=prepared,
            candidate_results=prepared.preview_results,
        )

    def apply(
        self,
        *,
        reference_snapshot_id: int,
        expected_plan_signature: str,
        step: Decimal | str = DEFAULT_STEP,
    ) -> ScreeningGatedResearchCandidateRegistrationResult:
        prepared, result = self._prepare(
            reference_snapshot_id=reference_snapshot_id, step=step, apply=True
        )
        if result is not None:
            return result
        if expected_plan_signature != prepared.signature:
            return self._result(
                True,
                STALE_REGISTRATION_PLAN,
                "fresh registration plan signature does not match expected signature",
                prepared=prepared,
            )
        created = []
        shared_time = None
        try:
            with self.session.begin_nested():
                self.snapshot_lock_fn()
                final_anchor = self.freshness_fn(reference_snapshot_id)
                self._assert_anchor_matches_plan(final_anchor, prepared.plan)
                shared_time = self.registry_service.trusted_registration_time()
                for candidate, scenario in zip(
                    prepared.plan.candidates, prepared.scenarios, strict=True
                ):
                    value = self.registry_service.register_with_shared_anchor(
                        reference_snapshot_id=reference_snapshot_id,
                        scenario=scenario,
                        registered_at=shared_time,
                        registration_snapshot_id_watermark=(
                            final_anchor.registration_snapshot_id_watermark
                        ),
                        registration_captured_at_watermark=(
                            final_anchor.registration_captured_at_watermark
                        ),
                    )
                    if (
                        value.registration_status != REGISTRY_CREATED
                        or not value.registration_created
                        or value.candidate is None
                    ):
                        raise _RegistrationAbort(
                            STALE_REGISTRATION_STATE,
                            "candidate registration state changed after preview",
                        )
                    created.append(
                        ScreeningGatedRegistrationCandidateResult(
                            candidate.scenario_name,
                            candidate.scenario_definition_signature,
                            value.registration_status,
                            value.candidate.id,
                            value.plan.registered_at,
                            value.plan.registration_snapshot_id_watermark,
                            value.plan.registration_captured_at_watermark,
                        )
                    )
        except _RegistrationAbort as error:
            return self._result(True, error.status, error.reason, prepared=prepared)
        except ResearchPolicyCandidateConflictError as error:
            return self._result(
                True, REGISTRATION_CONFLICT, str(error), prepared=prepared
            )
        except Exception as error:
            return self._result(
                True,
                REGISTRATION_FAILED,
                f"registration failed: {type(error).__name__}",
                prepared=prepared,
            )
        return self._result(
            True,
            CREATED,
            prepared=prepared,
            candidate_results=tuple(created),
            shared_time=shared_time,
            registration_performed=True,
        )

    def _prepare(self, *, reference_snapshot_id, step, apply):
        screening = None
        try:
            initial_anchor = self.freshness_fn(reference_snapshot_id)
            screening = self.screening_service.evaluate(
                reference_snapshot_id=reference_snapshot_id, step=step
            )
            statuses = tuple(item.status for item in screening.candidate_results)
            counts_valid = (
                screening.candidate_count == len(statuses)
                and screening.pass_count == sum(value == PASS for value in statuses)
                and screening.invalid_count
                == sum(value == "INVALID" for value in statuses)
            )
            if (
                screening.status != SCREENING_SUCCESS
                or screening.invalid_count
                or not counts_valid
            ):
                return None, self._result(
                    apply,
                    INVALID_SCREENING_RESULT,
                    screening.safe_reason
                    or "screening result is not valid for registration",
                    screening=screening,
                )
            passed = tuple(
                item for item in screening.candidate_results if item.status == PASS
            )
            if len(passed) != screening.pass_count:
                return None, self._result(
                    apply,
                    INVALID_SCREENING_RESULT,
                    "screening PASS count is inconsistent",
                    screening=screening,
                )
            if not passed:
                return None, self._result(
                    apply, NO_PASS_CANDIDATES, screening=screening
                )
            anchor = self.freshness_fn(reference_snapshot_id)
            self._assert_same_anchor(initial_anchor, anchor, STALE_REFERENCE)
            scenarios = tuple(self._scenario(item) for item in passed)
            previews = tuple(
                self.registry_service.preview(
                    reference_snapshot_id=reference_snapshot_id, scenario=scenario
                )
                for scenario in scenarios
            )
            if any(
                value.registration_status == ALREADY_REGISTERED for value in previews
            ):
                return None, self._result(
                    apply,
                    STALE_REGISTRATION_STATE,
                    "a screened candidate is already registered",
                    screening=screening,
                )
            if any(value.registration_status != DRY_RUN for value in previews):
                return None, self._result(
                    apply,
                    REGISTRATION_CONFLICT,
                    "candidate registry preview is not registerable",
                    screening=screening,
                )
            self._validate_previews(anchor, passed, previews)
            final_preview_anchor = self.freshness_fn(reference_snapshot_id)
            self._assert_same_anchor(anchor, final_preview_anchor, STALE_REFERENCE)
            plan = self._plan(screening, passed, anchor, step)
            candidate_results = tuple(
                ScreeningGatedRegistrationCandidateResult(
                    item.scenario_name,
                    item.scenario_definition_signature,
                    preview.registration_status,
                    None,
                    None,
                    preview.plan.registration_snapshot_id_watermark,
                    preview.plan.registration_captured_at_watermark,
                )
                for item, preview in zip(passed, previews, strict=True)
            )
            prepared = _PreparedRegistration(
                plan,
                registration_plan_signature(plan),
                scenarios,
                candidate_results,
            )
            return prepared, None
        except _RegistrationAbort as error:
            return None, self._result(
                apply, error.status, error.reason, screening=screening
            )
        except ResearchPolicyCandidateConflictError as error:
            return None, self._result(
                apply, REGISTRATION_CONFLICT, str(error), screening=screening
            )
        except ResearchPolicyCandidateRegistrationError as error:
            return None, self._result(
                apply, STALE_REFERENCE, str(error), screening=screening
            )
        except Exception as error:
            return None, self._result(
                apply,
                REGISTRATION_FAILED,
                f"registration planning failed: {type(error).__name__}",
                screening=screening,
            )

    def _load_fresh_anchor(
        self, reference_snapshot_id: int
    ) -> ResearchPolicyCandidateRegistrationAnchor:
        anchor = self.registry_service.registration_anchor(
            reference_snapshot_id=reference_snapshot_id
        )
        latest = self.session.scalar(
            select(StrategyReplaySnapshot)
            .where(
                StrategyReplaySnapshot.user_id == anchor.user_id,
                StrategyReplaySnapshot.exchange == anchor.exchange,
                StrategyReplaySnapshot.quote_asset == anchor.quote_asset,
                StrategyReplaySnapshot.dataset_schema_version
                == anchor.dataset_schema_version,
            )
            .order_by(
                StrategyReplaySnapshot.captured_at.desc(),
                StrategyReplaySnapshot.id.desc(),
            )
            .limit(1)
            .execution_options(autoflush=False)
        )
        if latest is None or latest.id != anchor.reference_snapshot_id:
            raise _RegistrationAbort(
                STALE_REFERENCE,
                "reference snapshot is not the latest runtime-context snapshot",
            )
        if (
            latest.policy_signature != anchor.baseline_policy_signature
            or anchor.registration_snapshot_id_watermark != anchor.reference_snapshot_id
            or anchor.registration_captured_at_watermark
            != anchor.reference_snapshot_captured_at
        ):
            raise _RegistrationAbort(
                STALE_REFERENCE,
                "reference policy, TopN, or registration watermark is stale",
            )
        return anchor

    @staticmethod
    def _scenario(candidate):
        scenario = parse_scenario_document(
            {
                "schema_version": SCHEMA_VERSION,
                "scenarios": [
                    {
                        "name": candidate.scenario_name,
                        "component_weights": candidate.component_weights,
                    }
                ],
            }
        )[0]
        if scenario.definition_signature != candidate.scenario_definition_signature:
            raise _RegistrationAbort(
                INVALID_SCREENING_RESULT,
                "screened candidate definition signature is invalid",
            )
        return scenario

    @staticmethod
    def _validate_previews(anchor, candidates, previews):
        for candidate, preview in zip(candidates, previews, strict=True):
            plan = preview.plan
            if (
                plan.reference_snapshot_id != anchor.reference_snapshot_id
                or plan.reference_snapshot_captured_at
                != anchor.reference_snapshot_captured_at
                or plan.baseline_policy_signature != anchor.baseline_policy_signature
                or plan.effective_top_n != anchor.effective_top_n
                or plan.registration_snapshot_id_watermark
                != anchor.registration_snapshot_id_watermark
                or plan.registration_captured_at_watermark
                != anchor.registration_captured_at_watermark
                or plan.scenario_name != candidate.scenario_name
                or plan.scenario_definition_signature
                != candidate.scenario_definition_signature
                or restore_component_weights(plan.component_weights)
                != candidate.component_weights
            ):
                raise _RegistrationAbort(
                    STALE_REGISTRATION_STATE,
                    "registry preview does not match screened candidate or anchor",
                )

    @staticmethod
    def _plan(screening, passed, anchor, step):
        if not isinstance(screening.historical_evidence_as_of, datetime):
            raise _RegistrationAbort(
                INVALID_SCREENING_RESULT,
                "screening historical evidence time is invalid",
            )
        candidates = tuple(
            ScreeningGatedRegistrationCandidatePlan(
                item.scenario_name,
                item.scenario_definition_signature,
                dict(item.component_weights),
                item.donor_field,
                item.receiver_field,
                item.transfer_step,
            )
            for item in passed
        )
        return ScreeningGatedRegistrationPlan(
            SCHEMA_VERSION_V1,
            anchor.reference_snapshot_id,
            anchor.reference_snapshot_captured_at,
            screening.historical_evidence_as_of,
            anchor.user_id,
            anchor.exchange,
            anchor.quote_asset,
            anchor.dataset_schema_version,
            anchor.baseline_policy_signature,
            anchor.effective_top_n,
            Decimal(str(step)),
            screening.gate_schema_version,
            screening.screening_policy_schema_version,
            screening.screening_policy_signature,
            screening.source_promotion_gate_policy_schema_version,
            screening.source_promotion_gate_policy_signature,
            anchor.registration_snapshot_id_watermark,
            anchor.registration_captured_at_watermark,
            screening.candidate_count,
            screening.pass_count,
            candidates,
        )

    @staticmethod
    def _assert_anchor_matches_plan(anchor, plan):
        if (
            anchor.reference_snapshot_id != plan.reference_snapshot_id
            or anchor.reference_snapshot_captured_at
            != plan.reference_snapshot_captured_at
            or anchor.baseline_policy_signature != plan.baseline_policy_signature
            or anchor.effective_top_n != plan.effective_top_n
            or anchor.registration_snapshot_id_watermark
            != plan.expected_registration_snapshot_id_watermark
            or anchor.registration_captured_at_watermark
            != plan.expected_registration_captured_at_watermark
        ):
            raise _RegistrationAbort(
                STALE_REGISTRATION_PLAN,
                "registration anchor changed after plan confirmation",
            )

    @staticmethod
    def _assert_same_anchor(expected, current, status):
        if expected != current:
            raise _RegistrationAbort(
                status, "registration reference changed during screening or preview"
            )

    def _lock_snapshot_writes(self) -> None:
        self.session.execute(text("LOCK TABLE strategy_replay_snapshots IN SHARE MODE"))

    @staticmethod
    def _result(
        apply,
        status,
        safe_reason=None,
        *,
        prepared=None,
        screening=None,
        candidate_results=(),
        shared_time=None,
        registration_performed=False,
    ):
        plan = prepared.plan if prepared else None
        signature = prepared.signature if prepared else None
        screened = (
            plan.screened_candidate_count
            if plan
            else getattr(screening, "candidate_count", 0)
        )
        passed = (
            plan.pass_candidate_count if plan else getattr(screening, "pass_count", 0)
        )
        values = tuple(candidate_results)
        return ScreeningGatedResearchCandidateRegistrationResult(
            REPORT_TYPE,
            SCHEMA_VERSION_V1,
            apply,
            status,
            safe_reason,
            plan,
            signature,
            screened,
            passed,
            len(plan.candidates) if plan else 0,
            len(values) if registration_performed else 0,
            values,
            shared_time,
            plan.expected_registration_snapshot_id_watermark
            if registration_performed
            else None,
            plan.expected_registration_captured_at_watermark
            if registration_performed
            else None,
            candidate_registration_performed=registration_performed,
            database_write=registration_performed,
            historical_screening_performed=(
                prepared is not None or screening is not None
            ),
            registration_preview_performed=prepared is not None,
            historical_policy_decision_verified=(
                prepared is not None or screening is not None
            ),
        )


__all__ = [
    "CREATED",
    "INVALID_SCREENING_RESULT",
    "NO_PASS_CANDIDATES",
    "READY",
    "REGISTRATION_CONFLICT",
    "REGISTRATION_FAILED",
    "REPORT_TYPE",
    "SCHEMA_VERSION_V1",
    "STALE_REFERENCE",
    "STALE_REGISTRATION_PLAN",
    "STALE_REGISTRATION_STATE",
    "ScreeningGatedRegistrationCandidatePlan",
    "ScreeningGatedRegistrationCandidateResult",
    "ScreeningGatedRegistrationPlan",
    "ScreeningGatedResearchCandidateRegistrationResult",
    "ScreeningGatedResearchCandidateRegistrationService",
    "registration_plan_signature",
]
