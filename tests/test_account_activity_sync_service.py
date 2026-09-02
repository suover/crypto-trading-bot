from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    JSON,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.db.models import (
    AccountActivity,
    AccountActivitySyncState,
    OrderLog,
    PortfolioSnapshot,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderReadError,
    UpbitSafeError,
)
from crypto_trading_bot.services.account_activity_sync_service import (
    ACCOUNT_ACTIVITY_PRIVATE_REQUEST_INTERVAL_SECONDS,
    AccountActivityCoverageError,
    AccountActivityNormalizationError,
    AccountActivitySyncService,
    UPBIT_CLOSED_ORDER,
    UPBIT_DEPOSIT,
    UPBIT_WITHDRAWAL,
    normalize_closed_order,
    normalize_deposit,
    normalize_withdrawal,
)


START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 10, tzinfo=UTC)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (
        User,
        TradeRecommendation,
        OrderLog,
        PortfolioSnapshot,
        AccountActivity,
        AccountActivitySyncState,
    ):
        constraints = []
        if model is AccountActivity:
            constraints.append(
                UniqueConstraint(
                    "user_id", "exchange", "source_type", "exchange_activity_id"
                )
            )
        elif model is AccountActivitySyncState:
            constraints.append(UniqueConstraint("user_id", "exchange", "source_type"))
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    JSON()
                    if isinstance(column.type, JSONB)
                    else Integer()
                    if isinstance(column.type, BigInteger)
                    else column.type,
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                    server_default=column.server_default,
                )
                for column in model.__table__.columns
            ),
            *constraints,
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def closed_order(
    uuid: str = "closed-1",
    *,
    identifier: str | None = None,
    side: str = "bid",
    state: str = "done",
    executed_volume: str = "0.01",
) -> dict[str, object]:
    return {
        "uuid": uuid,
        "side": side,
        "ord_type": "price" if side == "bid" else "market",
        "price": "10000" if side == "bid" else None,
        "state": state,
        "market": "KRW-BTC",
        "created_at": "2026-08-05T12:00:00+09:00",
        "volume": None if side == "bid" else "0.02",
        "executed_volume": executed_volume,
        "executed_funds": "10000",
        "paid_fee": "5",
        "identifier": identifier,
    }


def deposit(
    uuid: str = "deposit-1", *, state: str = "PROCESSING", currency: str = "BTC"
) -> dict[str, object]:
    return {
        "type": "deposit",
        "uuid": uuid,
        "currency": currency,
        "net_type": currency,
        "state": state,
        "created_at": "2026-08-05T12:00:00+09:00",
        "done_at": None,
        "amount": "0.01" if currency != "KRW" else "100000",
        "fee": "0",
        "transaction_type": "default",
        "txid": "must-not-be-persisted",
        "address": "must-not-be-persisted",
    }


def withdrawal(
    uuid: str = "withdrawal-1",
    *,
    state: str = "WAITING",
    currency: str = "ETH",
) -> dict[str, object]:
    row = deposit(uuid, state=state, currency=currency)
    row["type"] = "withdraw"
    row["fee"] = "0.001"
    return row


