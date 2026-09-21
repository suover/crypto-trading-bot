from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ResearchPolicyCandidate,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    COMPONENT_WEIGHT_FIELDS,
    REFERENCE_FIELDS,
    WEIGHT_FIELDS,
    ReplayInputError,
    apply_overrides,
    restore_weights,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION as SCENARIO_SCHEMA_VERSION,
    RankingScenarioDefinition,
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    policy_signature,
)


GENERATOR_SCHEMA_VERSION = "deterministic-ranking-candidate-generator-v1"
DEFAULT_STEP = Decimal("0.05")
MAX_STEP = Decimal("0.10")
DEFAULT_MAX_CANDIDATES = 20
MAX_CANDIDATES = 50
CANONICAL_COMPONENT_WEIGHT_FIELDS = tuple(
    name for name in WEIGHT_FIELDS if name in COMPONENT_WEIGHT_FIELDS
)
CANONICAL_REFERENCE_FIELDS = tuple(
    name for name in WEIGHT_FIELDS if name in REFERENCE_FIELDS
)


class CandidateGenerationError(ReplayInputError):
    pass


@dataclass(frozen=True)
class GeneratedRankingCandidate:
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    donor_field: str
    receiver_field: str
    transfer_step: Decimal
    reference_snapshot_id: int
    reference_policy_signature: str
    already_registered: bool
    definition: RankingScenarioDefinition


@dataclass(frozen=True)
class DeterministicRankingCandidateGenerationResult:
    generator_schema_version: str
    reference_snapshot_id: int
    user_id: int
    exchange: str
    quote_asset: str
    dataset_schema_version: str
    reference_policy_signature: str
    effective_top_n: int
    source_component_weights: dict[str, Decimal]
    source_reference_parameters: dict[str, Decimal]
    step: Decimal
    max_candidates: int
    generated_before_dedup_count: int
    generated_valid_count: int
    returned_candidate_count: int
    duplicate_removed_count: int
    already_registered_count: int
    novel_candidate_count: int
    selection_includes_registered: bool
    candidate_cap_applied: bool
    all_candidates: tuple[GeneratedRankingCandidate, ...]
    candidates: tuple[GeneratedRankingCandidate, ...]
    database_write: bool = False
    external_calls: bool = False
    outcome_data_used: bool = False
    performance_evaluated: bool = False
    policy_decision_performed: bool = False
    promotion_performed: bool = False
    shadow_runtime_changed: bool = False
    live_policy_change: bool = False
    live_order_change: bool = False


def select_balanced_candidates(
    candidates: tuple[GeneratedRankingCandidate, ...], *, max_candidates: int
) -> tuple[GeneratedRankingCandidate, ...]:
    if len(candidates) <= max_candidates:
        return candidates
    groups = {
        donor: tuple(
            candidate for candidate in candidates if candidate.donor_field == donor
        )
        for donor in CANONICAL_COMPONENT_WEIGHT_FIELDS
    }
    selected: list[GeneratedRankingCandidate] = []
    round_index = 0
    while len(selected) < max_candidates:
        added = False
        for donor in CANONICAL_COMPONENT_WEIGHT_FIELDS:
            group = groups[donor]
            if round_index >= len(group):
                continue
            selected.append(group[round_index])
            added = True
            if len(selected) == max_candidates:
                break
        if not added:
            break
        round_index += 1
    return tuple(selected)


