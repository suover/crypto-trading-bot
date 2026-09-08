import argparse
import json

from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    load_scenario_file,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistrationError,
    ResearchPolicyCandidateRegistrationResult,
    ResearchPolicyCandidateRegistryService,
    select_scenario,
)


REPORT_TYPE = "RESEARCH_POLICY_CANDIDATE_REGISTRATION"
RESULT_TYPE = "IMMUTABLE_RESEARCH_POLICY_CANDIDATE_REGISTRATION"
FORWARD_SNAPSHOT_RULE = (
    "snapshot_id > registration_snapshot_id_watermark AND "
    "captured_at > registered_at AND "
    "captured_at > registration_captured_at_watermark"
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or create one immutable research policy candidate anchor."
        )
    )
    parser.add_argument("--scenario-file", required=True)
    parser.add_argument("--scenario-name", required=True)
    parser.add_argument(
        "--reference-snapshot-id", type=_positive_integer, required=True
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(args)


def _weights(value: dict[str, str]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def report(
    result: ResearchPolicyCandidateRegistrationResult, *, apply: bool
) -> list[str]:
    plan = result.plan
    candidate_id = result.candidate.id if result.candidate is not None else None
    return [
        f"report_type={REPORT_TYPE}",
        f"result_type={RESULT_TYPE}",
        "research_only=true",
        "candidate_registration=true",
        f"registration_performed={str(result.registration_created).lower()}",
        f"database_write={str(apply).lower()}",
        "external_calls=false",
        "live_policy_change=false",
        "automatic_policy_selection=false",
        "policy_decision_performed=false",
        "promotion_performed=false",
        "shadow_policy_created=false",
        f"registration_status={result.registration_status}",
        f"candidate_id={candidate_id}",
        f"candidate_schema_version={plan.candidate_schema_version}",
        f"scenario_name={plan.scenario_name}",
        f"scenario_definition_signature={plan.scenario_definition_signature}",
        f"component_weights={_weights(plan.component_weights)}",
        f"user_id={plan.user_id}",
        f"exchange={plan.exchange}",
        f"quote_asset={plan.quote_asset}",
        f"baseline_policy_signature={plan.baseline_policy_signature}",
        f"effective_top_n={plan.effective_top_n}",
        f"dataset_schema_version={plan.dataset_schema_version}",
        f"reference_snapshot_id={plan.reference_snapshot_id}",
        f"reference_snapshot_captured_at={plan.reference_snapshot_captured_at}",
        f"registered_at={plan.registered_at}",
        (
            "registration_snapshot_id_watermark="
            f"{plan.registration_snapshot_id_watermark}"
        ),
        (
            "registration_captured_at_watermark="
            f"{plan.registration_captured_at_watermark}"
        ),
        f"future_only_anchor_created={str(result.candidate is not None).lower()}",
        "forward_evidence_generated=false",
        "forward_validation_performed=false",
        f"forward_snapshot_rule={FORWARD_SNAPSHOT_RULE}",
    ]


def run(session, namespace: argparse.Namespace) -> tuple[list[str], int]:
    scenarios = load_scenario_file(namespace.scenario_file)
    scenario = select_scenario(scenarios, namespace.scenario_name)
    service = ResearchPolicyCandidateRegistryService(session)
    if namespace.apply:
        result = service.register(
            reference_snapshot_id=namespace.reference_snapshot_id,
            scenario=scenario,
        )
        session.commit()
    else:
        result = service.preview(
            reference_snapshot_id=namespace.reference_snapshot_id,
            scenario=scenario,
        )
        session.rollback()
    return report(result, apply=namespace.apply), 0


def main(args: list[str] | None = None) -> int:
    namespace = parse_arguments(args)
    try:
        from crypto_trading_bot.db.database import SessionLocal

        with SessionLocal() as session:
            try:
                lines, exit_code = run(session, namespace)
            except Exception:
                session.rollback()
                raise
            for line in lines:
                print(line)
            return exit_code
    except ReplayInputError as error:
        print(f"Research policy candidate input rejected. reason={error}")
        return 2
    except ResearchPolicyCandidateRegistrationError as error:
        print(f"Research policy candidate registration rejected. reason={error}")
        return 1
    except Exception as error:
        print(
            "Research policy candidate registration failed. "
            f"error_type={type(error).__name__}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
