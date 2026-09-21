from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from crypto_trading_bot.config import settings as settings_module
from crypto_trading_bot.services import bot_trading_pnl_service as service_module
from scripts import run_bot_trading_pnl_worker as worker_script


class SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_args):
        return False


def test_disabled_worker_does_not_initialize_database(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            bot_trading_pnl_enabled=False,
            bot_trading_pnl_interval_seconds=300,
        ),
    )
    worker_script.run_worker(once=True)
    assert "inactive (disabled)" in capsys.readouterr().out


@pytest.mark.parametrize("changed", [False, True])
def test_cycle_rebuilds_only_when_source_signature_changes(
    monkeypatch, changed
) -> None:
    session = Mock()
    session.execute.side_effect = [[(1, "UPBIT")], []]
    service = Mock()
    service.source_changed.return_value = changed
    service_class = Mock(return_value=service)
    monkeypatch.setattr(service_module, "BotTradingPnlService", service_class)

    scope_count, rebuilt_count = worker_script.run_cycle(
        lambda: SessionContext(session)
    )

    assert scope_count == 1
    assert rebuilt_count == int(changed)
    if changed:
        service.rebuild.assert_called_once_with(1, exchange="UPBIT", apply=True)
    else:
        service.rebuild.assert_not_called()
    session.commit.assert_called_once_with()


def test_cycle_failure_does_not_mutate_trading_source(monkeypatch) -> None:
    session = Mock()
    session.execute.side_effect = [[(1, "UPBIT")], []]
    service = Mock()
    service.source_changed.return_value = True
    service.rebuild.side_effect = RuntimeError("derived accounting failure")
    monkeypatch.setattr(
        service_module, "BotTradingPnlService", Mock(return_value=service)
    )

    with pytest.raises(RuntimeError, match="derived accounting failure"):
        worker_script.run_cycle(lambda: SessionContext(session))

    session.commit.assert_not_called()
