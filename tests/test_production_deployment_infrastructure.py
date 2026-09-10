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
    assert "Upbit Order Chance preflight가 활성화되어 있습니다" in script
    assert "current order-condition preflight는 사용하지 않습니다" in script
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
        "live-order-reconciliation-worker",
        "account-activity-sync-worker",
        "bot-trading-pnl-worker",
        "portfolio-performance-worker",
        "operational-alert-worker",
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


def test_reconciliation_worker_is_in_default_runtime_and_all_deploy_stages() -> None:
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  live-order-reconciliation-worker:", 1)[1].split(
        "\nsecrets:", 1
    )[0]
    assert "profiles:" not in service
    assert "restart: unless-stopped" in service
    assert "condition: service_healthy" in service
    assert "condition: service_completed_successfully" in service
    assert "scripts.run_live_order_reconciliation_worker" in service
    deploy = read_repository_file("scripts/deploy_production.sh")
    stages = [
        "all application image build",
        "application runtime stop",
        "default runtime recreate",
        "post-deployment health check",
    ]
    for stage in stages:
        block = deploy.split(f'CURRENT_STAGE="{stage}"', 1)[1].split(
            "CURRENT_STAGE=", 1
        )[0]
        assert "live-order-reconciliation-worker" in block
    safety = read_repository_file("scripts/check_server_runtime_safety.sh")
    production_block = safety.split(
        'if [[ "$MODE" == "production-live" ]]; then\n  container_running "crypto-trading-ai-trade-scheduler"',
        1,
    )[1].split("elif container_running", 1)[0]
    assert "crypto-trading-live-order-reconciliation-worker" in production_block
    assert "LIVE_ORDER_RECONCILIATION_ENABLED" in production_block


def test_bot_pnl_worker_is_db_only_default_runtime_and_deploy_managed() -> None:
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  bot-trading-pnl-worker:", 1)[1].split("\nsecrets:", 1)[0]
    assert "profiles:" not in service
    assert "restart: unless-stopped" in service
    assert "scripts.run_bot_trading_pnl_worker" in service
    secret_block = service.split("    secrets:", 1)[1].split("    depends_on:", 1)[0]
    assert "postgres_password" in secret_block
    assert "upbit_access_key" not in secret_block
    assert "upbit_secret_key" not in secret_block
    assert "openai_api_key" not in secret_block
    assert "telegram_bot_token" not in secret_block
    for variable in (
        "OPENAI_API_KEY_FILE",
        "TELEGRAM_BOT_TOKEN_FILE",
        "UPBIT_ACCESS_KEY_FILE",
        "UPBIT_SECRET_KEY_FILE",
    ):
        assert f'{variable}: ""' in service
    deploy = read_repository_file("scripts/deploy_production.sh")
    for stage in (
        "all application image build",
        "application runtime stop",
        "default runtime recreate",
        "post-deployment health check",
    ):
        block = deploy.split(f'CURRENT_STAGE="{stage}"', 1)[1].split(
            "CURRENT_STAGE=", 1
        )[0]
        assert "bot-trading-pnl-worker" in block
    worker = read_repository_file("scripts/run_bot_trading_pnl_worker.py")
    for forbidden in ("UpbitClient", "OpenAI", "Telegram", "requests", "httpx"):
        assert forbidden not in worker


def test_portfolio_performance_worker_is_opt_in_and_minimum_secret() -> None:
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  portfolio-performance-worker:", 1)[1].split(
        "  operational-alert-worker:", 1
    )[0]
    assert "profiles:" not in service
    assert "restart: unless-stopped" in service
    assert "scripts.run_portfolio_performance_worker" in service
    secret_block = service.split("    secrets:", 1)[1].split("    depends_on:", 1)[0]
    assert "postgres_password" in secret_block
    for forbidden_secret in (
        "upbit_access_key",
        "upbit_secret_key",
        "openai_api_key",
        "telegram_bot_token",
    ):
        assert forbidden_secret not in secret_block
    worker = read_repository_file("scripts/run_portfolio_performance_worker.py")
    assert "portfolio_performance_enabled" in worker
    for forbidden in (
        "create_market_buy_order",
        "create_market_sell_order",
        "Telegram",
        "OpenAI",
    ):
        assert forbidden not in worker
    deploy = read_repository_file("scripts/deploy_production.sh")
    for stage in (
        "all application image build",
        "application runtime stop",
        "default runtime recreate",
        "post-deployment health check",
    ):
        block = deploy.split(f'CURRENT_STAGE="{stage}"', 1)[1].split(
            "CURRENT_STAGE=", 1
        )[0]
        assert "portfolio-performance-worker" in block
    safety = read_repository_file("scripts/check_server_runtime_safety.sh")
    assert "PORTFOLIO_PERFORMANCE_ENABLED" in safety
    assert "거래 실행에는 영향이 없습니다" in safety


