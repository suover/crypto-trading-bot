from pathlib import Path


def test_full_live_policy_activation_migration_contract() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b2c3d4e5f6a7_add_full_live_policy_activation.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"' in migration
    for table in (
        "full_live_policy_activations",
        "full_live_policy_termination_events",
        "market_universe_policy_runs",
    ):
        assert f'"{table}"' in migration
        assert f'op.drop_table("{table}")' in migration
    assert (
        '["promotion_approval_id"], ["shadow_policy_promotion_approvals.id"]'
        in migration
    )
    assert '["candidate_id"], ["research_policy_candidates.id"]' in migration
    assert '["shadow_enrollment_id"], ["shadow_policy_enrollments.id"]' in migration
    assert '["activation_id"], ["full_live_policy_activations.id"]' in migration
    assert '["analysis_run_id"], ["analysis_runs.id"]' in migration
    assert "ck_full_live_activation_top_n_positive" in migration
    assert "ck_market_universe_policy_run_mode" in migration
