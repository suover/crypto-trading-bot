from decimal import Decimal
from typing import Any

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.services.live_order_safety import LIVE_ORDER_CONFIRMATION_TEXT
from crypto_trading_bot.services.live_trading_readiness import (
    LiveTradingReadinessReport,
    LiveTradingReadinessService,
)


class FakeSession:
    def __init__(
        self,
        execute_error: Exception | None = None,
    ) -> None:
        self.execute_error = execute_error
        self.executed_statements: list[object] = []

    def execute(
        self,
        statement: object,
    ) -> object:
        if self.execute_error is not None:
            raise self.execute_error

        self.executed_statements.append(statement)

        return object()


class FakeUpbitClient:
    def __init__(
        self,
        accounts: list[dict[str, Any]] | None = None,
        tickers: list[dict[str, Any]] | None = None,
        accounts_error: Exception | None = None,
        tickers_error: Exception | None = None,
    ) -> None:
        self.accounts = accounts if accounts is not None else []
        self.tickers = tickers if tickers is not None else []
        self.accounts_error = accounts_error
        self.tickers_error = tickers_error
        self.requested_ticker_markets: list[str] | None = None

    def get_accounts(self) -> list[dict[str, Any]]:
        if self.accounts_error is not None:
            raise self.accounts_error

        return self.accounts

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        if self.tickers_error is not None:
            raise self.tickers_error

        self.requested_ticker_markets = markets

        return self.tickers

    def create_market_buy_order(
        self,
        market: str,
        amount_krw: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("create_market_buy_order must not be called")

    def create_market_sell_order(
        self,
        market: str,
        quantity: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("create_market_sell_order must not be called")


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def set_live_ready_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "access")
    monkeypatch.setenv(
        "UPBIT_SECRET_KEY",
        "test-secret-key-for-live-trading-readiness-unit-test-0123456789abcdef",
    )
    monkeypatch.setenv("ALLOWED_MARKETS", "KRW-BTC,KRW-ETH")
    monkeypatch.setenv("MAX_ORDER_AMOUNT_KRW", "10000")
    monkeypatch.setenv("DAILY_MAX_ORDER_AMOUNT_KRW", "30000")
    monkeypatch.setenv("LIVE_ORDER_ENABLED", "true")
    monkeypatch.setenv("LIVE_ORDER_CONFIRMATION", LIVE_ORDER_CONFIRMATION_TEXT)
    monkeypatch.setenv("ORDER_EXECUTION_MODE", "LIVE")

    clear_settings_cache()


def get_item(report: LiveTradingReadinessReport, name: str) -> object:
    return next(item for item in report.items if item.name == name)


def build_service(
    *,
    session: FakeSession | None = None,
    upbit_client: FakeUpbitClient | None = None,
) -> LiveTradingReadinessService:
    return LiveTradingReadinessService(
        session=session or FakeSession(),  # type: ignore[arg-type]
        upbit_client=upbit_client
        or FakeUpbitClient(
            accounts=[{"currency": "BTC", "balance": "0.1"}],
            tickers=[{"market": "KRW-BTC"}, {"market": "KRW-ETH"}],
        ),  # type: ignore[arg-type]
    )


def test_check_reports_ready_when_live_trading_preflight_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_ready_env(monkeypatch)
    upbit_client = FakeUpbitClient(
        accounts=[{"currency": "BTC", "balance": "0.1"}],
        tickers=[{"market": "KRW-BTC"}, {"market": "KRW-ETH"}],
    )
    service = build_service(upbit_client=upbit_client)

    report = service.check()

    assert report.ready is True
    assert all(item.ready for item in report.items)
    assert upbit_client.requested_ticker_markets == ["KRW-BTC", "KRW-ETH"]


def test_check_reports_not_ready_when_live_order_safety_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_ready_env(monkeypatch)
    monkeypatch.setenv("LIVE_ORDER_ENABLED", "false")
    clear_settings_cache()
    service = build_service()

    report = service.check()

    assert report.ready is False
    item = get_item(report, "live_order_safety")
    assert item.ready is False
    assert "live_order_enabled is false" in item.message


def test_check_reports_not_ready_when_database_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_ready_env(monkeypatch)
    service = build_service(
        session=FakeSession(execute_error=RuntimeError("database unavailable")),
    )

    report = service.check()

    assert report.ready is False
    item = get_item(report, "database_connection")
    assert item.ready is False
    assert "RuntimeError" in item.message
    assert "database unavailable" in item.message


def test_check_reports_not_ready_when_upbit_accounts_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_ready_env(monkeypatch)
    service = build_service(
        upbit_client=FakeUpbitClient(
            accounts_error=RuntimeError("accounts unavailable"),
            tickers=[{"market": "KRW-BTC"}],
        ),
    )

    report = service.check()

    assert report.ready is False
    item = get_item(report, "upbit_accounts")
    assert item.ready is False
    assert "RuntimeError" in item.message
    assert "accounts unavailable" in item.message


def test_check_reports_not_ready_when_upbit_tickers_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_live_ready_env(monkeypatch)
    service = build_service(
        upbit_client=FakeUpbitClient(
            accounts=[{"currency": "BTC", "balance": "0.1"}],
            tickers_error=RuntimeError("tickers unavailable"),
        ),
    )

    report = service.check()

    assert report.ready is False
    item = get_item(report, "upbit_tickers")
    assert item.ready is False
    assert "RuntimeError" in item.message
    assert "tickers unavailable" in item.message


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
