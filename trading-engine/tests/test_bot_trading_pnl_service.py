from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, Column, Integer, JSON, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.db.models import (
    BotInventoryLot,
    BotPnlMatch,
    BotSellRealization,
    BotTradingPnlSummary,
    OrderLog,
    User,
)
from crypto_trading_bot.services.bot_trading_pnl_service import BotTradingPnlService


NOW = datetime(2026, 8, 30, tzinfo=UTC)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (
        User,
        OrderLog,
        BotInventoryLot,
        BotSellRealization,
        BotPnlMatch,
        BotTradingPnlSummary,
    ):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    (
                        JSON()
                        if isinstance(column.type, JSONB)
                        else Integer()
                        if isinstance(column.type, BigInteger)
                        else column.type
                    ),
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                    server_default=column.server_default,
                    unique=(
                        column.name
                        in {
                            "source_buy_order_log_id",
                            "source_sell_order_log_id",
                        }
                    ),
                )
                for column in model.__table__.columns
            ),
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def add_user(session, name: str = "Test User") -> User:
    user = User(name=name)
    session.add(user)
    session.flush()
    return user


def add_order(
    session,
    user_id: int,
    side: str,
    quantity: object,
    funds: object,
    fee: object,
    *,
    market: str = "KRW-BTC",
    exchange: str = "UPBIT",
    status: str = "LIVE_DONE",
    trading_mode: str = "LIVE",
    minutes: int = 0,
    request_amount: object = "999999",
    request_quantity: object = "999",
    request_price: object = "777",
) -> OrderLog:
    order = OrderLog(
        recommendation_id=session.query(OrderLog).count() + 1,
        user_id=user_id,
        trading_mode=trading_mode,
        exchange=exchange,
        market=market,
        side=side,
        order_type="MARKET",
        amount_krw=request_amount,
        quantity=request_quantity,
        price=request_price,
        status=status,
        executed_quantity=quantity,
        executed_funds_krw=funds,
        paid_fee=fee,
        created_at=NOW + timedelta(minutes=minutes),
        updated_at=NOW + timedelta(minutes=minutes),
        execution_synced_at=NOW + timedelta(minutes=minutes),
    )
    session.add(order)
    session.flush()
    return order


def calculate(session, user_id: int):
    return BotTradingPnlService(session, now_fn=lambda: NOW).calculate(user_id)


def test_basic_buy_sell_uses_actual_execution_and_fees(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "1", "100000", "50")
        add_order(session, user.id, "SELL", "1", "110000", "55", minutes=1)

        result = calculate(session, user.id)

    lot = result.lots[0]
    sell = result.sells[0]
    assert lot.original_cost_basis_krw == Decimal("100050.0000000000")
    assert lot.remaining_quantity == 0
    assert lot.remaining_cost_basis_krw == 0
    assert sell.status == "FULLY_MATCHED"
    assert sell.recognized_net_proceeds_krw == Decimal("109945.0000000000")
    assert sell.recognized_realized_pnl_krw == Decimal("9895.0000000000")
    assert result.summary.total_fees_krw == Decimal("105.0000000000")
    assert result.summary.winning_sell_count == 1
    assert result.summary.win_rate_percentage == Decimal("100.0000000000")


def test_partial_sell_preserves_quantity_and_cost_residual(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "1", "100000", "50")
        add_order(session, user.id, "SELL", "0.4", "44000", "22", minutes=1)
        result = calculate(session, user.id)

    assert result.lots[0].remaining_quantity == Decimal("0.6")
    assert result.lots[0].remaining_cost_basis_krw == Decimal("60030.0000000000")
    assert result.sells[0].recognized_cost_basis_krw == Decimal("40020.0000000000")
    assert result.sells[0].recognized_realized_pnl_krw == Decimal("3958.0000000000")


