from pathlib import Path


def test_full_live_policy_promotion_approval_migration_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "f0c2e4a6b8d1_add_full_live_promotion_approvals.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "f0c2e4a6b8d1"' in migration
    assert 'down_revision: str | Sequence[str] | None = "e9a1b3c5d7f2"' in migration
    assert '"full_live_policy_promotion_approvals"' in migration
    assert migration.count("postgresql.JSONB(astext_type=sa.Text())") == 3
    assert (
        '["canary_activation_id"], ["live_policy_canary_activations.id"]' in migration
    )
    assert (
        '["safety_binding_id"], ["live_policy_canary_safety_bindings.id"]' in migration
    )
    assert (
        '["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]'
        in migration
    )
    assert '["candidate_id"], ["research_policy_candidates.id"]' in migration
    assert '["user_id"], ["users.id"]' in migration
    assert (
        '["termination_event_id"], ["live_policy_canary_termination_events.id"]'
        in migration
    )
    assert 'name="uq_full_live_promotion_canary_activation"' in migration
    assert 'name="uq_full_live_promotion_review_decision"' in migration
    assert 'name="ck_full_live_promotion_source_manual"' in migration
    assert 'name="ck_full_live_promotion_review_eligible"' in migration
    assert '"human_approved_at >= review_evaluated_at"' in migration
    assert '"review_evaluated_at >= evidence_as_of"' in migration
    assert '"effective_top_n > 0"' in migration
    assert '"ix_full_live_promotion_candidate_approved"' in migration
    assert '"ix_full_live_promotion_context_approved"' in migration
    assert 'op.drop_table("full_live_policy_promotion_approvals")' in migration