def test_recommendation_outcome_worker_is_opt_in_public_only_and_deploy_managed() -> (
    None
):
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  recommendation-outcome-worker:", 1)[1].split(
        "\nsecrets:", 1
    )[0]
    assert "scripts.run_recommendation_outcome_worker" in service
    assert "postgres_password" in service
    for forbidden in (
        "openai_api_key",
        "telegram_bot_token\n",
        "upbit_access_key\n",
        "upbit_secret_key\n",
    ):
        assert f"- {forbidden}" not in service
    worker = read_repository_file("scripts/run_recommendation_outcome_worker.py")
    assert "recommendation_outcome_enabled" in worker
    for forbidden in ("create_order", "OpenAI", "Telegram", "ApprovalRequest"):
        assert forbidden not in worker
    deploy = read_repository_file("scripts/deploy_production.sh")
    assert deploy.count("recommendation-outcome-worker") >= 4
    safety = read_repository_file("scripts/check_server_runtime_safety.sh")
    assert "crypto-trading-recommendation-outcome-worker" in safety
    assert "RESEARCH_CANDIDATE_OUTCOME_ENABLED" in safety
    assert "Research Candidate Outcome analytics가 비활성화" in safety
    assert "Shadow Selection Evaluation analytics가 비활성화" in safety
    assert "거래 실행에는 영향이 없습니다" in safety
    assert "research-candidate-outcome-worker:" not in compose


def test_operational_alert_worker_is_minimum_secret_default_runtime() -> None:
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  operational-alert-worker:", 1)[1].split("\nsecrets:", 1)[
        0
    ]
    assert "profiles:" not in service
    assert "restart: unless-stopped" in service
    assert "scripts.run_operational_alert_worker" in service
    secret_block = service.split("    secrets:", 1)[1].split("    depends_on:", 1)[0]
    assert "postgres_password" in secret_block
    assert "telegram_bot_token" in secret_block
    assert "upbit_access_key" not in secret_block
    assert "upbit_secret_key" not in secret_block
    assert "openai_api_key" not in secret_block
    for variable in (
        "OPENAI_API_KEY_FILE",
        "UPBIT_ACCESS_KEY_FILE",
        "UPBIT_SECRET_KEY_FILE",
    ):
        assert f'{variable}: ""' in service
    deploy = read_repository_file("scripts/deploy_production.sh")
    for stage in (
        "all application image build",
        "application runtime stop",
        "default runtime recreate",
        "post-deployment health check",
    ):
        block = deploy.split(f'CURRENT_STAGE="{stage}"', 1)[1].split(
            "CURRENT_STAGE=", 1
        )[0]
        assert "operational-alert-worker" in block
    worker = read_repository_file("scripts/run_operational_alert_worker.py")
    for forbidden in ("UpbitClient", "OpenAI", "requests", "httpx"):
        assert forbidden not in worker


def test_operational_alert_diagnostic_is_read_only_and_network_free() -> None:
    diagnostic = read_repository_file("scripts/check_operational_alerts.py")
    for forbidden in (
        ".add(",
        ".flush(",
        ".commit(",
        "TelegramClient",
        "UpbitClient",
        "OpenAI",
        "requests",
        "httpx",
    ):
        assert forbidden not in diagnostic
    assert "session.rollback()" in diagnostic


def test_account_activity_worker_is_opt_in_get_only_and_minimum_secret() -> None:
    compose = read_repository_file("docker-compose.yml")
    service = compose.split("  account-activity-sync-worker:", 1)[1].split(
        "  bot-trading-pnl-worker:", 1
    )[0]
    assert "restart: unless-stopped" in service
    assert "scripts.run_account_activity_sync_worker" in service
    secret_block = service.split("    secrets:", 1)[1].split("    depends_on:", 1)[0]
    for required in ("postgres_password", "upbit_access_key", "upbit_secret_key"):
        assert required in secret_block
    assert "openai_api_key" not in secret_block
    assert "telegram_bot_token" not in secret_block
    for variable in ("OPENAI_API_KEY_FILE", "TELEGRAM_BOT_TOKEN_FILE"):
        assert f'{variable}: ""' in service

    worker = read_repository_file("scripts/run_account_activity_sync_worker.py")
    assert "account_activity_sync_enabled" in worker
    for forbidden in (
        "create_market_buy_order",
        "create_market_sell_order",
        "withdraws/coin",
        "withdraws/krw",
        "Telegram",
        "OpenAI",
    ):
        assert forbidden not in worker

    deploy = read_repository_file("scripts/deploy_production.sh")
    for stage in (
        "all application image build",
        "application runtime stop",
        "default runtime recreate",
        "post-deployment health check",
    ):
        block = deploy.split(f'CURRENT_STAGE="{stage}"', 1)[1].split(
            "CURRENT_STAGE=", 1
        )[0]
        assert "account-activity-sync-worker" in block


def test_account_activity_scripts_do_not_contain_write_endpoints_or_other_systems() -> (
    None
):
    combined = "\n".join(
        read_repository_file(path)
        for path in (
            "scripts/sync_account_activities.py",
            "scripts/check_upbit_account_activity_access.py",
            "scripts/run_account_activity_sync_worker.py",
            "crypto_trading_bot/services/account_activity_sync_service.py",
        )
    )
    for forbidden in (
        "create_market_buy_order",
        "create_market_sell_order",
        "/v1/withdraws/coin",
        "/v1/withdraws/krw",
        "cancel_order",
        "TelegramClient",
        "OpenAI",
        "BotInventoryLot",
        "PortfolioPositionSnapshot",
        "LiveExecutionLedgerService",
    ):
        assert forbidden not in combined