def test_fifo_spans_multiple_buy_lots_and_preserves_lineage(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        first = add_order(session, user.id, "BUY", "1", "100", "1")
        second = add_order(session, user.id, "BUY", "2", "300", "3", minutes=1)
        add_order(session, user.id, "SELL", "2.5", "500", "5", minutes=2)
        result = calculate(session, user.id)

    assert [match.source_buy_order_log_id for match in result.sells[0].matches] == [
        first.id,
        second.id,
    ]
    assert [match.matched_quantity for match in result.sells[0].matches] == [
        Decimal("1"),
        Decimal("1.5"),
    ]
    assert result.lots[1].remaining_quantity == Decimal("0.5")
    assert (
        sum(
            (match.allocated_sell_fee_krw for match in result.sells[0].matches),
            Decimal("0"),
        )
        == result.sells[0].recognized_sell_fee_krw
    )


def test_sell_without_bot_inventory_is_unmatched_not_profit(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "SELL", "0.5", "1000", "1")
        result = calculate(session, user.id)

    sell = result.sells[0]
    assert sell.status == "UNMATCHED"
    assert sell.matched_quantity == 0
    assert sell.unmatched_quantity == Decimal("0.5")
    assert sell.recognized_cost_basis_krw is None
    assert sell.recognized_realized_pnl_krw is None
    assert result.summary.recognized_realized_pnl_krw == 0
    assert result.summary.accounting_status == "PARTIAL"


def test_partial_match_recognizes_only_bot_attributed_fraction(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "0.3", "300", "3")
        add_order(session, user.id, "SELL", "0.5", "600", "6", minutes=1)
        result = calculate(session, user.id)

    sell = result.sells[0]
    assert sell.status == "PARTIALLY_MATCHED"
    assert sell.matched_quantity == Decimal("0.3")
    assert sell.unmatched_quantity == Decimal("0.2")
    assert sell.recognized_gross_proceeds_krw == Decimal("360.0000000000")
    assert sell.recognized_sell_fee_krw == Decimal("3.6000000000")
    assert sell.recognized_realized_pnl_krw == Decimal("53.4000000000")
    assert result.summary.win_rate_percentage is None


def test_future_buy_never_matches_past_sell(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "SELL", "1", "120", "1")
        add_order(session, user.id, "BUY", "1", "100", "1", minutes=1)
        result = calculate(session, user.id)

    assert result.sells[0].status == "UNMATCHED"
    assert result.lots[0].remaining_quantity == 1


@pytest.mark.parametrize(
    ("status", "trading_mode", "included"),
    [
        ("LIVE_DONE", "LIVE", True),
        ("LIVE_EXECUTED_CANCELLED", "LIVE", True),
        ("LIVE_CANCELLED", "LIVE", False),
        ("LIVE_WAIT", "LIVE", False),
        ("LIVE_PLACED", "LIVE", False),
        ("LIVE_UNKNOWN", "LIVE", False),
        ("LIVE_FAILED", "LIVE", False),
        ("LIVE_DONE", "MOCK", False),
    ],
)
def test_only_terminal_live_execution_statuses_are_sources(
    session_factory, status, trading_mode, included
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(
            session,
            user.id,
            "BUY",
            "1",
            "100",
            "0",
            status=status,
            trading_mode=trading_mode,
        )
        result = calculate(session, user.id)
    assert result.summary.source_order_count == int(included)


@pytest.mark.parametrize("fee", [None, "-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_or_unknown_fee_is_incomplete(session_factory, fee) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "1", "100", fee)
        result = calculate(session, user.id)
    assert result.summary.processed_order_count == 0
    assert result.summary.incomplete_order_count == 1
    assert result.summary.accounting_status == "PARTIAL"


def test_zero_fee_is_valid_and_request_values_are_ignored(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(
            session,
            user.id,
            "BUY",
            "2",
            "100",
            "0",
            request_amount="1",
            request_quantity="900",
            request_price="800",
        )
        result = calculate(session, user.id)
    assert result.lots[0].original_cost_basis_krw == Decimal("100.0000000000")
    assert result.lots[0].acquired_quantity == 2


def test_invalid_source_blocks_only_later_orders_in_same_market(
    session_factory,
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "1", "100", None, market="KRW-BTC")
        add_order(session, user.id, "BUY", "1", "100", "1", market="KRW-BTC", minutes=1)
        add_order(session, user.id, "BUY", "2", "200", "2", market="KRW-ETH", minutes=2)
        result = calculate(session, user.id)
    assert result.summary.incomplete_order_count == 2
    assert [lot.market for lot in result.lots] == ["KRW-ETH"]


def test_user_exchange_and_market_inventory_are_isolated(session_factory) -> None:
    with session_factory() as session:
        first = add_user(session, "first")
        second = add_user(session, "second")
        add_order(session, first.id, "BUY", "1", "100", "1", market="KRW-BTC")
        add_order(session, first.id, "SELL", "1", "200", "1", market="KRW-ETH")
        add_order(session, second.id, "BUY", "1", "50", "1", market="KRW-ETH")
        result = calculate(session, first.id)
    assert result.sells[0].status == "UNMATCHED"
    assert result.lots[0].remaining_quantity == 1


def test_win_rate_uses_only_fully_matched_sells(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "3", "300", "0")
        add_order(session, user.id, "SELL", "1", "120", "0", minutes=1)
        add_order(session, user.id, "SELL", "1", "80", "0", minutes=2)
        add_order(session, user.id, "SELL", "1", "100", "0", minutes=3)
        add_order(session, user.id, "SELL", "1", "200", "0", minutes=4)
        result = calculate(session, user.id)
    assert result.summary.winning_sell_count == 1
    assert result.summary.losing_sell_count == 1
    assert result.summary.breakeven_sell_count == 1
    assert result.summary.unmatched_sell_count == 1
    assert result.summary.win_rate_percentage == Decimal("33.3333333333")


def test_apply_is_idempotent_and_source_update_rebuilds(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        buy = add_order(session, user.id, "BUY", "1", "100", "1")
        add_order(session, user.id, "SELL", "1", "120", "1", minutes=1)
        service = BotTradingPnlService(session, now_fn=lambda: NOW)
        service.rebuild(user.id, apply=True)
        session.commit()
        first_signature = session.query(BotTradingPnlSummary).one().source_signature
        assert service.source_changed(user.id) is False

        service.rebuild(user.id, apply=True)
        session.commit()
        assert session.query(BotInventoryLot).count() == 1
        assert session.query(BotSellRealization).count() == 1
        assert session.query(BotPnlMatch).count() == 1
        assert session.query(BotTradingPnlSummary).count() == 1

        buy.executed_funds_krw = Decimal("110")
        buy.updated_at = NOW + timedelta(hours=1)
        session.commit()
        assert service.source_changed(user.id) is True
        service.rebuild(user.id, apply=True)
        session.commit()
        summary = session.query(BotTradingPnlSummary).one()
        assert summary.source_signature != first_signature
        assert summary.recognized_realized_pnl_krw == Decimal("8")


def test_dry_run_does_not_write_or_flush(session_factory, monkeypatch) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, "BUY", "1", "100", "1")
        session.commit()
        flush_calls = 0
        original_flush = session.flush

        def tracked_flush(*args, **kwargs):
            nonlocal flush_calls
            flush_calls += 1
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", tracked_flush)
        result = BotTradingPnlService(session).rebuild(user.id, apply=False)
        assert result.summary.processed_buy_order_count == 1
        assert flush_calls == 0
        assert session.query(BotInventoryLot).count() == 0
        assert session.query(BotTradingPnlSummary).count() == 0


def test_caller_rollback_restores_previous_accounting(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        buy = add_order(session, user.id, "BUY", "1", "100", "1")
        service = BotTradingPnlService(session, now_fn=lambda: NOW)
        service.rebuild(user.id, apply=True)
        session.commit()
        buy.executed_funds_krw = Decimal("200")
        buy.updated_at = NOW + timedelta(hours=1)
        session.commit()
        service.rebuild(user.id, apply=True)
        session.rollback()
        assert session.query(BotInventoryLot).one().gross_buy_funds_krw == Decimal(
            "100"
        )
