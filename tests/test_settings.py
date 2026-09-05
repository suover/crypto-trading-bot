from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from crypto_trading_bot.config.settings import Settings


SETTINGS_ENV_NAMES = (
    "DATABASE_URL",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_PASSWORD_FILE",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
    "OPENAI_TRADE_MODEL",
    "OPENAI_REASONING_EFFORT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN_FILE",
    "UPBIT_ACCESS_KEY",
    "UPBIT_ACCESS_KEY_FILE",
    "UPBIT_SECRET_KEY",
    "UPBIT_SECRET_KEY_FILE",
    "LIVE_ORDER_CHANCE_PREFLIGHT_ENABLED",
    "LIVE_ORDER_RECONCILIATION_ENABLED",
    "LIVE_ORDER_RECONCILIATION_INTERVAL_SECONDS",
    "LIVE_ORDER_RECONCILIATION_BATCH_SIZE",
    "OPERATIONAL_ALERTING_ENABLED",
    "OPERATIONAL_ALERT_INTERVAL_SECONDS",
    "LIVE_ORDER_STALE_ALERT_AFTER_SECONDS",
    "OPERATIONAL_ALERT_MAX_RETRIES",
    "OPERATIONAL_ALERT_RETRY_DELAYS_MINUTES",
    "ACCOUNT_ACTIVITY_SYNC_ENABLED",
    "ACCOUNT_ACTIVITY_SYNC_INTERVAL_SECONDS",
    "ACCOUNT_ACTIVITY_SYNC_OVERLAP_HOURS",
    "PORTFOLIO_PERFORMANCE_ENABLED",
    "PORTFOLIO_PERFORMANCE_INTERVAL_SECONDS",
    "RECOMMENDATION_OUTCOME_ENABLED",
    "RECOMMENDATION_OUTCOME_INTERVAL_SECONDS",
    "RECOMMENDATION_OUTCOME_HORIZONS_MINUTES",
    "RECOMMENDATION_OUTCOME_BATCH_SIZE",
    "RESEARCH_CANDIDATE_OUTCOME_ENABLED",
    "RESEARCH_CANDIDATE_OUTCOME_INTERVAL_SECONDS",
    "RESEARCH_CANDIDATE_OUTCOME_HORIZONS_MINUTES",
    "RESEARCH_CANDIDATE_OUTCOME_BATCH_SIZE",
    "STRATEGY_REPLAY_DATASET_ENABLED",
    "PORTFOLIO_COINGECKO_ASSET_MAPPING",
    "PORTFOLIO_EXCLUDED_ASSETS",
)


@pytest.fixture(autouse=True)
def clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def write_secret(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_openai_trade_defaults() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
    )

    assert settings.openai_trade_model == "gpt-5.6-sol"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.market_universe_mode == "STATIC"
    assert settings.live_dynamic_market_enabled is False
    assert settings.strategy_replay_dataset_enabled is False
    assert settings.research_candidate_outcome_enabled is False
    assert settings.research_candidate_outcome_interval_seconds == 300
    assert settings.research_candidate_outcome_horizon_list == (60, 240, 1440)
    assert settings.research_candidate_outcome_batch_size == 20
    assert settings.live_order_chance_preflight_enabled is False
    assert settings.analysis_timeframe_list == ["15m", "60m", "240m", "1d"]
    assert settings.live_order_reconciliation_enabled is True
    assert settings.live_order_reconciliation_interval_seconds == 60
    assert settings.live_order_reconciliation_batch_size == 20
    assert settings.bot_trading_pnl_enabled is False
    assert settings.bot_trading_pnl_interval_seconds == 300
    assert settings.operational_alerting_enabled is False
    assert settings.operational_alert_interval_seconds == 60
    assert settings.live_order_stale_alert_after_seconds == 600
    assert settings.operational_alert_max_retries == 3
    assert settings.operational_alert_retry_delay_list == [1, 5, 15]
    assert settings.account_activity_sync_enabled is False
    assert settings.account_activity_sync_interval_seconds == 300
    assert settings.account_activity_sync_overlap_hours == 168
    assert settings.portfolio_performance_enabled is False
    assert settings.portfolio_performance_interval_seconds == 300
    assert settings.portfolio_coingecko_asset_identity_map == {}
    assert settings.portfolio_excluded_asset_set == set()
    assert settings.portfolio_valuation_policy_signature is None


def test_portfolio_excluded_assets_are_normalized_and_signature_is_deterministic() -> (
    None
):
    variants = (
        "QI,APENFT",
        "APENFT,QI",
        " qi, APENFT,qi ",
    )
    configured = [
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            portfolio_excluded_assets=value,
        )
        for value in variants
    ]

    assert all(
        item.portfolio_excluded_asset_set == {"QI", "APENFT"} for item in configured
    )
    assert len({item.portfolio_valuation_policy_signature for item in configured}) == 1
    different = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        portfolio_excluded_assets="QI",
    )
    assert (
        different.portfolio_valuation_policy_signature
        != configured[0].portfolio_valuation_policy_signature
    )


