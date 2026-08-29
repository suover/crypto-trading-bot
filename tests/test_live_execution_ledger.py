from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import (
    BigInteger,
    Column,
    JSON,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.db.models import OrderFill, OrderLog
from crypto_trading_bot.services.live_execution_ledger_backfill_service import (
    LiveExecutionLedgerBackfillService,
)
from crypto_trading_bot.services.live_execution_ledger_service import (
    LiveExecutionLedgerService,
    UpbitLiveExecutionNormalizer,
)


@compiles(BigInteger, "sqlite")
def compile_big_integer_as_sqlite_integer(type_, compiler, **kwargs) -> str:
    return "INTEGER"


def trade(
    trade_id: str | None, *, price: str, volume: str, funds: str, side: str = "bid"
) -> dict[str, str | None]:
    return {
        "uuid": trade_id,
        "price": price,
        "volume": volume,
        "funds": funds,
        "side": side,
    }


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (OrderLog, OrderFill):
        constraints = (
            [UniqueConstraint("order_log_id", "exchange_trade_id")]
            if model is OrderFill
            else []
        )
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
            *constraints,
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def make_order(
    order_id: int = 1, *, mode: str = "LIVE", exchange: str = "UPBIT", raw_response=None
) -> OrderLog:
    return OrderLog(
        id=order_id,
        recommendation_id=order_id,
        user_id=1,
        trading_mode=mode,
        exchange=exchange,
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        amount_krw=Decimal("5000"),
        quantity=None,
        price=None,
        status="LIVE_WAIT",
        raw_response=raw_response or {},
    )


def test_complete_execution_summary_uses_decimal_values() -> None:
    normalized = UpbitLiveExecutionNormalizer.normalize(
        {
            "state": "done",
            "executed_volume": "0.00123",
            "executed_funds": "123456.789",
            "paid_fee": "61.7283945",
            "remaining_volume": "0",
            "trades_count": "2",
            "trades": [],
            "price": "999999999",
        }
    )
    assert normalized.executed_quantity == Decimal("0.00123")
    assert normalized.executed_funds_krw == Decimal("123456.789")
    assert normalized.average_execution_price == Decimal("123456.789") / Decimal(
        "0.00123"
    )
    assert normalized.paid_fee == Decimal("61.7283945")
    assert normalized.remaining_quantity == Decimal("0")
    assert normalized.trades_count == 2


def test_multiple_fills_use_total_funds_over_total_quantity() -> None:
    normalized = UpbitLiveExecutionNormalizer.normalize(
        {
            "executed_volume": "10",
            "trades": [
                trade("A", price="100", volume="1", funds="100"),
                trade("B", price="200", volume="9", funds="1800"),
            ],
        }
    )
    assert normalized.executed_funds_krw == Decimal("1900")
    assert normalized.average_execution_price == Decimal("190")
    assert normalized.average_execution_price != Decimal("150")
    assert len(normalized.fills) == 2


def test_valid_executed_funds_has_priority_and_invalid_value_falls_back() -> None:
    response = {
        "executed_volume": "2",
        "executed_funds": "500",
        "trades": [trade("A", price="100", volume="2", funds="200")],
    }
    assert UpbitLiveExecutionNormalizer.normalize(
        response
    ).executed_funds_krw == Decimal("500")
    response["executed_funds"] = "NaN"
    assert UpbitLiveExecutionNormalizer.normalize(
        response
    ).executed_funds_krw == Decimal("200")


def test_missing_funds_is_not_estimated_from_request_price_or_ticker() -> None:
    normalized = UpbitLiveExecutionNormalizer.normalize(
        {"executed_volume": "1", "price": "999", "requested_amount": "5000"}
    )
    assert normalized.executed_funds_krw is None
    assert normalized.average_execution_price is None


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "abc", "", "-1", None, True]
)
def test_malformed_financial_values_are_not_normalized(value: object) -> None:
    normalized = UpbitLiveExecutionNormalizer.normalize(
        {
            "executed_volume": value,
            "executed_funds": value,
            "paid_fee": value,
            "remaining_volume": value,
        }
    )
    assert normalized.executed_quantity is None
    assert normalized.executed_funds_krw is None
    assert normalized.average_execution_price is None
    assert normalized.paid_fee is None
    assert normalized.remaining_quantity is None


