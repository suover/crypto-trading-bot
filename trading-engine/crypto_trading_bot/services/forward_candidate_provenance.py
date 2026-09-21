from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
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
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    CANDIDATE_SCHEMA_VERSION,
    ResearchPolicyCandidateRegistrationError,
    restore_component_weights,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    policy_signature,
)


class InvalidForwardCandidateProvenance(Exception):
    pass


@dataclass(frozen=True)
class ForwardCandidateMetadata:
    candidate_id: int
    candidate_schema_version: str
    scenario_name: str
    scenario_definition_signature: str
    component_weights: dict[str, Decimal]
    user_id: int
    exchange: str
    quote_asset: str
    baseline_policy_signature: str
    effective_top_n: int
    dataset_schema_version: str
    reference_snapshot_id: int
    reference_snapshot_captured_at: datetime
    registered_at: datetime
    registration_snapshot_id_watermark: int
    registration_captured_at_watermark: datetime


@dataclass(frozen=True)
class ValidatedForwardCandidate:
    row: ResearchPolicyCandidate
    metadata: ForwardCandidateMetadata
    scenario: RankingScenarioDefinition


def aware_utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidForwardCandidateProvenance(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def load_and_validate_forward_candidate(
    session: Session, candidate_id: int
) -> ValidatedForwardCandidate:
    candidate = session.scalar(
        select(ResearchPolicyCandidate)
        .where(ResearchPolicyCandidate.id == candidate_id)
        .execution_options(autoflush=False)
    )
    if candidate is None:
        raise InvalidForwardCandidateProvenance(
            f"research policy candidate does not exist: {candidate_id}"
        )
    if candidate.id != candidate_id:
        raise InvalidForwardCandidateProvenance("candidate query identity mismatch")
    return validate_forward_candidate(session, candidate)


def validate_forward_candidate(
    session: Session, candidate: ResearchPolicyCandidate
) -> ValidatedForwardCandidate:
    if candidate.candidate_schema_version != CANDIDATE_SCHEMA_VERSION:
        raise InvalidForwardCandidateProvenance(
            "candidate schema version is unsupported"
        )
    if candidate.dataset_schema_version != DATASET_SCHEMA_VERSION:
        raise InvalidForwardCandidateProvenance(
            "candidate dataset schema is unsupported"
        )
    positive_integers = {
        "candidate id": candidate.id,
        "user id": candidate.user_id,
        "reference snapshot id": candidate.reference_snapshot_id,
        "effective TopN": candidate.effective_top_n,
        "registration snapshot ID watermark": (
            candidate.registration_snapshot_id_watermark
        ),
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in positive_integers.values()
    ):
        raise InvalidForwardCandidateProvenance(
            "candidate positive integer metadata is invalid"
        )
    strings = (
        candidate.exchange,
        candidate.quote_asset,
        candidate.scenario_name,
        candidate.scenario_definition_signature,
        candidate.baseline_policy_signature,
    )
    if any(not isinstance(value, str) or not value for value in strings):
        raise InvalidForwardCandidateProvenance(
            "candidate identity metadata is invalid"
        )
    registered_at = aware_utc(candidate.registered_at, "registered_at")
    reference_at = aware_utc(
        candidate.reference_snapshot_captured_at,
        "reference_snapshot_captured_at",
    )
    watermark_at = aware_utc(
        candidate.registration_captured_at_watermark,
        "registration_captured_at_watermark",
    )
    if candidate.registration_snapshot_id_watermark < candidate.reference_snapshot_id:
        raise InvalidForwardCandidateProvenance(
            "registration snapshot ID watermark precedes reference snapshot"
        )
    if watermark_at < reference_at:
        raise InvalidForwardCandidateProvenance(
            "registration captured_at watermark precedes reference snapshot"
        )
    try:
        component_weights = restore_component_weights(candidate.component_weights)
        scenario = parse_scenario_document(
            {
                "schema_version": SCHEMA_VERSION,
                "scenarios": [
                    {
                        "name": candidate.scenario_name,
                        "component_weights": component_weights,
                    }
                ],
            }
        )[0]
    except (ReplayInputError, ResearchPolicyCandidateRegistrationError) as error:
        raise InvalidForwardCandidateProvenance(
            f"candidate component weights are invalid: {error}"
        ) from error
    if scenario.definition_signature != candidate.scenario_definition_signature:
        raise InvalidForwardCandidateProvenance(
            "candidate scenario definition signature does not match weights"
        )
    reference = session.scalar(
        select(StrategyReplaySnapshot)
        .where(StrategyReplaySnapshot.id == candidate.reference_snapshot_id)
        .execution_options(autoflush=False)
    )
    if reference is None:
        raise InvalidForwardCandidateProvenance(
            "candidate reference snapshot does not exist"
        )
    _validate_reference(candidate, reference, reference_at)
    return ValidatedForwardCandidate(
        row=candidate,
        metadata=ForwardCandidateMetadata(
            candidate_id=candidate.id,
            candidate_schema_version=candidate.candidate_schema_version,
            scenario_name=candidate.scenario_name,
            scenario_definition_signature=candidate.scenario_definition_signature,
            component_weights=component_weights,
            user_id=candidate.user_id,
            exchange=candidate.exchange,
            quote_asset=candidate.quote_asset,
            baseline_policy_signature=candidate.baseline_policy_signature,
            effective_top_n=candidate.effective_top_n,
            dataset_schema_version=candidate.dataset_schema_version,
            reference_snapshot_id=candidate.reference_snapshot_id,
            reference_snapshot_captured_at=reference_at,
            registered_at=registered_at,
            registration_snapshot_id_watermark=(
                candidate.registration_snapshot_id_watermark
            ),
            registration_captured_at_watermark=watermark_at,
        ),
        scenario=scenario,
    )


def _validate_reference(
    candidate: ResearchPolicyCandidate,
    reference: StrategyReplaySnapshot,
    expected_captured_at: datetime,
) -> None:
    captured_at = aware_utc(reference.captured_at, "reference captured_at")
    if captured_at != expected_captured_at:
        raise InvalidForwardCandidateProvenance(
            "reference snapshot captured_at mismatch"
        )
    if (
        reference.user_id != candidate.user_id
        or reference.exchange != candidate.exchange
        or reference.quote_asset != candidate.quote_asset
        or reference.dataset_schema_version != candidate.dataset_schema_version
        or reference.policy_signature != candidate.baseline_policy_signature
    ):
        raise InvalidForwardCandidateProvenance("reference snapshot context mismatch")
    if not isinstance(reference.policy_data, dict):
        raise InvalidForwardCandidateProvenance(
            "reference snapshot policy_data is invalid"
        )
    if policy_signature(reference.policy_data) != reference.policy_signature:
        raise InvalidForwardCandidateProvenance(
            "reference baseline policy signature mismatch"
        )
    try:
        _, stored_top_n = restore_weights(reference.policy_data)
    except Exception as error:
        raise InvalidForwardCandidateProvenance(
            f"reference ranking policy is invalid: {error}"
        ) from error
    if stored_top_n != candidate.effective_top_n:
        raise InvalidForwardCandidateProvenance("reference effective TopN mismatch")


__all__ = [
    "ForwardCandidateMetadata",
    "InvalidForwardCandidateProvenance",
    "ValidatedForwardCandidate",
    "aware_utc",
    "load_and_validate_forward_candidate",
    "validate_forward_candidate",
]
