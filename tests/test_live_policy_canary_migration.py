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