class FakeClient:
    def __init__(self) -> None:
        self.closed_rows: list[dict[str, object]] = []
        self.deposit_pages: dict[int | str, list[dict[str, object]]] = {}
        self.withdrawal_pages: dict[int | str, list[dict[str, object]]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.deposit_error: Exception | None = None

    def get_closed_orders(self, **kwargs):
        self.calls.append(("closed", kwargs))
        return deepcopy(self.closed_rows)

    def get_deposits(self, **kwargs):
        self.calls.append(("deposits", kwargs))
        if self.deposit_error:
            raise self.deposit_error
        return deepcopy(self.deposit_pages.get(kwargs.get("to", 1), []))

    def get_withdrawals(self, **kwargs):
        self.calls.append(("withdrawals", kwargs))
        return deepcopy(self.withdrawal_pages.get(kwargs.get("to", 1), []))


def add_user_and_bot_order(session) -> User:
    user = User(name="Minsu")
    session.add(user)
    session.flush()
    recommendation = TradeRecommendation(
        id=1,
        analysis_run_id=1,
        user_id=user.id,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
        status="APPROVED",
    )
    session.add(recommendation)
    session.add(
        OrderLog(
            recommendation_id=1,
            user_id=user.id,
            trading_mode="LIVE",
            exchange="UPBIT",
            market="KRW-BTC",
            side="BUY",
            order_type="MARKET",
            status="LIVE_DONE",
            exchange_order_id="bot-by-uuid",
            created_at=datetime(2026, 8, 5, tzinfo=UTC),
        )
    )
    session.commit()
    return user


def test_normalizes_order_deposit_withdrawal_semantics_without_krw_valuation() -> None:
    order = normalize_closed_order(closed_order(side="ask", state="cancel"))
    assert order.activity_type == "ORDER"
    assert order.cash_flow_direction is None
    assert order.side == "SELL"
    assert order.executed_quantity == Decimal("0.01")
    assert order.executed_funds_krw == Decimal("10000")

    krw_deposit = normalize_deposit(deposit(currency="KRW", state="ACCEPTED"))
    crypto_withdrawal = normalize_withdrawal(withdrawal(currency="ETH", state="DONE"))
    assert krw_deposit.cash_flow_direction == "IN"
    assert krw_deposit.amount == Decimal("100000")
    assert crypto_withdrawal.cash_flow_direction == "OUT"
    assert crypto_withdrawal.amount == Decimal("0.01")
    assert crypto_withdrawal.executed_funds_krw is None
    assert "txid" not in (crypto_withdrawal.source_metadata or {})
    assert "address" not in (crypto_withdrawal.source_metadata or {})


def test_cancelled_order_does_not_assume_fill_and_partial_cancel_keeps_fill() -> None:
    no_fill = closed_order(state="cancel", executed_volume="0")
    no_fill["executed_funds"] = "0"
    partial = closed_order(state="cancel", executed_volume="0.005")
    assert normalize_closed_order(no_fill).executed_quantity == Decimal("0")
    assert normalize_closed_order(partial).executed_quantity == Decimal("0.005")


@pytest.mark.parametrize(
    "state",
    [
        "PROCESSING",
        "ACCEPTED",
        "CANCELLED",
        "REJECTED",
        "TRAVEL_RULE_SUSPECTED",
        "REFUNDING",
        "REFUNDED",
    ],
)
def test_preserves_official_deposit_states(state: str) -> None:
    assert normalize_deposit(deposit(state=state)).state == state


@pytest.mark.parametrize(
    "state", ["WAITING", "PROCESSING", "DONE", "FAILED", "CANCELLED", "REJECTED"]
)
def test_preserves_official_withdrawal_states(state: str) -> None:
    assert normalize_withdrawal(withdrawal(state=state)).state == state


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1", "bad", {}])
def test_rejects_invalid_decimal(value: object) -> None:
    row = deposit()
    row["amount"] = value
    with pytest.raises(AccountActivityNormalizationError):
        normalize_deposit(row)


def test_rejects_naive_or_invalid_timestamp() -> None:
    for value in ("2026-08-01T00:00:00", "not-a-time", None):
        row = withdrawal()
        row["created_at"] = value
        with pytest.raises(AccountActivityNormalizationError):
            normalize_withdrawal(row)


def test_bot_origin_requires_uuid_or_persisted_identifier_relation(
    session_factory,
) -> None:
    client = FakeClient()
    client.closed_rows = [
        closed_order("bot-by-uuid"),
        closed_order("bot-by-identifier", identifier="recommendation-1"),
        closed_order("fake-identifier", identifier="recommendation-999"),
        closed_order("unknown", identifier=None),
    ]
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        result = AccountActivitySyncService(session, client).run(
            user.id, start_at=START, end_at=END, apply=True
        )
        origins = {
            row.exchange_activity_id: row.origin
            for row in session.scalars(select(AccountActivity))
        }
    order_result = result.sources[0]
    assert origins == {
        "bot-by-uuid": "BOT",
        "bot-by-identifier": "BOT",
        "fake-identifier": "EXTERNAL",
        "unknown": "EXTERNAL",
    }
    assert order_result.bot_order_count == 2
    assert order_result.external_order_count == 2


def test_apply_is_idempotent_and_updates_mutable_transfer_state(
    session_factory,
) -> None:
    client = FakeClient()
    client.closed_rows = [closed_order()]
    client.deposit_pages = {1: [deposit()]}
    client.withdrawal_pages = {1: [withdrawal()]}
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        service = AccountActivitySyncService(session, client)
        first = service.run(user.id, start_at=START, end_at=END, apply=True)
        second = service.run(user.id, start_at=START, end_at=END, apply=True)
        assert first.sources[0].new_count == 1
        assert all(source.new_count == 0 for source in second.sources)
        assert session.scalar(select(func.count()).select_from(AccountActivity)) == 3

        client.deposit_pages[1][0]["state"] = "ACCEPTED"
        client.deposit_pages[1][0]["done_at"] = "2026-08-06T01:00:00+09:00"
        third = service.run(user.id, start_at=START, end_at=END, apply=True)
        stored = session.scalar(
            select(AccountActivity).where(
                AccountActivity.exchange_activity_id == "deposit-1"
            )
        )
        assert third.sources[1].update_count == 1
        assert stored is not None and stored.state == "ACCEPTED"
        assert session.scalar(select(func.count()).select_from(AccountActivity)) == 3


def test_incremental_sync_uses_source_coverage_with_overlap(session_factory) -> None:
    client = FakeClient()
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        service = AccountActivitySyncService(
            session,
            client,
            overlap=timedelta(days=2),
            private_request_interval_seconds=0,
        )
        service.run(user.id, start_at=START, end_at=END, apply=True)
        client.calls.clear()
        service.run(user.id, end_at=END + timedelta(days=1), apply=False)
    closed_call = next(kwargs for name, kwargs in client.calls if name == "closed")
    assert datetime.fromisoformat(closed_call["start_time"]) == END - timedelta(days=2)


def test_initial_baseline_ignores_other_exchange_live_orders(session_factory) -> None:
    client = FakeClient()
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        session.add(
            OrderLog(
                recommendation_id=1,
                user_id=user.id,
                trading_mode="LIVE",
                exchange="OTHER",
                market="USD-BTC",
                side="BUY",
                order_type="MARKET",
                status="LIVE_DONE",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        session.commit()
        service = AccountActivitySyncService(
            session,
            client,
            overlap=timedelta(days=1),
            private_request_interval_seconds=0,
        )
        service.run(user.id, end_at=END, apply=False)

    first_closed_call = next(
        kwargs for name, kwargs in client.calls if name == "closed"
    )
    assert datetime.fromisoformat(first_closed_call["start_time"]) == datetime(
        2026, 8, 4, tzinfo=UTC
    )


def test_dry_run_has_no_commit_or_database_mutation(
    session_factory, monkeypatch
) -> None:
    client = FakeClient()
    client.closed_rows = [closed_order()]
    client.deposit_pages = {1: [deposit()]}
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        commit_calls = 0

        def forbidden_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1

        monkeypatch.setattr(session, "commit", forbidden_commit)
        result = AccountActivitySyncService(session, client).run(
            user.id, start_at=START, end_at=END, apply=False
        )
        assert sum(source.new_count for source in result.sources) == 2
        assert commit_calls == 0
        assert session.scalar(select(func.count()).select_from(AccountActivity)) == 0
        assert (
            session.scalar(select(func.count()).select_from(AccountActivitySyncState))
            == 0
        )


def test_closed_order_windows_never_exceed_seven_days() -> None:
    client = FakeClient()
    service = AccountActivitySyncService(None, client)  # type: ignore[arg-type]
    service._fetch_closed_orders(START, START + timedelta(days=15))
    calls = [kwargs for name, kwargs in client.calls if name == "closed"]
    assert len(calls) == 3
    for call in calls:
        start = datetime.fromisoformat(call["start_time"])
        end = datetime.fromisoformat(call["end_time"])
        assert end - start <= timedelta(days=7)


def test_closed_order_limit_hit_safely_splits_window() -> None:
    class SplittingClient(FakeClient):
        def get_closed_orders(self, **kwargs):
            self.calls.append(("closed", kwargs))
            start = datetime.fromisoformat(kwargs["start_time"])
            end = datetime.fromisoformat(kwargs["end_time"])
            return [closed_order("boundary")] * (
                1000 if end - start > timedelta(days=1) else 1
            )

    client = SplittingClient()
    service = AccountActivitySyncService(None, client)  # type: ignore[arg-type]
    rows = service._fetch_closed_orders(START, START + timedelta(days=2))
    assert len(client.calls) == 3
    assert len(rows) == 1


def test_account_activity_private_reads_leave_ten_requests_per_second_spacing() -> None:
    sleeps: list[float] = []
    clock = iter((0.0, 0.04, 0.1))
    service = AccountActivitySyncService(
        None,  # type: ignore[arg-type]
        FakeClient(),
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: next(clock),
        private_request_interval_seconds=0,
    )

    service._pace_private_read()
    service._pace_private_read()

    assert ACCOUNT_ACTIVITY_PRIVATE_REQUEST_INTERVAL_SECONDS == 0.1
    assert service.private_request_interval_seconds == 0.1
    assert sleeps == pytest.approx([0.06])


def test_transfer_pagination_uses_descending_cursor_and_rejects_repetition() -> None:
    client = FakeClient()
    first_page = [deposit(f"deposit-{index}") for index in range(100)]
    client.deposit_pages = {1: first_page, "deposit-99": first_page}
    service = AccountActivitySyncService(None, client)  # type: ignore[arg-type]
    with pytest.raises(AccountActivityCoverageError, match="cursor"):
        service._fetch_transfer_pages(client.get_deposits, START, END)
    assert client.calls[0][1] == {"limit": 100, "order_by": "desc"}
    assert client.calls[1][1] == {
        "limit": 100,
        "order_by": "desc",
        "to": "deposit-99",
    }


def test_transfer_pagination_collects_next_page_and_deduplicates_boundary() -> None:
    client = FakeClient()
    first_page = [deposit(f"deposit-{index}") for index in range(100)]
    client.deposit_pages = {
        1: first_page,
        "deposit-99": [deposit("deposit-99"), deposit("last")],
    }
    service = AccountActivitySyncService(
        None,
        client,
        private_request_interval_seconds=0,  # type: ignore[arg-type]
    )
    rows = service._fetch_transfer_pages(client.get_deposits, START, END)
    assert len(rows) == 101
    assert [call[1].get("to") for call in client.calls] == [None, "deposit-99"]


def test_transfer_cursor_does_not_skip_history_when_new_activity_arrives() -> None:
    class ConcurrentInsertClient(FakeClient):
        def get_deposits(self, **kwargs):
            self.calls.append(("deposits", kwargs))
            if kwargs.get("to") is None:
                return [deposit(f"deposit-{index}") for index in range(100)]
            assert kwargs["to"] == "deposit-99"
            self.new_activity = deposit("new-during-sync")
            return [deposit("deposit-100"), deposit("deposit-101")]

    client = ConcurrentInsertClient()
    service = AccountActivitySyncService(
        None,
        client,
        private_request_interval_seconds=0,  # type: ignore[arg-type]
    )

    rows = service._fetch_transfer_pages(client.get_deposits, START, END)
    row_ids = {row["uuid"] for row in rows}

    assert row_ids == {f"deposit-{index}" for index in range(102)}
    assert "new-during-sync" not in row_ids


def test_permission_failure_is_source_local_and_persisted_as_out_of_scope(
    session_factory,
) -> None:
    client = FakeClient()
    client.closed_rows = [closed_order()]
    client.withdrawal_pages = {1: [withdrawal()]}
    client.deposit_error = UpbitOrderReadError(
        UpbitSafeError(
            error_type="HTTPStatusError",
            operation="get_deposits",
            status_code=401,
            upbit_error_name="out_of_scope",
        )
    )
    with session_factory() as session:
        user = add_user_and_bot_order(session)
        result = AccountActivitySyncService(session, client).run(
            user.id, start_at=START, end_at=END, apply=True
        )
        states = {
            state.source_type: state.sync_status
            for state in session.scalars(select(AccountActivitySyncState))
        }
    assert [source.status for source in result.sources] == [
        "COMPLETE",
        "OUT_OF_SCOPE",
        "COMPLETE",
    ]
    assert states[UPBIT_CLOSED_ORDER] == "COMPLETE"
    assert states[UPBIT_DEPOSIT] == "OUT_OF_SCOPE"
    assert states[UPBIT_WITHDRAWAL] == "COMPLETE"
