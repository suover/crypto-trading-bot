from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB, dialect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.db import postgres_advisory_lock as lock_module
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderAmbiguousError,
    UpbitOrderNotFoundError,
    UpbitSafeError,
)
from crypto_trading_bot.services.live_order_execution_service import (
    recommendation_status_for_live_order,
)
from crypto_trading_bot.services.live_order_reconciliation_service import (
    LiveOrderReconciliationService,
)
from crypto_trading_bot.services.live_order_reconciliation_worker_service import (
    LiveOrderReconciliationWorkerService,
)
from scripts import run_live_order_reconciliation_worker as worker_script


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    # Isolated SQL query/transaction tests; parent FKs are not needed here.
    # PostgreSQL advisory locking is tested separately against the test DB.
    for model in (TradeRecommendation, OrderLog):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                    server_default=column.server_default,
                )
                for column in model.__table__.columns
            ),
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client():
    client = Mock(
        spec=[
            "get_order",
            "create_market_buy_order",
            "create_market_sell_order",
            "cancel_order",
        ]
    )
    client.get_order.return_value = {"state": "done", "executed_volume": "1"}
    yield client
    # Enforced for every worker test, including failure/UNKNOWN paths.
    client.create_market_buy_order.assert_not_called()
    client.create_market_sell_order.assert_not_called()
    client.cancel_order.assert_not_called()


def seed(
    session_factory,
    order_id=1,
    status="LIVE_WAIT",
    mode="LIVE",
    exchange="UPBIT",
    uuid="uuid-1",
):
    with session_factory() as session:
        session.add(
            TradeRecommendation(
                id=order_id,
                analysis_run_id=1,
                user_id=1,
                exchange=exchange,
                market="KRW-BTC",
                action="BUY",
                status="LIVE_EXECUTED",
                ai_response={},
            )
        )
        session.add(
            OrderLog(
                id=order_id,
                recommendation_id=order_id,
                user_id=1,
                trading_mode=mode,
                exchange=exchange,
                market="KRW-BTC",
                side="BUY",
                order_type="MARKET",
                status=status,
                exchange_order_id=uuid,
                raw_response={
                    "manual_reconciliation": True,
                    "original_audit": "preserved",
                },
            )
        )
        session.commit()


def make_worker(session_factory, client):
    return LiveOrderReconciliationWorkerService(
        session_factory, client, sleep_fn=Mock()
    )


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        ("LIVE_PLACED", "LIVE_EXECUTION_PENDING"),
        ("LIVE_WAIT", "LIVE_EXECUTION_PENDING"),
        ("LIVE_DONE", "LIVE_EXECUTED"),
        ("LIVE_EXECUTED_CANCELLED", "LIVE_EXECUTED"),
        ("LIVE_CANCELLED", "LIVE_EXECUTION_CANCELLED"),
        ("LIVE_FAILED", "LIVE_EXECUTION_FAILED"),
        ("LIVE_UNKNOWN", "LIVE_EXECUTION_UNKNOWN"),
        ("unrecognized", "LIVE_EXECUTION_UNKNOWN"),
    ],
)
def test_recommendation_status_mapping(local, expected):
    assert recommendation_status_for_live_order(local) == expected


@pytest.mark.parametrize(
    ("state", "volume", "local", "recommendation"),
    [
        ("done", "1", "LIVE_DONE", "LIVE_EXECUTED"),
        ("cancel", "0", "LIVE_CANCELLED", "LIVE_EXECUTION_CANCELLED"),
        ("cancel", "0.5", "LIVE_EXECUTED_CANCELLED", "LIVE_EXECUTED"),
        ("wait", "0", "LIVE_WAIT", "LIVE_EXECUTION_PENDING"),
        ("watch", "0", "LIVE_WAIT", "LIVE_EXECUTION_PENDING"),
    ],
)
def test_worker_transitions_persist_and_only_polls_pending(
    session_factory, client, state, volume, local, recommendation
):
    seed(session_factory)
    client.get_order.return_value = {
        "uuid": "uuid-1",
        "state": state,
        "executed_volume": volume,
    }
    worker = make_worker(session_factory, client)
    (result,) = worker.reconcile_pending()
    assert result.status == local
    with session_factory() as session:
        order = session.get(OrderLog, 1)
        assert order.status == local
        assert session.get(TradeRecommendation, 1).status == recommendation
        assert order.raw_response["reconciliation_source"] == "WORKER"
        assert order.raw_response["original_audit"] == "preserved"
        assert "manual_reconciliation" not in order.raw_response
    if state in {"done", "cancel"}:
        assert worker.reconcile_pending() == ()
        client.get_order.assert_called_once_with(uuid="uuid-1")


