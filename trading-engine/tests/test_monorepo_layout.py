from pathlib import Path
import runpy
import shutil
import tomllib

import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent


def test_engine_build_context_and_repository_operational_boundaries():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("    build:\n      context: ./trading-engine\n") == 11
    assert "      context: .\n" not in compose
    assert compose.count("    env_file:\n      - .env\n") == 11
    assert "${SECRET_DIR:-.secrets}/postgres_password" in compose
    assert {
        path.name for path in (REPO_ROOT / "scripts").iterdir() if path.is_file()
    } == {
        "backup_db.sh",
        "check_server_runtime_safety.sh",
        "deploy_production.sh",
    }
    dockerfile = (ENGINE_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "WORKDIR /app" in dockerfile
    assert "COPY pyproject.toml uv.lock .python-version ./" in dockerfile
    metadata = tomllib.loads((ENGINE_ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["name"] == "crypto-trading-bot"
    assert (ENGINE_ROOT / metadata["project"]["readme"]).is_file()


@pytest.mark.parametrize("layout", ["trading-engine", "app"])
def test_settings_keep_host_root_and_container_configuration_paths(
    tmp_path, monkeypatch, layout
):
    engine = tmp_path / layout
    module = engine / "crypto_trading_bot" / "config" / "settings.py"
    module.parent.mkdir(parents=True)
    shutil.copyfile(ENGINE_ROOT / "crypto_trading_bot/config/settings.py", module)
    config_root = tmp_path if layout == "trading-engine" else engine
    secret = config_root / ".secrets" / "local-test-token"
    secret.parent.mkdir()
    secret.write_text("fixture-only", encoding="utf-8")
    (config_root / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://test:test@localhost/test\n"
        "TRADING_USER_ID=123\n"
        "OPENAI_API_KEY_FILE=.secrets/local-test-token\n",
        encoding="utf-8",
    )
    if layout == "trading-engine":
        (engine / ".env").write_text("TRADING_USER_ID=999\n", encoding="utf-8")
    monkeypatch.chdir(engine)
    # Clear inherited settings without reading any real .env or secret file.
    from crypto_trading_bot.config.settings import Settings

    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    namespace = runpy.run_path(str(module))
    settings = namespace["Settings"]()
    assert settings.trading_user_id == 123
    assert settings.openai_api_key == "fixture-only"
    assert not settings.live_order_enabled
    assert not settings.live_dynamic_market_enabled
