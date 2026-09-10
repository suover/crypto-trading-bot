from pathlib import Path


def test_shadow_policy_evaluation_migration_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b6d8f0a2c4e5_add_shadow_policy_evaluations.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "a5c7e9f1b3d4"' in migration
    assert '"shadow_policy_evaluations"' in migration
    assert migration.count("postgresql.JSONB(astext_type=sa.Text())") == 4
    assert '["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]' in migration
    assert '["candidate_id"], ["research_policy_candidates.id"]' in migration
    assert (
        '["strategy_replay_snapshot_id"], ["strategy_replay_snapshots.id"]' in migration
    )
    assert 'name="uq_shadow_evaluation_enrollment_snapshot"' in migration
    assert 'name="ck_shadow_evaluation_status"' in migration
    assert 'name="ck_shadow_evaluation_snapshot_after_watermark"' in migration
    assert 'op.drop_table("shadow_policy_evaluations")' in migration