def test_worker_selection_excludes_terminal_mock_and_other_exchange(
    session_factory, client
):
    for order_id, status in enumerate(
        (
            "LIVE_PLACED",
            "LIVE_WAIT",
            "LIVE_UNKNOWN",
            "LIVE_DONE",
            "LIVE_CANCELLED",
            "LIVE_EXECUTED_CANCELLED",
            "LIVE_FAILED",
        ),
        1,
    ):
        seed(session_factory, order_id, status)
    seed(session_factory, 8, mode="MOCK")
    seed(session_factory, 9, exchange="BINANCE")
    worker = make_worker(session_factory, client)
    assert [candidate.order_log_id for candidate in worker.get_candidates()] == [
        1,
        2,
        3,
    ]


def test_unknown_recovers_by_identifier_without_submission(session_factory, client):
    seed(session_factory, status="LIVE_UNKNOWN", uuid=None)
    client.get_order.return_value = {"uuid": "found", "state": "done"}
    worker = make_worker(session_factory, client)
    assert worker.reconcile_pending()[0].status == "LIVE_DONE"
    client.get_order.assert_called_once_with(identifier="recommendation-1")
    with session_factory() as session:
        assert session.get(OrderLog, 1).exchange_order_id == "found"


@pytest.mark.parametrize(
    "error_class", [UpbitOrderAmbiguousError, UpbitOrderNotFoundError]
)
def test_lookup_failure_keeps_state_and_continues_other_orders(
    session_factory, client, error_class
):
    seed(session_factory, status="LIVE_UNKNOWN", uuid=None)
    seed(session_factory, 2)
    client.get_order.side_effect = [
        error_class(
            UpbitSafeError(
                "TemporaryFailure", "get_order", message="do-not-log-credential"
            )
        ),
        {"state": "done"},
    ]
    worker = make_worker(session_factory, client)
    results = worker.reconcile_pending()
    assert [result.outcome for result in results] == ["UNRESOLVED", "SYNCED"]
    assert "do-not-log-credential" not in repr(results)
    with session_factory() as session:
        assert session.get(OrderLog, 1).status == "LIVE_UNKNOWN"
        # Failure must not invent a new recommendation status or terminal result.
        assert session.get(TradeRecommendation, 1).status == "LIVE_EXECUTED"
        assert session.get(OrderLog, 2).status == "LIVE_DONE"
    assert [candidate.order_log_id for candidate in worker.get_candidates()] == [1]
    worker.sleep_fn.assert_called_once_with(0.2)


def test_round_robin_does_not_starve_orders_behind_unknown_batch(
    session_factory, client
):
    for order_id in range(1, 4):
        seed(session_factory, order_id, "LIVE_UNKNOWN", uuid=None)
    client.get_order.side_effect = UpbitOrderNotFoundError(
        UpbitSafeError("NotFound", "get_order")
    )
    worker = make_worker(session_factory, client)
    assert [worker.reconcile_pending(limit=1)[0].order_log_id for _ in range(4)] == [
        1,
        2,
        3,
        1,
    ]


def test_terminal_change_after_selection_is_rechecked_under_lock(
    session_factory, client
):
    seed(session_factory)
    worker = make_worker(session_factory, client)
    candidates = worker.get_candidates()
    with session_factory() as session:
        session.get(OrderLog, 1).status = "LIVE_CANCELLED"
        session.commit()
    worker.get_candidates = Mock(return_value=candidates)
    assert worker.reconcile_pending()[0].outcome == "SKIPPED"
    client.get_order.assert_not_called()


def test_manual_reconciliation_can_correct_legacy_terminal_recommendation(
    session_factory, client
):
    seed(session_factory, status="LIVE_CANCELLED")
    client.get_order.return_value = {"state": "cancel", "executed_volume": "0"}
    with session_factory() as session:
        result = LiveOrderReconciliationService(session, client).reconcile(1)
        assert result.recommendation.status == "LIVE_EXECUTION_CANCELLED"
        assert result.order_log.raw_response["reconciliation_source"] == "MANUAL"


def test_worker_uses_recommendation_first_skip_locked_and_skips_busy_order(client):
    statements = []
    recommendation = TradeRecommendation(id=1)

    def scalar(statement):
        statements.append(str(statement.compile(dialect=dialect())))
        return recommendation if len(statements) == 1 else None

    session = Mock()
    session.scalar.side_effect = scalar
    assert (
        LiveOrderReconciliationService(session, client).reconcile(1, source="WORKER")
        is None
    )
    assert "FROM trade_recommendations" in statements[0]
    assert "FROM order_logs" in statements[1]
    assert all("FOR UPDATE SKIP LOCKED" in statement for statement in statements)
    client.get_order.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("exchange", "BINANCE"), ("user_id", 99), ("market", "KRW-ETH")],
)
def test_identity_mismatch_is_unresolved_without_get(
    session_factory, client, field, value
):
    seed(session_factory)
    with session_factory() as session:
        setattr(session.get(TradeRecommendation, 1), field, value)
        session.commit()
    assert (
        make_worker(session_factory, client).reconcile_pending()[0].outcome
        == "UNRESOLVED"
    )
    client.get_order.assert_not_called()


