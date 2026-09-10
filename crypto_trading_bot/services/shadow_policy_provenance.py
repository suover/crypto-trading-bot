from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import ShadowPolicyEnrollment
from crypto_trading_bot.services.forward_candidate_provenance import (
    ForwardCandidateMetadata,
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION,
    RankingScenarioDefinition,
    parse_scenario_document,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    restore_component_weights,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentError,
    validate_stored_shadow_policy_enrollment,
)


@dataclass(frozen=True)
class ValidatedShadowPolicyEnrollment:
    row: ShadowPolicyEnrollment
    candidate: ForwardCandidateMetadata
    scenario: RankingScenarioDefinition


def load_and_validate_shadow_policy_enrollment(
    session: Session, candidate_id: int
) -> ValidatedShadowPolicyEnrollment | None:
    validated_candidate = load_and_validate_forward_candidate(session, candidate_id)
    row = session.scalar(
        select(ShadowPolicyEnrollment)
        .where(ShadowPolicyEnrollment.candidate_id == candidate_id)
        .execution_options(autoflush=False)
    )
    if row is None:
        return None
    candidate = validated_candidate.metadata
    validate_stored_shadow_policy_enrollment(row, candidate)
    weights = restore_component_weights(candidate.component_weights)
    scenario = parse_scenario_document(
        {
            "schema_version": SCHEMA_VERSION,
            "scenarios": [
                {
                    "name": candidate.scenario_name,
                    "component_weights": weights,
                }
            ],
        }
    )[0]
    if scenario.definition_signature != candidate.scenario_definition_signature:
        raise ShadowPolicyEnrollmentError(
            "stored shadow scenario definition does not verify"
        )
    return ValidatedShadowPolicyEnrollment(
        row=row,
        candidate=candidate,
        scenario=scenario,
    )


__all__ = [
    "ValidatedShadowPolicyEnrollment",
    "load_and_validate_shadow_policy_enrollment",
]
