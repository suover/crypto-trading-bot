from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from crypto_trading_bot.config import settings as settings_module
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderReadError,
    UpbitSafeError,
)
from scripts import run_account_activity_sync_worker as worker_script
from scripts.check_upbit_account_activity_access import run_diagnostic
from scripts.run_bot_trading_pnl_worker import BOT_TRADING_PNL_WORKER_LOCK_KEY
from scripts.run_live_order_reconciliation_worker import (
    LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
)
from scripts.run_operational_alert_worker import OPERATIONAL_ALERT_WORKER_LOCK_KEY


class ReadOnlyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.deposit_error: Exception | None = None

    def get_closed_orders(self, **kwargs):
        self.calls.append("GET /v1/orders/closed")
        return []

    def get_deposits(self, **kwargs):
        self.calls.append("GET /v1/deposits")
        if self.deposit_error:
            raise self.deposit_error
        return []

    def get_withdrawals(self, **kwargs):
        self.calls.append("GET /v1/withdraws")
        return []

    def create_market_buy_order(self, **kwargs):
        raise AssertionError("write API must not be called")

    def create_market_sell_order(self, **kwargs):
        raise AssertionError("write API must not be called")


def test_permission_diagnostic_calls_only_three_read_endpoints(capsys) -> None:
    client = ReadOnlyClient()
    assert run_diagnostic(client) is True  # type: ignore[arg-type]
    assert client.calls == [
        "GET /v1/orders/closed",
        "GET /v1/deposits",
        "GET /v1/withdraws",
    ]
    output = capsys.readouterr().out
    assert output.count("=AVAILABLE") == 3
    for forbidden in ("Authorization", "Bearer", "JWT", "SECRET"):
        assert forbidden not in output


def test_permission_diagnostic_identifies_out_of_scope_and_continues(capsys) -> None:
    client = ReadOnlyClient()
    client.deposit_error = UpbitOrderReadError(
        UpbitSafeError(
            error_type="HTTPStatusError",
            operation="get_deposits",
            status_code=401,
            upbit_error_name="out_of_scope",
        )
    )
    assert run_diagnostic(client) is False  # type: ignore[arg-type]
    assert client.calls == [
        "GET /v1/orders/closed",
        "GET /v1/deposits",
        "GET /v1/withdraws",
    ]
    assert "deposits=OUT_OF_SCOPE" in capsys.readouterr().out


def test_disabled_worker_initializes_neither_database_nor_upbit(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            account_activity_sync_enabled=False,
            account_activity_sync_interval_seconds=300,
        ),
    )
    worker_script.run_worker(once=True)
    assert "inactive (disabled)" in capsys.readouterr().out


def test_worker_advisory_lock_key_is_unique() -> None:
    assert worker_script.ACCOUNT_ACTIVITY_SYNC_WORKER_LOCK_KEY not in {
        BOT_TRADING_PNL_WORKER_LOCK_KEY,
        LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
        OPERATIONAL_ALERT_WORKER_LOCK_KEY,
    }


class SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_args):
        return False


def test_enabled_worker_cycle_runs_apply_with_configured_overlap() -> None:
    session = Mock()
    session.scalar.return_value = SimpleNamespace(id=7)
    service = Mock()
    expected = SimpleNamespace(sources=())
    service.run.return_value = expected
    service_factory = Mock(return_value=service)
    overlap = timedelta(hours=24)

    result = worker_script.run_cycle(
        lambda: SessionContext(session), service_factory, overlap=overlap
    )

    assert result is expected
    service_factory.assert_called_once_with(session, overlap=overlap)
    service.run.assert_called_once_with(7, apply=True)


def test_worker_cycle_propagates_sync_failure() -> None:
    session = Mock()
    session.scalar.return_value = SimpleNamespace(id=7)
    service = Mock()
    service.run.side_effect = RuntimeError("sync failed")
    with pytest.raises(RuntimeError, match="sync failed"):
        worker_script.run_cycle(
            lambda: SessionContext(session),
            Mock(return_value=service),
            overlap=timedelta(hours=24),
        )
