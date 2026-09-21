from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ResearchPolicyCandidate,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    ReplayInputError,
    restore_weights,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    RankingScenarioDefinition,
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    policy_signature,
)


CANDIDATE_SCHEMA_VERSION = "research-policy-candidate-v1"
CREATED = "CREATED"
ALREADY_REGISTERED = "ALREADY_REGISTERED"
DRY_RUN = "DRY_RUN"


class ResearchPolicyCandidateInputError(ReplayInputError):
    pass


class ResearchPolicyCandidateRegistrationError(ValueError):
    pass


class ResearchPolicyCandidateConflictError(ResearchPolicyCandidateRegistrationError):
    pass


@dataclass(frozen=True)
class ResearchPolicyCandidateRegistrationPlan:
    candidate_schema_version: str
    user_id: int
    exchange: str
    quote_asset: str
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, str]
    reference_snapshot_id: int
    reference_snapshot_captured_at: datetime
    dataset_schema_version: str
    baseline_policy_signature: str
    effective_top_n: int
    registered_at: datetime | None
    registration_snapshot_id_watermark: int
    registration_captured_at_watermark: datetime


@dataclass(frozen=True)
class ResearchPolicyCandidateRegistrationResult:
    candidate: ResearchPolicyCandidate | None
    plan: ResearchPolicyCandidateRegistrationPlan
    registration_created: bool
    registration_status: str


@dataclass(frozen=True)
class ResearchPolicyCandidateRegistrationAnchor:
    reference_snapshot_id: int
    reference_snapshot_captured_at: datetime
    user_id: int
    exchange: str
    quote_asset: str
    dataset_schema_version: str
    baseline_policy_signature: str
    effective_top_n: int
    registration_snapshot_id_watermark: int
    registration_captured_at_watermark: datetime


def select_scenario(
    scenarios: Iterable[RankingScenarioDefinition], scenario_name: str
) -> RankingScenarioDefinition:
    if not isinstance(scenario_name, str) or not scenario_name:
        raise ResearchPolicyCandidateInputError("scenario name is required")
    matches = tuple(item for item in scenarios if item.name == scenario_name)
    if len(matches) != 1:
        raise ResearchPolicyCandidateInputError(
            f"scenario name must identify exactly one definition: {scenario_name}"
        )
    return matches[0]