class DeterministicRankingCandidateGeneratorService:
    """Generate a small DB-read-only neighborhood around one stored policy."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def generate(
        self,
        *,
        reference_snapshot_id: int,
        step: Decimal | str = DEFAULT_STEP,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        include_registered: bool = False,
    ) -> DeterministicRankingCandidateGenerationResult:
        normalized_step = self._validate_step(step)
        normalized_max = self._validate_max_candidates(max_candidates)
        snapshot, source_weights, top_n = self._reference_context(reference_snapshot_id)
        source_data = asdict(source_weights)
        source_components = {
            name: source_data[name] for name in CANONICAL_COMPONENT_WEIGHT_FIELDS
        }
        source_references = {
            name: source_data[name] for name in CANONICAL_REFERENCE_FIELDS
        }

        generated: list[GeneratedRankingCandidate] = []
        signatures: set[str] = set()
        generated_before_dedup_count = 0
        duplicate_removed_count = 0
        for donor in CANONICAL_COMPONENT_WEIGHT_FIELDS:
            if source_components[donor] < normalized_step:
                continue
            for receiver in CANONICAL_COMPONENT_WEIGHT_FIELDS:
                if donor == receiver:
                    continue
                if source_components[receiver] + normalized_step > Decimal("1"):
                    continue
                overrides = dict(source_components)
                overrides[donor] -= normalized_step
                overrides[receiver] += normalized_step
                candidate_weights = apply_overrides(source_weights, overrides)
                component_weights = {
                    name: getattr(candidate_weights, name)
                    for name in CANONICAL_COMPONENT_WEIGHT_FIELDS
                }
                if component_weights == source_components:
                    continue
                generated_before_dedup_count += 1
                name = self._candidate_name(donor, receiver, normalized_step)
                definition = parse_scenario_document(
                    {
                        "schema_version": SCENARIO_SCHEMA_VERSION,
                        "scenarios": [
                            {
                                "name": name,
                                "component_weights": component_weights,
                            }
                        ],
                    }
                )[0]
                if definition.definition_signature in signatures:
                    duplicate_removed_count += 1
                    continue
                signatures.add(definition.definition_signature)
                generated.append(
                    GeneratedRankingCandidate(
                        scenario_name=definition.name,
                        scenario_definition_signature=definition.definition_signature,
                        component_weights=dict(definition.component_weights),
                        donor_field=donor,
                        receiver_field=receiver,
                        transfer_step=normalized_step,
                        reference_snapshot_id=snapshot.id,
                        reference_policy_signature=snapshot.policy_signature,
                        already_registered=False,
                        definition=definition,
                    )
                )

        registered_signatures = self._registered_signatures(snapshot, top_n)
        all_candidates = tuple(
            replace(
                candidate,
                already_registered=(
                    candidate.scenario_definition_signature in registered_signatures
                ),
            )
            for candidate in generated
        )
        already_registered_count = sum(
            candidate.already_registered for candidate in all_candidates
        )
        selection_pool = tuple(
            candidate
            for candidate in all_candidates
            if include_registered or not candidate.already_registered
        )
        candidates = select_balanced_candidates(
            selection_pool, max_candidates=normalized_max
        )
        return DeterministicRankingCandidateGenerationResult(
            generator_schema_version=GENERATOR_SCHEMA_VERSION,
            reference_snapshot_id=snapshot.id,
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            quote_asset=snapshot.quote_asset,
            dataset_schema_version=snapshot.dataset_schema_version,
            reference_policy_signature=snapshot.policy_signature,
            effective_top_n=top_n,
            source_component_weights=source_components,
            source_reference_parameters=source_references,
            step=normalized_step,
            max_candidates=normalized_max,
            generated_before_dedup_count=generated_before_dedup_count,
            generated_valid_count=len(generated),
            returned_candidate_count=len(candidates),
            duplicate_removed_count=duplicate_removed_count,
            already_registered_count=already_registered_count,
            novel_candidate_count=len(all_candidates) - already_registered_count,
            selection_includes_registered=include_registered,
            candidate_cap_applied=len(selection_pool) > normalized_max,
            all_candidates=all_candidates,
            candidates=candidates,
        )

    def _reference_context(self, reference_snapshot_id: int):
        if (
            isinstance(reference_snapshot_id, bool)
            or not isinstance(reference_snapshot_id, int)
            or reference_snapshot_id < 1
        ):
            raise CandidateGenerationError(
                "reference snapshot ID must be a positive integer"
            )
        snapshot = self.session.scalar(
            select(StrategyReplaySnapshot)
            .where(StrategyReplaySnapshot.id == reference_snapshot_id)
            .execution_options(autoflush=False)
        )
        if snapshot is None:
            raise CandidateGenerationError(
                f"reference snapshot does not exist: {reference_snapshot_id}"
            )
        if (
            snapshot.dataset_schema_version != DATASET_SCHEMA_VERSION
            or not isinstance(snapshot.policy_data, dict)
            or snapshot.policy_data.get("dataset_schema_version")
            != DATASET_SCHEMA_VERSION
        ):
            raise CandidateGenerationError(
                "reference snapshot dataset schema is unsupported"
            )
        if (
            snapshot.id != reference_snapshot_id
            or isinstance(snapshot.id, bool)
            or not isinstance(snapshot.id, int)
            or snapshot.id < 1
            or isinstance(snapshot.user_id, bool)
            or not isinstance(snapshot.user_id, int)
            or snapshot.user_id < 1
            or not isinstance(snapshot.exchange, str)
            or not snapshot.exchange.strip()
            or not isinstance(snapshot.quote_asset, str)
            or not snapshot.quote_asset.strip()
            or not isinstance(snapshot.policy_signature, str)
            or not snapshot.policy_signature
        ):
            raise CandidateGenerationError("reference snapshot context is invalid")
        if policy_signature(snapshot.policy_data) != snapshot.policy_signature:
            raise CandidateGenerationError(
                "reference snapshot policy signature does not match policy_data"
            )
        try:
            weights, top_n = restore_weights(snapshot.policy_data)
        except Exception as error:
            status = getattr(error, "status", "INVALID_REPLAY_DATA")
            raise CandidateGenerationError(
                f"{status}: reference snapshot ranking policy is invalid: {error}"
            ) from error
        return snapshot, weights, top_n

    def _registered_signatures(
        self, snapshot: StrategyReplaySnapshot, top_n: int
    ) -> frozenset[str]:
        values = self.session.scalars(
            select(ResearchPolicyCandidate.scenario_definition_signature)
            .where(
                ResearchPolicyCandidate.user_id == snapshot.user_id,
                ResearchPolicyCandidate.exchange == snapshot.exchange,
                ResearchPolicyCandidate.quote_asset == snapshot.quote_asset,
                ResearchPolicyCandidate.baseline_policy_signature
                == snapshot.policy_signature,
                ResearchPolicyCandidate.effective_top_n == top_n,
            )
            .execution_options(autoflush=False)
        )
        return frozenset(value for value in values if isinstance(value, str) and value)

    @staticmethod
    def _validate_step(value: Decimal | str) -> Decimal:
        if isinstance(value, bool):
            raise CandidateGenerationError("step must be a valid Decimal")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise CandidateGenerationError("step must be a valid Decimal") from error
        if not parsed.is_finite() or parsed <= 0 or parsed > MAX_STEP:
            raise CandidateGenerationError(
                f"step must be finite and greater than 0 and at most {MAX_STEP}"
            )
        return parsed

    @staticmethod
    def _validate_max_candidates(value: int) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or value > MAX_CANDIDATES
        ):
            raise CandidateGenerationError(
                f"max_candidates must be an integer from 1 through {MAX_CANDIDATES}"
            )
        return value

    @staticmethod
    def _candidate_name(donor: str, receiver: str, step: Decimal) -> str:
        normalized = step.normalize()
        step_text = "0" if normalized == 0 else format(normalized, "f")
        name = f"auto_v1_{donor}_to_{receiver}_p{step_text}"
        if len(name) <= 80:
            return name
        compact_step = str(normalized).lower().replace("+", "")
        compact_name = f"auto_v1_{donor}_to_{receiver}_p{compact_step}"
        if len(compact_name) <= 80:
            return compact_name
        prefix = f"auto_v1_{donor}_to_{receiver}_p"
        digest = sha256(compact_step.encode("ascii")).hexdigest()[:10]
        visible_length = 80 - len(prefix) - len("_h") - len(digest)
        return f"{prefix}{compact_step[:visible_length]}_h{digest}"


__all__ = [
    "CANONICAL_COMPONENT_WEIGHT_FIELDS",
    "CANONICAL_REFERENCE_FIELDS",
    "CandidateGenerationError",
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_STEP",
    "DeterministicRankingCandidateGenerationResult",
    "DeterministicRankingCandidateGeneratorService",
    "GENERATOR_SCHEMA_VERSION",
    "GeneratedRankingCandidate",
    "MAX_CANDIDATES",
    "MAX_STEP",
    "select_balanced_candidates",
]