def test_zero_and_partial_cancel_execution_values_are_preserved() -> None:
    zero = UpbitLiveExecutionNormalizer.normalize(
        {"state": "cancel", "executed_volume": "0"}
    )
    assert zero.executed_quantity == 0
    assert zero.executed_funds_krw is None
    assert zero.paid_fee is None
    assert zero.average_execution_price is None
    partial = UpbitLiveExecutionNormalizer.normalize(
        {
            "state": "cancel",
            "executed_volume": "4",
            "executed_funds": "600",
            "paid_fee": "0.3",
            "remaining_volume": "6",
        }
    )
    assert partial.executed_quantity == 4
    assert partial.executed_funds_krw == 600
    assert partial.average_execution_price == 150
    assert partial.paid_fee == Decimal("0.3")


def test_invalid_or_missing_trade_uuid_is_not_invented_but_valid_funds_contributes() -> (
    None
):
    normalized = UpbitLiveExecutionNormalizer.normalize(
        {
            "executed_volume": "2",
            "trades": [
                trade(None, price="100", volume="1", funds="100"),
                trade("", price="100", volume="1", funds="100"),
                trade("valid", price="bad", volume="1", funds="100"),
            ],
        }
    )
    assert normalized.executed_funds_krw == Decimal("300")
    assert normalized.fills == ()


def test_fill_ledger_is_idempotent_and_adds_only_new_trade(session_factory) -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    with session_factory() as session:
        order = make_order()
        session.add(order)
        session.commit()
        service = LiveExecutionLedgerService(session, now_fn=lambda: now)
        first = {
            "executed_volume": "2",
            "trades": [
                trade("A", price="100", volume="1", funds="100"),
                trade("B", price="200", volume="1", funds="200"),
                trade("A", price="100", volume="1", funds="100"),
            ],
        }
        assert service.sync(order, first).inserted_fill_count == 2
        session.commit()
        assert service.sync(order, first).inserted_fill_count == 0
        session.commit()
        second = {
            "executed_volume": "3",
            "trades": [
                trade("A", price="100", volume="1", funds="100"),
                trade("B", price="200", volume="1", funds="200"),
                trade("C", price="300", volume="1", funds="300"),
            ],
        }
        assert service.sync(order, second).inserted_fill_count == 1
        session.commit()
        assert session.scalar(select(func.count(OrderFill.id))) == 3
        assert set(session.scalars(select(OrderFill.exchange_trade_id))) == {
            "A",
            "B",
            "C",
        }
        assert order.amount_krw == Decimal("5000")
        assert order.quantity is None
        assert order.price is None
        assert order.executed_quantity == 3
        assert order.executed_funds_krw == 600
        assert order.average_execution_price == 200
        assert order.execution_synced_at == now


def test_database_unique_constraint_rejects_duplicate_fill(session_factory) -> None:
    with session_factory() as session:
        session.add(make_order())
        session.commit()
        values = dict(
            order_log_id=1,
            exchange_trade_id="A",
            price=Decimal("1"),
            volume=Decimal("1"),
            funds_krw=Decimal("1"),
            raw_data={},
        )
        session.add_all([OrderFill(**values), OrderFill(**values)])
        with pytest.raises(IntegrityError):
            session.commit()


def test_incomplete_later_response_does_not_erase_good_values(session_factory) -> None:
    with session_factory() as session:
        order = make_order()
        session.add(order)
        session.commit()
        service = LiveExecutionLedgerService(session)
        service.sync(
            order, {"executed_volume": "2", "executed_funds": "200", "paid_fee": "1"}
        )
        session.commit()
        service.sync(
            order,
            {
                "executed_volume": "NaN",
                "executed_funds": "bad",
                "paid_fee": "2",
                "remaining_volume": "0",
                "trades_count": 2,
            },
        )
        session.commit()
        assert order.executed_quantity == 2
        assert order.executed_funds_krw == 200
        assert order.average_execution_price == 100
        assert order.paid_fee == 2
        assert order.remaining_quantity == 0
        assert order.trades_count == 2


def test_execution_pair_is_updated_only_from_one_coherent_response(
    session_factory,
) -> None:
    with session_factory() as session:
        order = make_order()
        session.add(order)
        session.commit()
        service = LiveExecutionLedgerService(session)

        service.sync(order, {"executed_volume": "1", "executed_funds": "100"})
        assert (
            order.executed_quantity,
            order.executed_funds_krw,
            order.average_execution_price,
        ) == (Decimal("1"), Decimal("100"), Decimal("100"))

        service.sync(order, {"executed_volume": "2"})
        assert (
            order.executed_quantity,
            order.executed_funds_krw,
            order.average_execution_price,
        ) == (Decimal("1"), Decimal("100"), Decimal("100"))

        service.sync(order, {"executed_funds": "200"})
        assert (
            order.executed_quantity,
            order.executed_funds_krw,
            order.average_execution_price,
        ) == (Decimal("1"), Decimal("100"), Decimal("100"))

        service.sync(order, {"executed_volume": "2", "executed_funds": "300"})
        assert (
            order.executed_quantity,
            order.executed_funds_krw,
            order.average_execution_price,
        ) == (Decimal("2"), Decimal("300"), Decimal("150"))


