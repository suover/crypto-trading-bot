from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
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

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    AccountSnapshot,
    AnalysisRun,
    MarketSnapshot,
    MarketUniverseCandidate,
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
    User,
)
from crypto_trading_bot.services.account_snapshot_service import AccountSnapshotService
from crypto_trading_bot.services.portfolio_valuation_service import (
    PortfolioValuationService,
)


PIPELINE_A = "11111111-1111-1111-1111-111111111111"
PIPELINE_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    models = (
        User,
        AnalysisRun,
        AccountSnapshot,
        MarketSnapshot,
        MarketUniverseCandidate,
        PortfolioSnapshot,
        PortfolioPositionSnapshot,
    )
    for model in models:
        constraints = []
        if model is PortfolioSnapshot:
            constraints.append(
                UniqueConstraint("user_id", "exchange", "pipeline_run_id")
            )
        elif model is PortfolioPositionSnapshot:
            constraints.append(UniqueConstraint("portfolio_snapshot_id", "currency"))
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
                    unique=column.unique,
                )
                for column in model.__table__.columns
            ),
            *constraints,
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost/test",
        database_password="",
        database_password_file="",
        trading_mode="AI_APPROVAL",
    )


def add_user(session) -> User:
    user = User(name="Test User")
    session.add(user)
    session.commit()
    return user


def add_account_source(
    session,
    user: User,
    pipeline_id: str,
    rows: list[dict[str, object]],
    *,
    run_type: str = "ACCOUNT_SNAPSHOT",
) -> AnalysisRun:
    run = AnalysisRun(
        user_id=user.id,
        pipeline_run_id=pipeline_id,
        run_type=run_type,
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            AccountSnapshot(
                analysis_run_id=run.id,
                user_id=user.id,
                exchange="UPBIT",
                currency=str(row["currency"]),
                balance=row.get("balance"),
                locked=row.get("locked"),
                avg_buy_price=row.get("avg_buy_price"),
                raw_data={},
            )
            for row in rows
        ]
    )
    session.commit()
    return run


def add_universe_source(
    session,
    user: User,
    pipeline_id: str,
    markets: list[dict[str, object]],
) -> AnalysisRun:
    run = AnalysisRun(
        user_id=user.id,
        pipeline_run_id=pipeline_id,
        run_type="MARKET_UNIVERSE",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    session.add(run)
    session.flush()
    for rank, values in enumerate(markets, start=1):
        market = str(values["market"])
        base_asset = str(values["base_asset"])
        trading_supported = bool(values.get("trading_supported", True))
        session.add(
            MarketUniverseCandidate(
                analysis_run_id=run.id,
                user_id=user.id,
                exchange="UPBIT",
                market=market,
                base_asset=base_asset,
                quote_asset="KRW",
                rank=rank,
                selection_source="HELD",
                buy_eligible=False,
                sell_eligible=trading_supported,
                market_event_data={"raw": {"trading_supported": trading_supported}},
                feature_data={"trading_supported": trading_supported},
            )
        )
        if "price" in values:
            session.add(
                MarketSnapshot(
                    analysis_run_id=run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=market,
                    current_price=values["price"],
                    raw_data={},
                )
            )
    session.commit()
    return run


def build_service(
    session, settings: Settings, *, coingecko_client=None
) -> PortfolioValuationService:
    return PortfolioValuationService(
        session,
        settings=settings,
        coingecko_client=coingecko_client,
        now_fn=lambda: datetime(2026, 8, 30, tzinfo=UTC),
    )


def test_complete_portfolio_uses_available_and_locked_balances(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000000", "locked": "100000"},
                {
                    "currency": "BTC",
                    "balance": "0.001",
                    "locked": "0.001",
                    "avg_buy_price": "100000000",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "120000000"}],
        )

        result = build_service(session, settings).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )
        snapshot = result.portfolio_snapshot
        (position,) = result.positions

        assert snapshot.cash_total_krw == Decimal("1100000")
        assert snapshot.priced_positions_value_krw == Decimal("240000")
        assert snapshot.known_total_value_krw == Decimal("1340000")
        assert snapshot.total_value_krw == Decimal("1340000")
        assert snapshot.positions_estimated_cost_basis_krw == Decimal("200000")
        assert snapshot.unrealized_pnl_krw == Decimal("40000")
        assert snapshot.unrealized_pnl_percentage == Decimal("20")
        assert snapshot.valuation_status == "COMPLETE"
        assert position.total_quantity == Decimal("0.002")
        assert position.market_value_krw == Decimal("240000")
        assert position.estimated_cost_basis_krw == Decimal("200000")
        assert position.unrealized_pnl_krw == Decimal("40000")
        assert position.unrealized_pnl_percentage == Decimal("20")
        assert position.account_snapshot_id is not None
        assert position.market_snapshot_id is not None


