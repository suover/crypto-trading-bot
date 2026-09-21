from pathlib import Path


def test_shadow_policy_promotion_approval_migration_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "c7e9a1b3d5f2_add_shadow_promotion_approvals.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "b6d8f0a2c4e5"' in migration
    assert '"shadow_policy_promotion_approvals"' in migration
    assert migration.count("postgresql.JSONB(astext_type=sa.Text())") == 5
    assert '["candidate_id"], ["research_policy_candidates.id"]' in migration
    assert '["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]' in migration
    assert 'name="uq_shadow_promotion_candidate"' in migration
    assert 'name="uq_shadow_promotion_enrollment"' in migration
    assert 'name="ck_shadow_promotion_review_eligible"' in migration
    assert 'name="ck_shadow_promotion_source_manual"' in migration
    assert '"human_approved_at >= review_evaluated_at"' in migration
    assert '"human_approved_at >= performance_evidence_as_of"' in migration
    assert 'op.drop_table("shadow_policy_promotion_approvals")' in migration
