import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
)
from crypto_trading_bot.services.order_execution_mode import (
    OrderExecutionModeError,
    assert_order_execution_mode_ready,
    check_order_execution_mode,
    normalize_order_execution_mode,
)


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://test:test@localhost:5432/test",
        "upbit_access_key": "access",
        "upbit_secret_key": "secret",
        "allowed_markets": "KRW-BTC,KRW-ETH",
        "max_order_amount_krw": 10000,
        "daily_max_order_amount_krw": 30000,
        "live_order_enabled": False,
        "live_order_confirmation": "",
        "order_execution_mode": "MOCK",
    }
    values.update(overrides)

    return Settings(**values)


def test_normalize_order_execution_mode_accepts_mock() -> None:
    assert normalize_order_execution_mode("mock") == "MOCK"


def test_normalize_order_execution_mode_accepts_live() -> None:
    assert normalize_order_execution_mode(" live ") == "LIVE"


def test_normalize_order_execution_mode_rejects_invalid_mode() -> None:
    with pytest.raises(
        OrderExecutionModeError,
        match="must be MOCK or LIVE",
    ):
        normalize_order_execution_mode("PAPER")


def test_check_order_execution_mode_ready_for_mock_by_default() -> None:
    settings = build_settings(
        order_execution_mode="MOCK",
        live_order_enabled=False,
        live_order_confirmation="",
    )

    check = check_order_execution_mode(settings)

    assert check.mode == "MOCK"
    assert check.ready is True
    assert check.reasons == ()
    assert check.live_safety_ready is None
    assert check.live_safety_reasons == ()


def test_check_order_execution_mode_blocks_live_when_safety_is_not_ready() -> None:
    settings = build_settings(
        order_execution_mode="LIVE",
        live_order_enabled=False,
        live_order_confirmation="",
    )

    check = check_order_execution_mode(settings)

    assert check.mode == "LIVE"
    assert check.ready is False
    assert check.reasons == ("live order safety check is not ready",)
    assert check.live_safety_ready is False
    assert "live_order_enabled is false" in check.live_safety_reasons
    assert (
        "live_order_confirmation does not match required text"
        in check.live_safety_reasons
    )


def test_check_order_execution_mode_ready_for_live_when_safety_is_ready() -> None:
    settings = build_settings(
        order_execution_mode="LIVE",
        live_order_enabled=True,
        live_order_confirmation=LIVE_ORDER_CONFIRMATION_TEXT,
    )

    check = check_order_execution_mode(settings)

    assert check.mode == "LIVE"
    assert check.ready is True
    assert check.reasons == ()
    assert check.live_safety_ready is True
    assert check.live_safety_reasons == ()


def test_assert_order_execution_mode_ready_returns_mock() -> None:
    settings = build_settings(
        order_execution_mode="MOCK",
    )

    assert assert_order_execution_mode_ready(settings) == "MOCK"


def test_assert_order_execution_mode_ready_raises_when_live_safety_is_not_ready() -> (
    None
):
    settings = build_settings(
        order_execution_mode="LIVE",
        live_order_enabled=False,
        live_order_confirmation="",
    )

    with pytest.raises(
        OrderExecutionModeError,
        match="Order execution mode is not ready",
    ):
        assert_order_execution_mode_ready(settings)