def test_cash_only_portfolio_is_complete_with_zero_position_pnl(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [{"currency": "KRW", "balance": "1000", "locked": "50"}],
        )
        add_universe_source(session, user, PIPELINE_A, [])

        snapshot = (
            build_service(session, settings)
            .capture(user.id, pipeline_run_id=PIPELINE_A)
            .portfolio_snapshot
        )

        assert snapshot.position_count == 0
        assert snapshot.priced_positions_value_krw == 0
        assert snapshot.known_total_value_krw == 1050
        assert snapshot.total_value_krw == 1050
        assert snapshot.positions_estimated_cost_basis_krw == 0
        assert snapshot.unrealized_pnl_krw == 0
        assert snapshot.unrealized_pnl_percentage is None
        assert snapshot.unpriced_asset_count == 0
        assert snapshot.valuation_status == "COMPLETE"


def test_unpriced_holding_is_preserved_and_total_is_null(
    session_factory, settings
) -> None:
    coingecko = MagicMock()
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000000", "locked": "0"},
                {
                    "currency": "ABC",
                    "balance": "10",
                    "locked": "0",
                    "avg_buy_price": "50",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-ABC", "base_asset": "ABC"}],
        )

        result = build_service(session, settings, coingecko_client=coingecko).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )
        snapshot = result.portfolio_snapshot
        (position,) = result.positions

        assert position.currency == "ABC"
        assert position.mark_price is None
        assert position.market_value_krw is None
        assert position.valuation_status == "UNPRICED"
        assert snapshot.known_total_value_krw == 1000000
        assert snapshot.total_value_krw is None
        assert snapshot.unpriced_asset_count == 1
        assert snapshot.valuation_status == "PARTIAL"
        coingecko.get_markets.assert_not_called()


def test_explicit_coingecko_fallback_prices_unlisted_assets_and_keeps_upbit_priority(
    session_factory,
) -> None:
    configured = Settings(
        database_url="postgresql://test:test@localhost/test",
        portfolio_coingecko_asset_mapping=(
            "BTC=must-not-be-requested,APENFT=apenft,QI=qiswap"
        ),
    )
    coingecko = MagicMock()
    coingecko.get_markets.return_value = [
        {"id": "apenft", "current_price": "0.5"},
        {"id": "qiswap", "current_price": "2"},
        {"id": "benqi", "current_price": "999999"},
    ]
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "100", "locked": "0"},
                {
                    "currency": "BTC",
                    "balance": "1",
                    "locked": "0",
                    "avg_buy_price": "80",
                },
                {
                    "currency": "APENFT",
                    "balance": "10",
                    "locked": "0",
                    "avg_buy_price": "0.1",
                },
                {
                    "currency": "QI",
                    "balance": "5",
                    "locked": "0",
                    "avg_buy_price": "1",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )

        result = build_service(session, configured, coingecko_client=coingecko).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        positions = {position.currency: position for position in result.positions}
        coingecko.get_markets.assert_called_once_with(
            ["apenft", "qiswap"], vs_currency="krw"
        )
        assert positions["BTC"].price_source == "MARKET_SNAPSHOT"
        assert positions["BTC"].mark_price == 100
        assert positions["APENFT"].price_source == "COINGECKO"
        assert positions["APENFT"].market_value_krw == Decimal("5.0")
        assert positions["QI"].price_source == "COINGECKO"
        assert positions["QI"].mark_price == 2
        assert positions["QI"].market is None
        assert result.portfolio_snapshot.priced_positions_value_krw == Decimal("115.0")
        assert result.portfolio_snapshot.total_value_krw == Decimal("215.0")
        assert result.portfolio_snapshot.valuation_status == "COMPLETE"


