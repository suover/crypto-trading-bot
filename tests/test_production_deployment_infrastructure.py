from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def read_repository_file(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_production_runtime_safety_mode_preserves_limited_live_policy() -> None:
    script = read_repository_file("scripts/check_server_runtime_safety.sh")

    assert "--strict-live" in script
    assert "--production-live" in script
    assert "MAX_ORDER_AMOUNT_KRW가 5000 이하" in script
    assert "Production LIVE에서는 ai-trade-scheduler가 실행 중이어야 합니다" in script
    assert 'TRADING_MODE" == "AI_APPROVAL"' in script
    assert 'LIVE_ORDER_CONFIRMATION" == "ENABLE_LIVE_UPBIT_ORDERS"' in script
    assert "DAILY_MAX_ORDER_AMOUNT_KRW가 유효한 양의 정수가 아닙니다" in script
    assert "더 보수적인 유효한 안전 정책입니다" in script
    assert 'fail_check "production-live: DAILY_MAX_ORDER_AMOUNT_KRW가 MAX' not in script
    assert "LIVE_ORDER_CONFIRMATION=${" not in script


def test_deploy_script_is_fast_forward_exact_sha_and_fail_closed() -> None:
    script = read_repository_file("scripts/deploy_production.sh")

    assert "set -euo pipefail" in script
    assert "git merge --ff-only refs/remotes/origin/main" in script
    assert "--expected-sha" in script
    assert "git reset" not in script
    assert "git stash" not in script
    assert "docker compose down" not in script
    assert "migration downgrade" not in script
    assert "DEPLOY_PRODUCTION_FROM_TEMP" in script
    assert script.index("--help|-h)") < script.index("for required_command")
    assert script.index(
        'CURRENT_STAGE="Compose configuration validation"'
    ) < script.index('CURRENT_STAGE="fetch and exact SHA validation"')
    assert script.index(
        'CURRENT_STAGE="fetch and exact SHA validation"'
    ) < script.index('CURRENT_STAGE="database backup"')
    assert script.index('CURRENT_STAGE="database backup"') < script.index(
        'CURRENT_STAGE="environment backup"'
    )
    assert script.index('CURRENT_STAGE="environment backup"') < script.index(
        'CURRENT_STAGE="scheduler stop"'
    )
    assert script.index('CURRENT_STAGE="scheduler stop"') < script.index(
        'CURRENT_STAGE="fast-forward main update"'
    )
    assert script.index('CURRENT_STAGE="all application image build"') < script.index(
        'CURRENT_STAGE="application runtime stop"'
    )
    assert script.index('CURRENT_STAGE="application runtime stop"') < script.index(
        'CURRENT_STAGE="database migration"'
    )
    for service in (
        "migrate",
        "telegram-listener",
        "mock-order-retry-worker",
        "ai-trade-analysis",
        "ai-trade-scheduler",
    ):
        assert service in script


def test_ci_covers_all_branches_profiles_and_shell_syntax() -> None:
    workflow = read_repository_file(".github/workflows/ci.yml")

    for branch_pattern in (
        "main",
        '"feature/**"',
        '"fix/**"',
        '"refactor/**"',
        '"chore/**"',
        '"docs/**"',
        '"ci/**"',
    ):
        assert branch_pattern in workflow
    assert "bash -n scripts/check_server_runtime_safety.sh" in workflow
    assert "bash -n scripts/deploy_production.sh" in workflow
    assert (
        "docker compose --profile manual --profile scheduler config --quiet" in workflow
    )
    assert "docker compose --profile manual --profile scheduler build" in workflow


def test_production_workflow_is_manual_exact_sha_and_verified_ssh_only() -> None:
    workflow = read_repository_file(".github/workflows/deploy-production.yml")

    assert "on:\n  workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "confirmation" in workflow
    assert '!= "DEPLOY"' in workflow
    assert 'GITHUB_REF" != "refs/heads/main' in workflow
    assert "EXPECTED_SHA: ${{ github.sha }}" in workflow
    assert '--expected-sha "$EXPECTED_SHA"' in workflow
    assert "cancel-in-progress: false" in workflow
    assert "contents: read" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert 'git show "${EXPECTED_SHA}:scripts/deploy_production.sh"' in workflow
