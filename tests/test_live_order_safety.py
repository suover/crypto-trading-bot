from decimal import Decimal

import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.live_order_safety import (
    LIVE_ORDER_CONFIRMATION_TEXT,
    LiveOrderSafetyError,
    check_live_order_safety,
    validate_live_order_request,
)


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://test:test@localhost:5432/test",
        "upbit_access_key": "access",
        "upbit_secret_key": "secret",
        "allowed_markets": "KRW-BTC,KRW-ETH",
        "max_order_amount_krw": 10000,
        "daily_max_order_amount_krw": 30000,
        "live_order_enabled": True,
        "live_order_confirmation": LIVE_ORDER_CONFIRMATION_TEXT,
    }
    values.update(overrides)

    return Settings(_env_file=None, **values)


def test_check_live_order_safety_blocks_when_disabled() -> None:
    settings = build_settings(
        live_order_enabled=False,
    )

    check = check_live_order_safety(settings)

    assert check.ready is False
    assert "live_order_enabled is false" in check.reasons


def test_check_live_order_safety_blocks_when_confirmation_does_not_match() -> None:
    settings = build_settings(
        live_order_confirmation="wrong",
    )

    check = check_live_order_safety(settings)

    assert check.ready is False
    assert "live_order_confirmation does not match required text" in check.reasons


def test_check_live_order_safety_blocks_when_upbit_keys_are_missing() -> None:
    settings = build_settings(
        upbit_access_key="",
        upbit_secret_key="",
    )

    check = check_live_order_safety(settings)

    assert check.ready is False
    assert "upbit_access_key is not configured" in check.reasons
    assert "upbit_secret_key is not configured" in check.reasons


def test_check_live_order_safety_ready_when_required_settings_are_valid() -> None:
    settings = build_settings()

    check = check_live_order_safety(settings)

    assert check.ready is True
    assert check.reasons == ()


def test_validate_live_order_request_allows_valid_buy_request() -> None:
    settings = build_settings()

    validate_live_order_request(
        settings=settings,
        action="BUY",
        market="KRW-BTC",
        amount_krw=Decimal("5000"),
    )


def test_validate_live_order_request_blocks_not_allowed_market() -> None:
    settings = build_settings()

    with pytest.raises(
        LiveOrderSafetyError,
        match="Live order market is not allowed",
    ):
        validate_live_order_request(
            settings=settings,
            action="BUY",
            market="KRW-XRP",
            amount_krw=Decimal("5000"),
        )


def test_validate_live_order_request_blocks_buy_amount_below_minimum() -> None:
    settings = build_settings()

    with pytest.raises(
        LiveOrderSafetyError,
        match="below minimum Upbit order amount",
    ):
        validate_live_order_request(
            settings=settings,
            action="BUY",
            market="KRW-BTC",
            amount_krw=Decimal("4999"),
        )


def test_validate_live_order_request_blocks_buy_amount_over_maximum() -> None:
    settings = build_settings(
        max_order_amount_krw=10000,
    )

    with pytest.raises(
        LiveOrderSafetyError,
        match="exceeds max_order_amount_krw",
    ):
        validate_live_order_request(
            settings=settings,
            action="BUY",
            market="KRW-BTC",
            amount_krw=Decimal("10001"),
        )


def test_validate_live_order_request_allows_valid_sell_request() -> None:
    settings = build_settings()

    validate_live_order_request(
        settings=settings,
        action="SELL",
        market="KRW-BTC",
        quantity=Decimal("0.0001"),
        amount_krw=Decimal("9000"),
    )


def test_validate_live_order_request_blocks_sell_quantity_zero() -> None:
    settings = build_settings()

    with pytest.raises(
        LiveOrderSafetyError,
        match="quantity must be greater than 0",
    ):
        validate_live_order_request(
            settings=settings,
            action="SELL",
            market="KRW-BTC",
            quantity=Decimal("0"),
        )
