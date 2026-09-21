from pathlib import Path


def test_shadow_policy_enrollment_migration_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "a5c7e9f1b3d4_add_shadow_policy_enrollments.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "f4a8c2e6b1d3"' in migration
    assert '"shadow_policy_enrollments"' in migration
    assert "postgresql.JSONB(astext_type=sa.Text())" in migration
    assert (
        'sa.ForeignKeyConstraint(["candidate_id"], ["research_policy_candidates.id"])'
        in migration
    )
    assert (
        'sa.UniqueConstraint("candidate_id", name="uq_shadow_enrollment_candidate")'
        in migration
    )
    assert (
        '"shadow_snapshot_id_watermark >= gate_forward_snapshot_id_ceiling"'
        in migration
    )
    assert '"shadow_enrolled_at >= gate_evaluated_at"' in migration
    assert 'op.drop_table("shadow_policy_enrollments")' in migration