def test_excluded_assets_remain_auditable_but_do_not_affect_nav_or_coingecko(
    session_factory,
) -> None:
    configured = Settings(
        database_url="postgresql://test:test@localhost/test",
        portfolio_excluded_assets=" qi, APENFT,qi ",
        portfolio_coingecko_asset_mapping=("QI=qiswap,APENFT=apenft,ABC=abc-token"),
    )
    coingecko = MagicMock()
    coingecko.get_markets.return_value = [{"id": "abc-token", "current_price": "10"}]
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "100", "locked": "0"},
                {
                    "currency": "BTC",
                    "balance": "1",
                    "locked": "0",
                    "avg_buy_price": "80",
                },
                {
                    "currency": "QI",
                    "balance": "2",
                    "locked": "0",
                    "avg_buy_price": "0",
                },
                {
                    "currency": "APENFT",
                    "balance": "3",
                    "locked": "0",
                    "avg_buy_price": "0",
                },
                {
                    "currency": "ABC",
                    "balance": "4",
                    "locked": "0",
                    "avg_buy_price": "1",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [
                {"market": "KRW-BTC", "base_asset": "BTC", "price": "100"},
                {"market": "KRW-QI", "base_asset": "QI", "price": "999"},
                {"market": "KRW-APENFT", "base_asset": "APENFT"},
            ],
        )

        result = build_service(session, configured, coingecko_client=coingecko).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        positions = {position.currency: position for position in result.positions}
        coingecko.get_markets.assert_called_once_with(["abc-token"], vs_currency="krw")
        for asset in ("QI", "APENFT"):
            position = positions[asset]
            assert position.valuation_status == "EXCLUDED"
            assert position.price_source == "POLICY_EXCLUDED"
            assert position.mark_price is None
            assert position.market_value_krw is None
            assert position.estimated_cost_basis_krw is None
            assert position.unrealized_pnl_krw is None
            assert position.unrealized_pnl_percentage is None
        assert positions["QI"].market == "KRW-QI"
        assert positions["QI"].market_snapshot_id is None
        assert positions["ABC"].price_source == "COINGECKO"
        assert positions["ABC"].market_value_krw == 40
        assert result.portfolio_snapshot.position_count == 4
        assert result.portfolio_snapshot.unpriced_asset_count == 0
        assert result.portfolio_snapshot.missing_cost_basis_count == 0
        assert result.portfolio_snapshot.priced_positions_value_krw == 140
        assert result.portfolio_snapshot.known_total_value_krw == 240
        assert result.portfolio_snapshot.total_value_krw == 240
        assert result.portfolio_snapshot.positions_estimated_cost_basis_krw == 84
        assert result.portfolio_snapshot.unrealized_pnl_krw == 56
        assert result.portfolio_snapshot.valuation_status == "COMPLETE"
        assert result.portfolio_snapshot.valuation_policy_signature == (
            configured.portfolio_valuation_policy_signature
        )
        source_qi = session.scalar(
            select(AccountSnapshot).where(AccountSnapshot.currency == "QI")
        )
        assert source_qi is not None
        assert source_qi.balance == 2


def test_excluded_only_fallback_candidates_do_not_call_coingecko(
    session_factory,
) -> None:
    configured = Settings(
        database_url="postgresql://test:test@localhost/test",
        portfolio_excluded_assets="QI",
        portfolio_coingecko_asset_mapping="QI=qiswap",
    )
    coingecko = MagicMock()
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "100", "locked": "0"},
                {"currency": "QI", "balance": "2", "locked": "0"},
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-QI", "base_asset": "QI", "price": "999"}],
        )

        result = build_service(session, configured, coingecko_client=coingecko).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        coingecko.get_markets.assert_not_called()
        assert result.positions[0].valuation_status == "EXCLUDED"
        assert result.portfolio_snapshot.total_value_krw == 100
        assert result.portfolio_snapshot.valuation_status == "COMPLETE"


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ([], None),
        ([{"id": "apenft", "current_price": "NaN"}], None),
        (None, httpx.ConnectError("offline")),
    ],
)
def test_coingecko_missing_invalid_or_error_remains_unpriced(
    session_factory, response, error
) -> None:
    configured = Settings(
        database_url="postgresql://test:test@localhost/test",
        portfolio_coingecko_asset_mapping="APENFT=apenft",
    )
    coingecko = MagicMock()
    if error is None:
        coingecko.get_markets.return_value = response
    else:
        coingecko.get_markets.side_effect = error
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "100", "locked": "0"},
                {"currency": "APENFT", "balance": "10", "locked": "0"},
            ],
        )
        add_universe_source(session, user, PIPELINE_A, [])

        result = build_service(session, configured, coingecko_client=coingecko).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        assert result.positions[0].valuation_status == "UNPRICED"
        assert result.positions[0].price_source == "UNAVAILABLE"
        assert result.portfolio_snapshot.valuation_status == "PARTIAL"
        assert result.portfolio_snapshot.total_value_krw is None


