from pathlib import Path


def test_live_policy_canary_migration_contract():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "d8f0a2c4e6b1_add_limited_live_canary.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "c7e9a1b3d5f2"' in migration
    assert '"live_policy_canary_activations"' in migration
    assert '"live_policy_canary_runs"' in migration
    assert 'name="uq_live_canary_activation_approval"' in migration
    assert 'name="uq_live_canary_run_analysis"' in migration
    assert 'name="uq_live_canary_run_activation_pipeline"' in migration
    assert 'name="uq_live_canary_run_activation_ordinal"' in migration
    assert 'name="ck_live_canary_run_policy_used"' in migration
    assert 'op.drop_table("live_policy_canary_runs")' in migration
    assert 'op.drop_table("live_policy_canary_activations")' in migration


def test_live_policy_canary_v1b_migration_extends_current_head():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "e9a1b3c5d7f2_add_limited_live_canary_v1b.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: str | Sequence[str] | None = "d8f0a2c4e6b1"' in migration
    assert '"live_policy_canary_safety_bindings"' in migration
    assert '"live_policy_canary_termination_events"' in migration
    assert 'name="uq_live_canary_safety_binding_activation"' in migration
    assert 'name="uq_live_canary_termination_activation"' in migration
    assert "sa.Numeric(30, 10)" in migration
    assert "postgresql.JSONB" in migration
    assert 'op.drop_table("live_policy_canary_termination_events")' in migration
    assert 'op.drop_table("live_policy_canary_safety_bindings")' in migration