def test_database_failure_is_not_swallowed(session_factory, client, monkeypatch):
    seed(session_factory)
    monkeypatch.setattr(
        LiveOrderReconciliationService,
        "reconcile",
        Mock(side_effect=OperationalError("test", {}, Exception("db failure"))),
    )
    with pytest.raises(OperationalError):
        make_worker(session_factory, client).reconcile_pending()


@pytest.fixture
def script_settings(monkeypatch):
    settings = SimpleNamespace(
        live_order_reconciliation_enabled=True,
        order_execution_mode="LIVE",
        live_order_reconciliation_interval_seconds=60,
        live_order_reconciliation_batch_size=20,
    )
    monkeypatch.setattr(worker_script, "get_settings", lambda: settings)
    return settings


def test_once_processes_one_batch_and_releases_lock(script_settings, monkeypatch):
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(lock_module, "PostgresAdvisoryLock", Mock(return_value=lock))
    service = Mock()
    service.reconcile_pending.return_value = ()
    monkeypatch.setattr(
        worker_script,
        "LiveOrderReconciliationWorkerService",
        Mock(return_value=service),
    )
    sleep = Mock()
    monkeypatch.setattr(worker_script.time, "sleep", sleep)
    assert worker_script.main(["--once"]) == 0
    service.reconcile_pending.assert_called_once_with(limit=20)
    lock.release.assert_called_once()
    sleep.assert_not_called()


def test_worker_lock_prevents_second_worker_with_real_postgres(
    script_settings, monkeypatch, capsys
):
    lock = PostgresAdvisoryLock(worker_script.LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY)
    service = Mock()
    monkeypatch.setattr(worker_script, "LiveOrderReconciliationWorkerService", service)
    try:
        assert lock.acquire()
        assert worker_script.main(["--once"]) == 0
        service.assert_not_called()
        assert (
            "Another live order reconciliation worker is already running"
            in capsys.readouterr().out
        )
    finally:
        lock.release()


@pytest.mark.parametrize(("mode", "enabled"), [("MOCK", True), ("LIVE", False)])
def test_disabled_or_mock_once_does_not_open_db_or_create_client(
    script_settings, monkeypatch, mode, enabled
):
    script_settings.order_execution_mode = mode
    script_settings.live_order_reconciliation_enabled = enabled
    lock = Mock()
    service = Mock()
    monkeypatch.setattr(lock_module, "PostgresAdvisoryLock", lock)
    monkeypatch.setattr(worker_script, "LiveOrderReconciliationWorkerService", service)
    assert worker_script.main(["--once"]) == 0
    lock.assert_not_called()
    service.assert_not_called()


def test_fatal_worker_error_exits_nonzero_without_raw_credentials(
    script_settings, monkeypatch, capsys
):
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(lock_module, "PostgresAdvisoryLock", Mock(return_value=lock))
    service = Mock()
    service.reconcile_pending.side_effect = OperationalError(
        "secret-connection-url", {}, Exception("private-key")
    )
    monkeypatch.setattr(
        worker_script,
        "LiveOrderReconciliationWorkerService",
        Mock(return_value=service),
    )
    assert worker_script.main(["--once"]) == 1
    output = capsys.readouterr().out
    assert "OperationalError" in output
    assert "secret-connection-url" not in output
    assert "private-key" not in output
    lock.release.assert_called_once()


def test_configuration_failure_is_sanitized_before_db_initialization(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        worker_script, "get_settings", Mock(side_effect=ValueError("raw-config-secret"))
    )
    assert worker_script.main(["--once"]) == 1
    output = capsys.readouterr().out
    assert "ValueError" in output
    assert "raw-config-secret" not in output


def test_manual_status_script_still_reports_cancelled_without_execution(
    session_factory, client, monkeypatch, capsys
):
    from scripts import check_live_order_status

    seed(session_factory, status="LIVE_CANCELLED")
    client.get_order.return_value = {"state": "cancel", "executed_volume": "0"}
    monkeypatch.setattr(check_live_order_status, "SessionLocal", session_factory)
    monkeypatch.setattr(
        check_live_order_status,
        "LiveOrderReconciliationService",
        lambda session: LiveOrderReconciliationService(session, client),
    )
    assert check_live_order_status.main(["--recommendation-id", "1"]) == 0
    assert "local_status=LIVE_CANCELLED" in capsys.readouterr().out
    with session_factory() as session:
        assert session.get(TradeRecommendation, 1).status == "LIVE_EXECUTION_CANCELLED"


def test_continuous_worker_runs_next_cycle_and_releases_on_interrupt(
    script_settings, monkeypatch
):
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(lock_module, "PostgresAdvisoryLock", Mock(return_value=lock))
    service = Mock()
    service.reconcile_pending.side_effect = [(), KeyboardInterrupt()]
    monkeypatch.setattr(
        worker_script,
        "LiveOrderReconciliationWorkerService",
        Mock(return_value=service),
    )
    sleeper = Mock()
    monkeypatch.setattr(worker_script.time, "sleep", sleeper)
    assert worker_script.main([]) == 0
    assert service.reconcile_pending.call_count == 2
    sleeper.assert_called_once()
    lock.release.assert_called_once()