def test_missing_cost_basis_does_not_make_complete_valuation_partial(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {
                    "currency": "BTC",
                    "balance": "2",
                    "locked": "0",
                    "avg_buy_price": "0",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )

        snapshot = (
            build_service(session, settings)
            .capture(user.id, pipeline_run_id=PIPELINE_A)
            .portfolio_snapshot
        )

        assert snapshot.valuation_status == "COMPLETE"
        assert snapshot.total_value_krw == 1200
        assert snapshot.missing_cost_basis_count == 1
        assert snapshot.positions_estimated_cost_basis_krw is None
        assert snapshot.unrealized_pnl_krw is None
        assert snapshot.unrealized_pnl_percentage is None


def test_same_pipeline_price_is_used_and_newer_pipeline_is_ignored(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        account_rows = [
            {"currency": "KRW", "balance": "1000", "locked": "0"},
            {
                "currency": "BTC",
                "balance": "1",
                "locked": "0",
                "avg_buy_price": "80",
            },
        ]
        add_account_source(session, user, PIPELINE_A, account_rows)
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )
        add_account_source(session, user, PIPELINE_B, account_rows)
        add_universe_source(
            session,
            user,
            PIPELINE_B,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "999"}],
        )

        position = (
            build_service(session, settings)
            .capture(user.id, pipeline_run_id=PIPELINE_A)
            .positions[0]
        )

        assert position.mark_price == 100
        assert position.market_value_krw == 100


def test_missing_same_pipeline_price_does_not_fall_back_to_other_pipeline(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        account_rows = [
            {"currency": "KRW", "balance": "1000", "locked": "0"},
            {
                "currency": "BTC",
                "balance": "1",
                "locked": "0",
                "avg_buy_price": "80",
            },
        ]
        add_account_source(session, user, PIPELINE_A, account_rows)
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC"}],
        )
        add_account_source(session, user, PIPELINE_B, account_rows)
        add_universe_source(
            session,
            user,
            PIPELINE_B,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "999"}],
        )

        result = build_service(session, settings).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        assert result.positions[0].mark_price is None
        assert result.portfolio_snapshot.valuation_status == "PARTIAL"
        assert result.portfolio_snapshot.total_value_krw is None


