from types import SimpleNamespace

from crypto_trading_bot.config import settings as settings_module
from scripts import run_portfolio_performance_worker as worker_script
from scripts.run_account_activity_sync_worker import (
    ACCOUNT_ACTIVITY_SYNC_WORKER_LOCK_KEY,
)
from scripts.run_bot_trading_pnl_worker import BOT_TRADING_PNL_WORKER_LOCK_KEY
from scripts.run_live_order_reconciliation_worker import (
    LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
)
from scripts.run_operational_alert_worker import OPERATIONAL_ALERT_WORKER_LOCK_KEY


def test_disabled_worker_does_not_initialize_database_or_market_provider(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            portfolio_performance_enabled=False,
            portfolio_performance_interval_seconds=300,
        ),
    )

    worker_script.run_worker(once=True)

    assert "inactive (disabled)" in capsys.readouterr().out


def test_portfolio_performance_worker_lock_key_is_unique() -> None:
    assert worker_script.PORTFOLIO_PERFORMANCE_WORKER_LOCK_KEY not in {
        ACCOUNT_ACTIVITY_SYNC_WORKER_LOCK_KEY,
        BOT_TRADING_PNL_WORKER_LOCK_KEY,
        LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
        OPERATIONAL_ALERT_WORKER_LOCK_KEY,
    }
