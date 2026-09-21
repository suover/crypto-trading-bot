from pathlib import Path

from crypto_trading_bot.db.models import ResearchPolicyCandidate


def test_registry_model_and_migration_are_aligned():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "f4a8c2e6b1d3_add_research_policy_candidates.py"
    ).read_text(encoding="utf-8")
    assert ResearchPolicyCandidate.__tablename__ == "research_policy_candidates"
    assert 'down_revision: str | Sequence[str] | None = "e2f4a6b8c0d1"' in migration
    assert "postgresql.JSONB" in migration
    assert migration.count("sa.DateTime(timezone=True)") == 3
    for constraint in (
        "uq_rpc_context_scenario_name",
        "uq_rpc_context_definition_signature",
        "ck_rpc_effective_top_n_positive",
        "ck_rpc_snapshot_watermark_positive",
    ):
        assert constraint in migration
    for index in (
        "ix_research_policy_candidates_reference_snapshot_id",
        "ix_research_policy_candidates_user_id",
        "ix_rpc_context_registered_at",
    ):
        assert index in migration
    assert 'ForeignKeyConstraint(["user_id"], ["users.id"])' in migration
    assert "sa.ForeignKeyConstraint(" in migration
    assert '["reference_snapshot_id"]' in migration
    assert '["strategy_replay_snapshots.id"]' in migration
    assert "ondelete" not in migration