def test_trading_unsupported_holding_with_persisted_ticker_is_still_valued(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {
                    "currency": "DELISTED",
                    "balance": "2",
                    "locked": "0",
                    "avg_buy_price": "10",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [
                {
                    "market": "KRW-DELISTED",
                    "base_asset": "DELISTED",
                    "price": "100",
                    "trading_supported": False,
                }
            ],
        )

        position = (
            build_service(session, settings)
            .capture(user.id, pipeline_run_id=PIPELINE_A)
            .positions[0]
        )

        assert position.market == "KRW-DELISTED"
        assert position.market_snapshot_id is not None
        assert position.mark_price == 100
        assert position.market_value_krw == 200
        assert position.valuation_status == "PRICED"


def test_negative_position_balance_makes_portfolio_partial_without_fake_value(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {
                    "currency": "BTC",
                    "balance": "-1",
                    "locked": "0",
                    "avg_buy_price": "80",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )

        snapshot = (
            build_service(session, settings)
            .capture(user.id, pipeline_run_id=PIPELINE_A)
            .portfolio_snapshot
        )

        assert snapshot.valuation_status == "PARTIAL"
        assert snapshot.known_total_value_krw == 1000
        assert snapshot.total_value_krw is None
        assert snapshot.position_count == 0
        assert snapshot.positions_estimated_cost_basis_krw is None
        assert snapshot.unrealized_pnl_krw is None


@pytest.mark.parametrize(
    "value", [None, "", "NaN", "Infinity", "-Infinity", "broken", -1, True]
)
def test_invalid_decimal_values_are_rejected(value, settings) -> None:
    service = PortfolioValuationService(  # type: ignore[arg-type]
        SimpleNamespace(), settings=settings
    )

    assert service._non_negative_decimal(value) is None
    assert service._positive_decimal(value) is None


def test_capture_is_idempotent_for_same_user_exchange_and_pipeline(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {
                    "currency": "BTC",
                    "balance": "1",
                    "locked": "0",
                    "avg_buy_price": "80",
                },
            ],
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )
        service = build_service(session, settings)

        first = service.capture(user.id, pipeline_run_id=PIPELINE_A)
        second = service.capture(user.id, pipeline_run_id=PIPELINE_A)

        assert first.portfolio_snapshot.id == second.portfolio_snapshot.id
        assert second.already_captured is True
        assert session.scalar(select(func.count(PortfolioSnapshot.id))) == 1
        assert session.scalar(select(func.count(PortfolioPositionSnapshot.id))) == 1
        assert (
            session.scalar(
                select(func.count(AnalysisRun.id)).where(
                    AnalysisRun.run_type == "PORTFOLIO_VALUATION"
                )
            )
            == 1
        )


@pytest.mark.parametrize("account_run_type", ["ACCOUNT_SNAPSHOT", "MANUAL"])
def test_account_snapshot_run_types_are_supported(
    session_factory, settings, account_run_type
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_account_source(
            session,
            user,
            PIPELINE_A,
            [{"currency": "KRW", "balance": "1000", "locked": "0"}],
            run_type=account_run_type,
        )
        add_universe_source(
            session,
            user,
            PIPELINE_A,
            [{"market": "KRW-BTC", "base_asset": "BTC", "price": "100"}],
        )

        result = build_service(session, settings).capture(
            user.id, pipeline_run_id=PIPELINE_A
        )

        assert result.portfolio_snapshot.cash_total_krw == 1000
        assert result.analysis_run.run_type == "PORTFOLIO_VALUATION"


def test_missing_account_source_records_failed_run_without_partial_snapshot(
    session_factory, settings
) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_universe_source(session, user, PIPELINE_A, [])

        with pytest.raises(ValueError, match="No account snapshots"):
            build_service(session, settings).capture(
                user.id, pipeline_run_id=PIPELINE_A
            )

        failed_run = session.scalar(
            select(AnalysisRun).where(AnalysisRun.run_type == "PORTFOLIO_VALUATION")
        )
        assert failed_run.status == "FAILED"
        assert failed_run.finished_at is not None
        assert session.scalar(select(func.count(PortfolioSnapshot.id))) == 0
        assert session.scalar(select(func.count(PortfolioPositionSnapshot.id))) == 0


def test_new_account_collection_uses_account_snapshot_run_type(monkeypatch) -> None:
    class FakeQuery:
        def filter(self, *args):
            return self

        @staticmethod
        def first():
            return SimpleNamespace(id=1)

    class FakeSession:
        def __init__(self) -> None:
            self.added = []

        @staticmethod
        def get(model, user_id):
            return SimpleNamespace(id=user_id, is_active=True)

        @staticmethod
        def query(model):
            return FakeQuery()

        def add(self, row) -> None:
            self.added.append(row)
            if isinstance(row, AnalysisRun):
                row.id = 10

        def add_all(self, rows) -> None:
            self.added.extend(rows)

        @staticmethod
        def flush() -> None:
            pass

        @staticmethod
        def commit() -> None:
            pass

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
    monkeypatch.setattr(
        "crypto_trading_bot.services.account_snapshot_service.get_settings",
        lambda: SimpleNamespace(trading_mode="AI_APPROVAL"),
    )
    session = FakeSession()

    analysis_run, _ = AccountSnapshotService(
        session,
        upbit_client=client,  # type: ignore[arg-type]
    ).collect_account_snapshots(1, pipeline_run_id=PIPELINE_A)

    assert analysis_run.run_type == "ACCOUNT_SNAPSHOT"
