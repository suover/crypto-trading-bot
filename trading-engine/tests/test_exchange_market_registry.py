from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.base import Base
from crypto_trading_bot.db.models import Exchange, ExchangeMarket, MarketCandle
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)
from crypto_trading_bot.services.market_candle_service import MarketCandleService
from crypto_trading_bot.services.market_snapshot_service import MarketSnapshotService


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://test:test@localhost:5432/test",
        "max_order_amount_krw": 10000,
        "daily_max_order_amount_krw": 30000,
        "allowed_markets": "KRW-IGNORED",
    }
    values.update(overrides)
    return Settings(**values)


def build_registry_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Exchange.__table__, ExchangeMarket.__table__],
    )
    return Session(engine)


def test_load_active_upbit_markets_uses_enabled_exchange_and_priority() -> None:
    session = build_registry_session()
    session.add_all(
        [
            Exchange(code="UPBIT", name="Upbit", enabled=True, tradable=True),
            Exchange(code="DISABLED", name="Disabled", enabled=False, tradable=False),
            ExchangeMarket(
                id=1,
                exchange_code="UPBIT",
                market="KRW-ETH",
                base_asset="ETH",
                quote_asset="KRW",
                status="ACTIVE",
                priority=2,
            ),
            ExchangeMarket(
                id=2,
                exchange_code="UPBIT",
                market="KRW-BTC",
                base_asset="BTC",
                quote_asset="KRW",
                status="ACTIVE",
                priority=1,
            ),
            ExchangeMarket(
                id=3,
                exchange_code="UPBIT",
                market="KRW-XRP",
                base_asset="XRP",
                quote_asset="KRW",
                status="PAUSED",
                priority=3,
            ),
            ExchangeMarket(
                id=4,
                exchange_code="DISABLED",
                market="KRW-SOL",
                base_asset="SOL",
                quote_asset="KRW",
                status="ACTIVE",
                priority=1,
            ),
        ]
    )
    session.commit()

    markets = ExchangeMarketRegistryService(
        session,
        settings=build_settings(),
    ).load_active_markets_for_exchange("UPBIT")

    assert [market.market for market in markets] == ["KRW-BTC", "KRW-ETH"]


def test_load_allowed_active_markets_filters_and_preserves_configured_order() -> None:
    session = build_registry_session()
    session.add_all(
        [
            Exchange(code="UPBIT", name="Upbit", enabled=True, tradable=True),
            ExchangeMarket(
                id=1,
                exchange_code="UPBIT",
                market="KRW-BTC",
                base_asset="BTC",
                quote_asset="KRW",
                status="ACTIVE",
                priority=1,
            ),
            ExchangeMarket(
                id=2,
                exchange_code="UPBIT",
                market="KRW-ETH",
                base_asset="ETH",
                quote_asset="KRW",
                status="ACTIVE",
                priority=2,
            ),
            ExchangeMarket(
                id=3,
                exchange_code="UPBIT",
                market="KRW-XRP",
                base_asset="XRP",
                quote_asset="KRW",
                status="ACTIVE",
                priority=3,
            ),
        ]
    )
    session.commit()

    markets = ExchangeMarketRegistryService(
        session,
        settings=build_settings(allowed_markets="KRW-XRP,KRW-BTC"),
    ).load_allowed_active_markets_for_exchange("UPBIT")

    assert [market.market for market in markets] == ["KRW-XRP", "KRW-BTC"]


def test_load_allowed_active_markets_rejects_empty_allowlist() -> None:
    session = MagicMock()
    service = ExchangeMarketRegistryService(
        session,
        settings=build_settings(allowed_markets=""),
    )

    with pytest.raises(ValueError) as exc_info:
        service.load_allowed_active_markets_for_exchange("UPBIT")

    assert str(exc_info.value) == "Allowed markets must not be empty. exchange=UPBIT"
    session.query.assert_not_called()