def test_zero_execution_does_not_regress_existing_positive_summary(
    session_factory,
) -> None:
    with session_factory() as session:
        positive_order = make_order()
        empty_order = make_order(2)
        session.add_all([positive_order, empty_order])
        session.commit()
        service = LiveExecutionLedgerService(session)

        service.sync(positive_order, {"executed_volume": "1", "executed_funds": "100"})
        service.sync(positive_order, {"executed_volume": "0"})
        assert (
            positive_order.executed_quantity,
            positive_order.executed_funds_krw,
            positive_order.average_execution_price,
        ) == (Decimal("1"), Decimal("100"), Decimal("100"))

        service.sync(empty_order, {"executed_volume": "0"})
        assert empty_order.executed_quantity == 0
        assert empty_order.executed_funds_krw is None
        assert empty_order.average_execution_price is None


def test_backfill_defaults_to_dry_run_then_applies_idempotently(
    session_factory,
) -> None:
    response = {
        "executed_volume": "2",
        "executed_funds": "300",
        "paid_fee": "0.15",
        "trades": [
            trade("A", price="100", volume="1", funds="100"),
            trade("B", price="200", volume="1", funds="200"),
        ],
    }
    with session_factory() as session:
        session.add(
            make_order(
                raw_response={
                    "identifier": "recommendation-1",
                    "create_response": {"uuid": "order"},
                    "order_status_response": response,
                    "reconciliation_source": "WORKER",
                    "reconciled_at": "kept",
                }
            )
        )
        session.add(
            make_order(2, mode="MOCK", raw_response={"order_status_response": response})
        )
        session.add(
            make_order(
                3, exchange="BINANCE", raw_response={"order_status_response": response}
            )
        )
        session.add(make_order(4, raw_response={"create_response": response}))
        session.commit()
    service = LiveExecutionLedgerBackfillService(session_factory)
    dry_run = service.run()
    assert (
        dry_run.processed_count,
        dry_run.eligible_count,
        dry_run.applied_count,
        dry_run.skipped_count,
        dry_run.error_count,
    ) == (2, 1, 0, 1, 0)
    with session_factory() as session:
        assert session.get(OrderLog, 1).executed_quantity is None
        assert session.scalar(select(func.count(OrderFill.id))) == 0
    applied = service.run(apply=True)
    assert applied.applied_count == 1
    repeated = service.run(apply=True)
    assert repeated.applied_count == 1
    with session_factory() as session:
        order = session.get(OrderLog, 1)
        assert order.executed_quantity == 2
        assert order.executed_funds_krw == 300
        assert session.scalar(select(func.count(OrderFill.id))) == 2
        assert order.raw_response["identifier"] == "recommendation-1"
        assert order.raw_response["create_response"] == {"uuid": "order"}
        assert order.raw_response["reconciliation_source"] == "WORKER"
        assert order.raw_response["reconciled_at"] == "kept"


def test_backfill_dry_run_uses_unlocked_read_without_transaction_writes() -> None:
    order = make_order(
        raw_response={
            "order_status_response": {
                "executed_volume": "1",
                "executed_funds": "100",
            }
        }
    )

    class TrackingSession:
        def __init__(self, *, ids=(), selected_order=None) -> None:
            self.ids = ids
            self.selected_order = selected_order
            self.scalar_statements = []
            self.commit_count = 0
            self.rollback_count = 0
            self.flush_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            pass

        def scalars(self, statement):
            return self.ids

        def scalar(self, statement):
            self.scalar_statements.append(statement)
            return self.selected_order

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

        def flush(self) -> None:
            self.flush_count += 1

    listing_session = TrackingSession(ids=(1,))
    row_session = TrackingSession(selected_order=order)
    sessions = iter((listing_session, row_session))
    service = LiveExecutionLedgerBackfillService(lambda: next(sessions))  # type: ignore[arg-type]

    summary = service.run()

    assert summary.processed_count == 1
    assert summary.eligible_count == 1
    assert summary.applied_count == 0
    assert summary.normalized_fill_count == 0
    assert order.executed_quantity is None
    assert order.executed_funds_krw is None
    assert order.average_execution_price is None
    assert order.execution_synced_at is None
    assert row_session.commit_count == 0
    assert row_session.rollback_count == 0
    assert row_session.flush_count == 0
    (row_statement,) = row_session.scalar_statements
    compiled = str(row_statement.compile(dialect=dialect())).upper()
    assert "FOR UPDATE" not in compiled
