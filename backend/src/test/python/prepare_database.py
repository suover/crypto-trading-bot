"""Testcontainers-only schema bootstrap. Never reads repository .env or secrets.

The parent Java test supplies an ephemeral container URL. The real Alembic tree
remains the sole DDL source; ORM objects below are test fixtures, not migrations.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from crypto_trading_bot.config import settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchCandidateAutomationState,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    StrategyReplaySnapshot,
    User,
)


def main():
    # No generic DATABASE_URL fallback: only the test runner may supply this value.
    url = os.environ["BACKEND_TEST_MIGRATION_URL"]
    if not url.startswith("postgresql+psycopg://fixture_owner:fixture-only@"):
        raise RuntimeError("Expected isolated Testcontainers fixture credentials")
    settings.get_settings = lambda: SimpleNamespace(database_url=url)
    engine_root = Path(__file__).resolve().parents[4] / "trading-engine"
    config = Config(str(engine_root / "alembic.ini"))
    config.set_main_option("script_location", str(engine_root / "migrations"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    time = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            User(id=i, name=f"fixture-{i}", is_active=i != 3) for i in range(1, 5)
        )
        session.flush()
        for offset, (user, exchange, quote) in enumerate(
            [(1, "UPBIT", "KRW")] * 3
            + [(2, "UPBIT", "KRW"), (1, "BINANCE", "KRW"), (1, "UPBIT", "BTC")]
        ):
            snapshot_id = 101 + offset
            session.add(
                AnalysisRun(
                    id=snapshot_id,
                    user_id=user,
                    run_type="TEST",
                    trading_mode="AI_APPROVAL",
                    status="SUCCESS",
                )
            )
            session.flush()
            snapshot = StrategyReplaySnapshot(
                id=snapshot_id,
                analysis_run_id=snapshot_id,
                pipeline_run_id=str(snapshot_id),
                user_id=user,
                exchange=exchange,
                quote_asset=quote,
                dataset_schema_version="dataset-v1",
                policy_signature="baseline",
                policy_data={},
                research_candidate_count=20,
                prefilter_candidate_count=10,
                ranked_candidate_count=7,
                final_candidate_count=8,
                captured_at=time + timedelta(hours=min(offset, 1)),
                created_at=time,
            )
            session.add(snapshot)
            session.flush()
            candidate = ResearchPolicyCandidate(
                id=201 + offset,
                candidate_schema_version="candidate-v1",
                user_id=user,
                exchange=exchange,
                quote_asset=quote,
                scenario_name=[
                    "Momentum A",
                    "Momentum B",
                    "Value",
                    "Private",
                    "Other",
                    "BTC",
                ][offset],
                scenario_definition_signature=f"definition-{offset}",
                component_weights={
                    "liquidity": "0.123456789012345678901234567890",
                    "momentum": "0.5",
                },
                reference_snapshot_id=snapshot_id,
                reference_snapshot_captured_at=snapshot.captured_at,
                dataset_schema_version="dataset-v1",
                baseline_policy_signature="baseline",
                effective_top_n=7,
                registered_at=time + timedelta(days=1 if offset == 2 else 0),
                registration_snapshot_id_watermark=snapshot_id,
                registration_captured_at_watermark=snapshot.captured_at,
            )
            session.add(candidate)
            session.flush()
            if offset in (0, 3, 4, 5):
                session.add(
                    ShadowPolicyEnrollment(
                        id=301 + offset,
                        enrollment_schema_version="enrollment-v1",
                        candidate_id=candidate.id,
                        candidate_schema_version="candidate-v1",
                        user_id=user,
                        exchange=exchange,
                        quote_asset=quote,
                        scenario_name=candidate.scenario_name,
                        scenario_definition_signature=candidate.scenario_definition_signature,
                        component_weights=candidate.component_weights,
                        dataset_schema_version="dataset-v1",
                        baseline_policy_signature="baseline",
                        effective_top_n=7,
                        candidate_registered_at=candidate.registered_at,
                        candidate_registration_snapshot_id_watermark=snapshot_id,
                        candidate_registration_captured_at_watermark=snapshot.captured_at,
                        gate_result_type="fixture",
                        gate_policy_schema_version="fixture",
                        gate_policy_signature="not-exposed",
                        gate_policy_definition={},
                        gate_status="ELIGIBLE_FOR_REVIEW",
                        gate_evaluated_at=time,
                        gate_forward_snapshot_id_ceiling=snapshot_id,
                        gate_checks=[],
                        gate_evidence_provenance={},
                        gate_decision_signature="not-exposed",
                        shadow_enrolled_at=time,
                        shadow_snapshot_id_watermark=snapshot_id,
                        shadow_captured_at_watermark=snapshot.captured_at,
                    )
                )
        for user, exchange, quote, signature, top_n in [
            (1, "UPBIT", "KRW", "a", 7),
            (1, "UPBIT", "KRW", "b", 5),
            (2, "UPBIT", "KRW", "a", 7),
            (1, "BINANCE", "KRW", "a", 7),
            (1, "UPBIT", "BTC", "a", 7),
        ]:
            session.add(
                ResearchCandidateAutomationState(
                    user_id=user,
                    exchange=exchange,
                    quote_asset=quote,
                    dataset_schema_version="dataset-v1",
                    baseline_policy_signature=signature,
                    effective_top_n=top_n,
                    last_observed_snapshot_id=103,
                    reviewed_snapshot_id=102,
                    reviewed_captured_at=time,
                    reviewed_created_at=time,
                    review_status="OBSERVED",
                    review_cycle_cap=2,
                    review_context_cap=2,
                )
            )
        session.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE ROLE backend_reader LOGIN PASSWORD 'reader-fixture-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            )
        )
        connection.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
        connection.execute(text("REVOKE ALL ON DATABASE backend_fixture FROM PUBLIC"))
        connection.execute(
            text("GRANT CONNECT ON DATABASE backend_fixture TO backend_reader")
        )
        connection.execute(text("GRANT USAGE ON SCHEMA public TO backend_reader"))
        connection.execute(
            text(
                "GRANT SELECT ON users, research_policy_candidates, strategy_replay_snapshots, "
                "research_candidate_automation_states, shadow_policy_enrollments TO backend_reader"
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
