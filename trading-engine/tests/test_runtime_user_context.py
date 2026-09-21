from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.runtime_user_resolver import (
    RuntimeUserConfigurationError,
    RuntimeUserResolver,
    require_trading_user_id,
    validate_user_id,
)


def test_trading_user_id_accepts_positive_integer_and_optional_blank() -> None:
    configured = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        trading_user_id=" 7 ",
    )
    missing = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        trading_user_id="",
    )

    assert configured.trading_user_id == 7
    assert missing.trading_user_id is None


@pytest.mark.parametrize("invalid", [True, 0, -1, "false", "1.5"])
def test_trading_user_id_rejects_invalid_values(invalid: object) -> None:
    with pytest.raises(ValueError, match="TRADING_USER_ID must be a positive integer"):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost:5432/test",
            trading_user_id=invalid,
        )


@pytest.mark.parametrize("invalid", [None, True, 0, -1, "1"])
def test_runtime_user_id_validation_fails_closed(invalid: object) -> None:
    if invalid is None:
        with pytest.raises(RuntimeUserConfigurationError, match="TRADING_USER_ID"):
            require_trading_user_id(invalid)
    else:
        with pytest.raises(RuntimeUserConfigurationError, match="positive integer"):
            validate_user_id(invalid)


def test_runtime_user_resolver_returns_exact_active_primary_key() -> None:
    session = MagicMock()
    expected = SimpleNamespace(id=2, name="Same Name", is_active=True)
    session.get.return_value = expected

    actual = RuntimeUserResolver(session).resolve(2)

    assert actual is expected
    session.get.assert_called_once()
    assert session.get.call_args.args[1] == 2
    session.scalar.assert_not_called()


def test_runtime_user_resolver_rejects_missing_user_without_fallback() -> None:
    session = MagicMock()
    session.get.return_value = None

    with pytest.raises(RuntimeUserConfigurationError, match="was not found"):
        RuntimeUserResolver(session).resolve(999999)

    session.scalar.assert_not_called()


def test_runtime_user_resolver_rejects_inactive_user() -> None:
    session = MagicMock()
    session.get.return_value = SimpleNamespace(id=3, is_active=False)

    with pytest.raises(RuntimeUserConfigurationError, match="is inactive"):
        RuntimeUserResolver(session).resolve(3)

    session.scalar.assert_not_called()


def test_same_name_users_and_account_snapshot_are_isolated_by_id(monkeypatch) -> None:
    from crypto_trading_bot.db.models import AnalysisRun
    from crypto_trading_bot.services.account_snapshot_service import (
        AccountSnapshotService,
    )

    users = {
        1: SimpleNamespace(id=1, name="Same Name", is_active=True),
        2: SimpleNamespace(id=2, name="Same Name", is_active=True),
    }

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.commits = 0

        def get(self, model, user_id):
            return users.get(user_id)

        def add(self, row) -> None:
            self.added.append(row)
            if isinstance(row, AnalysisRun):
                row.id = 100

        def add_all(self, rows) -> None:
            self.added.extend(rows)

        def flush(self) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(
        "crypto_trading_bot.services.account_snapshot_service.get_settings",
        lambda: SimpleNamespace(trading_mode="AI_APPROVAL"),
    )
    session = FakeSession()
    client = SimpleNamespace(
        get_accounts=lambda: [
            {
                "currency": "KRW",
                "balance": "1000",
                "locked": "0",
                "avg_buy_price": "0",
            }
        ]
    )

    analysis_run, snapshots = AccountSnapshotService(
        session, upbit_client=client
    ).collect_account_snapshots(
        1,
        pipeline_run_id="00000000-0000-0000-0000-000000000001",
    )

    assert analysis_run.user_id == 1
    assert snapshots
    assert all(snapshot.user_id == 1 for snapshot in snapshots)
    assert all(getattr(row, "user_id", 1) != 2 for row in session.added)
    assert RuntimeUserResolver(session).resolve(2) is users[2]