def restore_component_weights(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ResearchPolicyCandidateRegistrationError(
            "stored component weights are invalid"
        )
    restored: dict[str, Decimal] = {}
    for name, raw_value in value.items():
        if not isinstance(name, str) or not name or isinstance(raw_value, bool):
            raise ResearchPolicyCandidateRegistrationError(
                "stored component weights are invalid"
            )
        try:
            parsed = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ResearchPolicyCandidateRegistrationError(
                "stored component weights are invalid"
            ) from error
        if not parsed.is_finite():
            raise ResearchPolicyCandidateRegistrationError(
                "stored component weights are invalid"
            )
        restored[name] = parsed
    return restored


class ResearchPolicyCandidateRegistryService:
    """Create immutable research registration anchors without policy activation."""

    def __init__(
        self,
        session: Session,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def preview(
        self,
        *,
        reference_snapshot_id: int,
        scenario: RankingScenarioDefinition,
    ) -> ResearchPolicyCandidateRegistrationResult:
        snapshot, top_n = self._reference_context(reference_snapshot_id)
        weights = self._component_weights(scenario)
        existing = self._find_existing(snapshot, top_n, scenario)
        if existing is not None:
            self._validate_existing(existing, weights)
            return ResearchPolicyCandidateRegistrationResult(
                candidate=existing,
                plan=self._plan_from_candidate(existing),
                registration_created=False,
                registration_status=ALREADY_REGISTERED,
            )
        id_watermark, captured_watermark = self._watermarks(snapshot, top_n)
        return ResearchPolicyCandidateRegistrationResult(
            candidate=None,
            plan=self._plan(
                snapshot,
                top_n,
                scenario,
                weights,
                registered_at=None,
                id_watermark=id_watermark,
                captured_watermark=captured_watermark,
            ),
            registration_created=False,
            registration_status=DRY_RUN,
        )

    def register(
        self,
        *,
        reference_snapshot_id: int,
        scenario: RankingScenarioDefinition,
    ) -> ResearchPolicyCandidateRegistrationResult:
        snapshot, top_n = self._reference_context(reference_snapshot_id)
        weights = self._component_weights(scenario)
        existing = self._find_existing(snapshot, top_n, scenario)
        if existing is not None:
            self._validate_existing(existing, weights)
            return ResearchPolicyCandidateRegistrationResult(
                candidate=existing,
                plan=self._plan_from_candidate(existing),
                registration_created=False,
                registration_status=ALREADY_REGISTERED,
            )

        # Capture the trusted clock first, then watermark every matching row visible
        # to this transaction. A row appearing between the two anchors is therefore
        # conservatively included in the registration cutoff.
        registered_at = self._trusted_now()
        id_watermark, captured_watermark = self._watermarks(snapshot, top_n)
        plan = self._plan(
            snapshot,
            top_n,
            scenario,
            weights,
            registered_at=registered_at,
            id_watermark=id_watermark,
            captured_watermark=captured_watermark,
        )
        candidate = ResearchPolicyCandidate(**plan.__dict__)
        try:
            with self.session.begin_nested():
                self.session.add(candidate)
                self.session.flush()
        except IntegrityError:
            concurrent = self._find_existing(snapshot, top_n, scenario)
            if concurrent is None:
                raise ResearchPolicyCandidateRegistrationError(
                    "candidate registration uniqueness conflict"
                ) from None
            self._validate_existing(concurrent, weights)
            return ResearchPolicyCandidateRegistrationResult(
                candidate=concurrent,
                plan=self._plan_from_candidate(concurrent),
                registration_created=False,
                registration_status=ALREADY_REGISTERED,
            )
        return ResearchPolicyCandidateRegistrationResult(
            candidate=candidate,
            plan=plan,
            registration_created=True,
            registration_status=CREATED,
        )

    def registration_anchor(
        self, *, reference_snapshot_id: int
    ) -> ResearchPolicyCandidateRegistrationAnchor:
        snapshot, top_n = self._reference_context(reference_snapshot_id)
        id_watermark, captured_watermark = self._watermarks(snapshot, top_n)
        return ResearchPolicyCandidateRegistrationAnchor(
            reference_snapshot_id=snapshot.id,
            reference_snapshot_captured_at=self._aware_utc(
                snapshot.captured_at, "reference snapshot captured_at"
            ),
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            quote_asset=snapshot.quote_asset,
            dataset_schema_version=snapshot.dataset_schema_version,
            baseline_policy_signature=snapshot.policy_signature,
            effective_top_n=top_n,
            registration_snapshot_id_watermark=id_watermark,
            registration_captured_at_watermark=captured_watermark,
        )

    def trusted_registration_time(self) -> datetime:
        return self._trusted_now()

    def register_with_shared_anchor(
        self,
        *,
        reference_snapshot_id: int,
        scenario: RankingScenarioDefinition,
        registered_at: datetime,
        registration_snapshot_id_watermark: int,
        registration_captured_at_watermark: datetime,
    ) -> ResearchPolicyCandidateRegistrationResult:
        snapshot, top_n = self._reference_context(reference_snapshot_id)
        weights = self._component_weights(scenario)
        existing = self._find_existing(snapshot, top_n, scenario)
        if existing is not None:
            self._validate_existing(existing, weights)
            return ResearchPolicyCandidateRegistrationResult(
                candidate=existing,
                plan=self._plan_from_candidate(existing),
                registration_created=False,
                registration_status=ALREADY_REGISTERED,
            )
        trusted_time = self._aware_utc(registered_at, "registration clock")
        current_id_watermark, current_captured_watermark = self._watermarks(
            snapshot, top_n
        )
        captured_watermark = self._aware_utc(
            registration_captured_at_watermark,
            "registration captured_at watermark",
        )
        if (
            isinstance(registration_snapshot_id_watermark, bool)
            or not isinstance(registration_snapshot_id_watermark, int)
            or registration_snapshot_id_watermark != current_id_watermark
            or captured_watermark != current_captured_watermark
            or captured_watermark
            < self._aware_utc(snapshot.captured_at, "reference snapshot captured_at")
            or trusted_time < captured_watermark
        ):
            raise ResearchPolicyCandidateRegistrationError(
                "shared registration anchor is invalid"
            )
        plan = self._plan(
            snapshot,
            top_n,
            scenario,
            weights,
            registered_at=trusted_time,
            id_watermark=registration_snapshot_id_watermark,
            captured_watermark=captured_watermark,
        )
        candidate = ResearchPolicyCandidate(**plan.__dict__)
        try:
            with self.session.begin_nested():
                self.session.add(candidate)
                self.session.flush()
        except IntegrityError:
            concurrent = self._find_existing(snapshot, top_n, scenario)
            if concurrent is None:
                raise ResearchPolicyCandidateRegistrationError(
                    "candidate registration uniqueness conflict"
                ) from None
            self._validate_existing(concurrent, weights)
            return ResearchPolicyCandidateRegistrationResult(
                candidate=concurrent,
                plan=self._plan_from_candidate(concurrent),
                registration_created=False,
                registration_status=ALREADY_REGISTERED,
            )
        return ResearchPolicyCandidateRegistrationResult(
            candidate=candidate,
            plan=plan,
            registration_created=True,
            registration_status=CREATED,
        )

    def _reference_context(
        self, reference_snapshot_id: int
    ) -> tuple[StrategyReplaySnapshot, int]:
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id < 1
        ):
            raise ResearchPolicyCandidateInputError(
                "reference snapshot ID must be a positive integer"
            )
        snapshot = self.session.scalar(
            select(StrategyReplaySnapshot)
            .where(StrategyReplaySnapshot.id == reference_snapshot_id)
            .execution_options(autoflush=False)
        )
        if snapshot is None:
            raise ResearchPolicyCandidateRegistrationError(
                f"reference snapshot does not exist: {reference_snapshot_id}"
            )
        if (
            snapshot.dataset_schema_version != DATASET_SCHEMA_VERSION
            or not isinstance(snapshot.policy_data, dict)
            or snapshot.policy_data.get("dataset_schema_version")
            != DATASET_SCHEMA_VERSION
        ):
            raise ResearchPolicyCandidateRegistrationError(
                "reference snapshot dataset schema is unsupported"
            )
        self._aware_utc(snapshot.captured_at, "reference snapshot captured_at")
        if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
            raise ResearchPolicyCandidateRegistrationError(
                "reference snapshot policy signature does not match policy_data"
            )
        try:
            _, stored_top_n = restore_weights(snapshot.policy_data)
        except Exception as error:
            raise ResearchPolicyCandidateRegistrationError(
                f"reference snapshot ranking policy is invalid: {error}"
            ) from error
        if (
            isinstance(snapshot.user_id, bool)
            or not isinstance(snapshot.user_id, int)
            or snapshot.user_id < 1
            or not isinstance(snapshot.exchange, str)
            or not snapshot.exchange
            or not isinstance(snapshot.quote_asset, str)
            or not snapshot.quote_asset
            or not isinstance(snapshot.policy_signature, str)
            or not snapshot.policy_signature
        ):
            raise ResearchPolicyCandidateRegistrationError(
                "reference snapshot context is invalid"
            )
        return snapshot, stored_top_n

    @staticmethod
    def _component_weights(scenario: RankingScenarioDefinition) -> dict[str, str]:
        if (
            not isinstance(scenario.name, str)
            or not scenario.name
            or not isinstance(scenario.definition_signature, str)
            or not scenario.definition_signature
            or not isinstance(scenario.component_weights, dict)
            or not scenario.component_weights
        ):
            raise ResearchPolicyCandidateInputError("scenario definition is invalid")
        try:
            validated = parse_scenario_document(
                {
                    "schema_version": SCHEMA_VERSION,
                    "scenarios": [
                        {
                            "name": scenario.name,
                            "component_weights": scenario.component_weights,
                        }
                    ],
                }
            )[0]
        except ReplayInputError as error:
            raise ResearchPolicyCandidateInputError(str(error)) from error
        if validated.definition_signature != scenario.definition_signature:
            raise ResearchPolicyCandidateInputError(
                "scenario definition signature does not match component weights"
            )
        values: dict[str, str] = {}
        for name, value in sorted(validated.component_weights.items()):
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(value, Decimal)
                or not value.is_finite()
            ):
                raise ResearchPolicyCandidateInputError(
                    "scenario component weights are invalid"
                )
            normalized = value.normalize()
            values[name] = "0" if normalized == 0 else format(normalized, "f")
        return values

    def _find_existing(
        self,
        snapshot: StrategyReplaySnapshot,
        top_n: int,
        scenario: RankingScenarioDefinition,
    ) -> ResearchPolicyCandidate | None:
        matches = tuple(
            self.session.scalars(
                select(ResearchPolicyCandidate)
                .where(
                    *self._context_predicates(snapshot, top_n),
                    or_(
                        ResearchPolicyCandidate.scenario_name == scenario.name,
                        ResearchPolicyCandidate.scenario_definition_signature
                        == scenario.definition_signature,
                    ),
                )
                .execution_options(autoflush=False)
            )
        )
        exact = None
        for candidate in matches:
            same_name = candidate.scenario_name == scenario.name
            same_definition = (
                candidate.scenario_definition_signature == scenario.definition_signature
            )
            if same_name and same_definition:
                exact = candidate
            elif same_name:
                raise ResearchPolicyCandidateConflictError(
                    "scenario name is already registered with another definition"
                )
            elif same_definition:
                raise ResearchPolicyCandidateConflictError(
                    "scenario definition is already registered under another name"
                )
        return exact

    def _watermarks(
        self, snapshot: StrategyReplaySnapshot, top_n: int
    ) -> tuple[int, datetime]:
        id_watermark, captured_watermark = self.session.execute(
            select(
                func.max(StrategyReplaySnapshot.id),
                func.max(StrategyReplaySnapshot.captured_at),
            )
            .where(
                StrategyReplaySnapshot.user_id == snapshot.user_id,
                StrategyReplaySnapshot.exchange == snapshot.exchange,
                StrategyReplaySnapshot.quote_asset == snapshot.quote_asset,
                StrategyReplaySnapshot.policy_signature == snapshot.policy_signature,
                StrategyReplaySnapshot.dataset_schema_version == DATASET_SCHEMA_VERSION,
                StrategyReplaySnapshot.policy_data["market_universe"][
                    "top_n"
                ].as_integer()
                == top_n,
            )
            .execution_options(autoflush=False)
        ).one()
        if (
            isinstance(id_watermark, bool)
            or not isinstance(id_watermark, int)
            or id_watermark < snapshot.id
            or not isinstance(captured_watermark, datetime)
        ):
            raise ResearchPolicyCandidateRegistrationError(
                "registration snapshot watermark is invalid"
            )
        captured_utc = self._aware_utc(
            captured_watermark, "registration captured_at watermark"
        )
        reference_utc = self._aware_utc(
            snapshot.captured_at, "reference snapshot captured_at"
        )
        if captured_utc < reference_utc:
            raise ResearchPolicyCandidateRegistrationError(
                "registration captured_at watermark precedes reference snapshot"
            )
        return id_watermark, captured_utc

    @staticmethod
    def _context_predicates(snapshot: StrategyReplaySnapshot, top_n: int) -> tuple:
        return (
            ResearchPolicyCandidate.user_id == snapshot.user_id,
            ResearchPolicyCandidate.exchange == snapshot.exchange,
            ResearchPolicyCandidate.quote_asset == snapshot.quote_asset,
            ResearchPolicyCandidate.baseline_policy_signature
            == snapshot.policy_signature,
            ResearchPolicyCandidate.effective_top_n == top_n,
        )

    @staticmethod
    def _plan(
        snapshot: StrategyReplaySnapshot,
        top_n: int,
        scenario: RankingScenarioDefinition,
        weights: dict[str, str],
        *,
        registered_at: datetime | None,
        id_watermark: int,
        captured_watermark: datetime,
    ) -> ResearchPolicyCandidateRegistrationPlan:
        return ResearchPolicyCandidateRegistrationPlan(
            candidate_schema_version=CANDIDATE_SCHEMA_VERSION,
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            quote_asset=snapshot.quote_asset,
            scenario_name=scenario.name,
            scenario_definition_signature=scenario.definition_signature,
            component_weights=weights,
            reference_snapshot_id=snapshot.id,
            reference_snapshot_captured_at=snapshot.captured_at.astimezone(UTC),
            dataset_schema_version=snapshot.dataset_schema_version,
            baseline_policy_signature=snapshot.policy_signature,
            effective_top_n=top_n,
            registered_at=registered_at,
            registration_snapshot_id_watermark=id_watermark,
            registration_captured_at_watermark=captured_watermark,
        )

    @staticmethod
    def _plan_from_candidate(
        candidate: ResearchPolicyCandidate,
    ) -> ResearchPolicyCandidateRegistrationPlan:
        return ResearchPolicyCandidateRegistrationPlan(
            candidate_schema_version=candidate.candidate_schema_version,
            user_id=candidate.user_id,
            exchange=candidate.exchange,
            quote_asset=candidate.quote_asset,
            scenario_name=candidate.scenario_name,
            scenario_definition_signature=candidate.scenario_definition_signature,
            component_weights=candidate.component_weights,
            reference_snapshot_id=candidate.reference_snapshot_id,
            reference_snapshot_captured_at=candidate.reference_snapshot_captured_at,
            dataset_schema_version=candidate.dataset_schema_version,
            baseline_policy_signature=candidate.baseline_policy_signature,
            effective_top_n=candidate.effective_top_n,
            registered_at=candidate.registered_at,
            registration_snapshot_id_watermark=(
                candidate.registration_snapshot_id_watermark
            ),
            registration_captured_at_watermark=(
                candidate.registration_captured_at_watermark
            ),
        )

    @staticmethod
    def _validate_existing(
        candidate: ResearchPolicyCandidate, expected_weights: dict[str, str]
    ) -> None:
        try:
            stored_weights = restore_component_weights(candidate.component_weights)
            expected = restore_component_weights(expected_weights)
        except ResearchPolicyCandidateRegistrationError:
            raise
        datetimes = (
            candidate.reference_snapshot_captured_at,
            candidate.registered_at,
            candidate.registration_captured_at_watermark,
        )
        if (
            candidate.candidate_schema_version != CANDIDATE_SCHEMA_VERSION
            or stored_weights != expected
            or isinstance(candidate.registration_snapshot_id_watermark, bool)
            or candidate.registration_snapshot_id_watermark
            < candidate.reference_snapshot_id
            or candidate.effective_top_n < 1
            or any(
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
                for value in datetimes
            )
            or candidate.registration_captured_at_watermark
            < candidate.reference_snapshot_captured_at
        ):
            raise ResearchPolicyCandidateRegistrationError(
                "existing candidate registration is invalid"
            )

    def _trusted_now(self) -> datetime:
        return self._aware_utc(self.now_fn(), "registration clock")

    @staticmethod
    def _aware_utc(value: datetime, field_name: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ResearchPolicyCandidateRegistrationError(
                f"{field_name} must be timezone-aware"
            )
        return value.astimezone(UTC)


__all__ = [
    "ALREADY_REGISTERED",
    "CANDIDATE_SCHEMA_VERSION",
    "CREATED",
    "DRY_RUN",
    "ResearchPolicyCandidateConflictError",
    "ResearchPolicyCandidateInputError",
    "ResearchPolicyCandidateRegistrationError",
    "ResearchPolicyCandidateRegistrationAnchor",
    "ResearchPolicyCandidateRegistrationPlan",
    "ResearchPolicyCandidateRegistrationResult",
    "ResearchPolicyCandidateRegistryService",
    "restore_component_weights",
    "select_scenario",
]