def test_load_allowed_active_markets_rejects_missing_inactive_and_disabled() -> None:
    session = build_registry_session()
    session.add_all(
        [
            Exchange(code="UPBIT", name="Upbit", enabled=True, tradable=True),
            Exchange(code="DISABLED", name="Disabled", enabled=False, tradable=False),
            ExchangeMarket(
                id=1,
                exchange_code="UPBIT",
                market="KRW-BTC",
                base_asset="BTC",
                quote_asset="KRW",
                status="ACTIVE",
            ),
            ExchangeMarket(
                id=2,
                exchange_code="UPBIT",
                market="KRW-ETH",
                base_asset="ETH",
                quote_asset="KRW",
                status="PAUSED",
            ),
            ExchangeMarket(
                id=3,
                exchange_code="DISABLED",
                market="KRW-XRP",
                base_asset="XRP",
                quote_asset="KRW",
                status="ACTIVE",
            ),
        ]
    )
    session.commit()
    service = ExchangeMarketRegistryService(
        session,
        settings=build_settings(allowed_markets="KRW-BTC,KRW-ETH,KRW-XRP,KRW-DOGE"),
    )

    with pytest.raises(ValueError) as exc_info:
        service.load_allowed_active_markets_for_exchange("UPBIT")

    assert str(exc_info.value) == (
        "Allowed markets are missing or inactive for exchange. "
        "exchange=UPBIT, markets=['KRW-ETH', 'KRW-XRP', 'KRW-DOGE']"
    )


def test_order_amounts_inherit_overrides_defaults_and_apply_env_caps() -> None:
    session = build_registry_session()
    exchange = Exchange(
        code="UPBIT",
        name="Upbit",
        enabled=True,
        tradable=True,
        default_max_order_amount=Decimal("8000"),
        default_daily_max_order_amount=Decimal("100000"),
    )
    inherited_market = ExchangeMarket(
        id=1,
        exchange_code="UPBIT",
        market="KRW-BTC",
        base_asset="BTC",
        quote_asset="KRW",
    )
    override_market = ExchangeMarket(
        id=2,
        exchange_code="UPBIT",
        market="KRW-ETH",
        base_asset="ETH",
        quote_asset="KRW",
        max_order_amount_override=Decimal("12000"),
        daily_max_order_amount_override=Decimal("20000"),
    )
    session.add_all([exchange, inherited_market, override_market])
    session.commit()
    service = ExchangeMarketRegistryService(session, settings=build_settings())

    assert service.calculate_final_max_order_amount(inherited_market) == Decimal("8000")
    assert service.calculate_final_max_order_amount(override_market) == Decimal("10000")
    assert service.calculate_final_daily_max_order_amount(inherited_market) == Decimal(
        "30000"
    )
    assert service.calculate_final_daily_max_order_amount(override_market) == Decimal(
        "20000"
    )


def test_market_candle_service_uses_registry_markets() -> None:
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    upbit_client = MagicMock()
    upbit_client.get_minute_candles.return_value = [
        {
            "candle_date_time_utc": "2026-07-11T00:00:00",
            "opening_price": 1,
            "high_price": 2,
            "low_price": 1,
            "trade_price": 2,
            "candle_acc_trade_price": 10,
            "candle_acc_trade_volume": 5,
        }
    ]
    registry = MagicMock()
    registry.load_allowed_active_markets_for_exchange.return_value = [
        SimpleNamespace(market="KRW-BTC")
    ]

    result = MarketCandleService(
        session,
        upbit_client=upbit_client,
        registry_service=registry,
    ).collect_minute_candles()

    registry.load_allowed_active_markets_for_exchange.assert_called_once_with("UPBIT")
    upbit_client.get_minute_candles.assert_called_once_with(
        market="KRW-BTC",
        unit=15,
        count=50,
    )
    assert result == {"KRW-BTC": 1}
    assert session.add.call_args.args[0].exchange == "UPBIT"
    assert isinstance(session.add.call_args.args[0], MarketCandle)


def test_market_snapshot_service_uses_registry_markets() -> None:
    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=1, is_active=True)
    upbit_client = MagicMock()
    upbit_client.get_tickers.return_value = [
        {
            "market": "KRW-BTC",
            "trade_price": 1000,
            "signed_change_rate": 0.1,
            "acc_trade_price_24h": 1000000,
            "acc_trade_volume_24h": 100,
        }
    ]
    registry = MagicMock()
    registry.load_allowed_active_markets_for_exchange.return_value = [
        SimpleNamespace(market="KRW-BTC")
    ]

    _, snapshots = MarketSnapshotService(
        session,
        upbit_client=upbit_client,
        registry_service=registry,
    ).collect_market_snapshots(1)

    registry.load_allowed_active_markets_for_exchange.assert_called_once_with("UPBIT")
    upbit_client.get_tickers.assert_called_once_with(["KRW-BTC"])
    assert [snapshot.market for snapshot in snapshots] == ["KRW-BTC"]
    assert snapshots[0].exchange == "UPBIT"
    assert snapshots[0].volume_24h == Decimal("1000000")