def test_portfolio_coingecko_mapping_is_explicit_and_does_not_change_trading_lists() -> (
    None
):
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        portfolio_coingecko_asset_mapping=" apenft=APENFT, qi=qiswap ",
        allowed_markets="KRW-BTC",
        market_blocklist="KRW-XRP",
    )

    assert settings.portfolio_coingecko_asset_identity_map == {
        "APENFT": "apenft",
        "QI": "qiswap",
    }
    assert settings.allowed_market_list == ["KRW-BTC"]
    assert settings.market_block_list == ["KRW-XRP"]


@pytest.mark.parametrize("value", ["QI", "=qiswap", "QI=", "QI=qiswap,QI=benqi"])
def test_portfolio_coingecko_mapping_rejects_ambiguous_entries(value: str) -> None:
    with pytest.raises(ValueError, match="PORTFOLIO_COINGECKO_ASSET_MAPPING"):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            portfolio_coingecko_asset_mapping=value,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operational_alert_interval_seconds", 9),
        ("operational_alert_interval_seconds", 3601),
        ("live_order_stale_alert_after_seconds", 0),
        ("operational_alert_max_retries", -1),
        ("operational_alert_max_retries", 11),
    ],
)
def test_operational_alert_settings_reject_invalid_bounds(field, value) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            **{field: value},
        )


@pytest.mark.parametrize(
    ("retry_count", "delays"),
    [(3, "1,5"), (1, "0"), (1, "invalid")],
)
def test_operational_alert_settings_validate_retry_schedule(
    retry_count, delays
) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            operational_alert_max_retries=retry_count,
            operational_alert_retry_delays_minutes=delays,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("live_order_reconciliation_interval_seconds", 0),
        ("live_order_reconciliation_interval_seconds", 3601),
        ("live_order_reconciliation_batch_size", 0),
        ("live_order_reconciliation_batch_size", 101),
    ],
)
def test_reconciliation_settings_reject_unbounded_polling(field, value) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            **{field: value},
        )


@pytest.mark.parametrize("value", [0, 29, 86401])
def test_bot_pnl_worker_interval_rejects_unbounded_values(value) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            bot_trading_pnl_interval_seconds=value,
        )


def test_openai_trade_settings_support_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_TRADE_MODEL", "test-trade-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")

    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
    )

    assert settings.openai_trade_model == "test-trade-model"
    assert settings.openai_reasoning_effort == "low"


def test_research_candidate_outcome_horizons_are_sorted_and_deduplicated() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        research_candidate_outcome_horizons_minutes="240,60,1440,60",
    )
    assert settings.research_candidate_outcome_horizon_list == (60, 240, 1440)


@pytest.mark.parametrize("value", ["", "60,,240", "bad", "0", "-1,60"])
def test_research_candidate_outcome_horizons_reject_invalid_values(value) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            research_candidate_outcome_horizons_minutes=value,
        )


@pytest.mark.parametrize("value", [0, 501])
def test_research_candidate_outcome_batch_rejects_invalid_bounds(value) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            research_candidate_outcome_batch_size=value,
        )


def test_openai_reasoning_effort_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "")

    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
    )

    assert settings.openai_reasoning_effort == ""


def test_secret_file_content_is_loaded_and_trimmed(tmp_path: Path) -> None:
    secret_file = write_secret(tmp_path / "openai_api_key", "file-openai-key\n")

    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        openai_api_key_file=str(secret_file),
    )

    assert settings.openai_api_key == "file-openai-key"


def test_secret_file_takes_priority_over_direct_value(tmp_path: Path) -> None:
    secret_file = write_secret(tmp_path / "upbit_access_key", "file-access-key")

    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        upbit_access_key="direct-access-key",
        upbit_access_key_file=str(secret_file),
    )

    assert settings.upbit_access_key == "file-access-key"


def test_direct_value_is_used_without_secret_file() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        telegram_bot_token="direct-telegram-token",
        telegram_bot_token_file="",
    )

    assert settings.telegram_bot_token == "direct-telegram-token"


def test_database_url_supports_special_characters(tmp_path: Path) -> None:
    password = "test@password:/#%?"
    password_file = write_secret(tmp_path / "database_password", password)

    settings = Settings(
        _env_file=None,
        database_host="database.test",
        database_port=6543,
        database_name="test_database",
        database_user="test_user",
        database_password_file=str(password_file),
    )

    parsed = make_url(settings.database_url)

    assert parsed.username == "test_user"
    assert parsed.password == password
    assert parsed.host == "database.test"
    assert parsed.port == 6543
    assert parsed.database == "test_database"


def test_missing_required_database_secret_file_fails_clearly(
    tmp_path: Path,
) -> None:
    missing_file = tmp_path / "missing_database_password"

    with pytest.raises(ValueError) as exc_info:
        Settings(
            _env_file=None,
            database_password_file=str(missing_file),
            openai_api_key="direct-unrelated-secret",
        )

    error_message = str(exc_info.value)
    assert "DATABASE_PASSWORD_FILE" in error_message
    assert str(missing_file) in error_message
    assert "direct-unrelated-secret" not in error_message


def test_empty_required_database_secret_file_fails_clearly(
    tmp_path: Path,
) -> None:
    empty_file = write_secret(tmp_path / "empty_database_password", "")

    with pytest.raises(ValueError) as exc_info:
        Settings(
            _env_file=None,
            database_password_file=str(empty_file),
        )

    error_message = str(exc_info.value)
    assert "DATABASE_PASSWORD_FILE must not be empty" in error_message
