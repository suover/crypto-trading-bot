from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderLog,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.services.mock_order_execution_service import (
    KST,
    MockOrderExecutionError,
    MockOrderExecutionService,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)
        if isinstance(instance, OrderLog):
            instance.id = 10

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def refresh(self, instance: object) -> None:
        pass


class FakeUpbitClient:
    def __init__(
        self,
        *,
        krw: object = "100000",
        coin: object = "2",
        price: object = "10000",
    ) -> None:
        self.accounts = [
            {"currency": "KRW", "balance": krw},
            {"currency": "BTC", "balance": coin},
        ]
        self.price = price

    def get_tickers(self, markets: list[str]) -> list[dict[str, object]]:
        return [{"market": markets[0], "trade_price": self.price}]

    def get_accounts(self) -> list[dict[str, object]]:
        return self.accounts


class StubMockOrderExecutionService(MockOrderExecutionService):
    def __init__(
        self,
        recommendation: TradeRecommendation,
        *,
        daily_amount: Decimal = Decimal("0"),
        existing_order: OrderLog | None = None,
        krw: object = "100000",
        coin: object = "2",
        price: object = "10000",
    ) -> None:
        super().__init__(
            session=FakeSession(),  # type: ignore[arg-type]
            upbit_client=FakeUpbitClient(krw=krw, coin=coin, price=price),  # type: ignore[arg-type]
        )
        self.recommendation = recommendation
        self.daily_amount = daily_amount
        self.existing_order = existing_order
        now = datetime.now(UTC)
        self.approval = ApprovalRequest(
            id=20,
            recommendation_id=recommendation.id,
            user_id=recommendation.user_id,
            status="APPROVED",
            callback_token="token",
            expires_at=now + timedelta(minutes=10),
            approved_at=now,
        )

    def _get_recommendation_for_update(
        self, recommendation_id: int
    ) -> TradeRecommendation | None:
        return self.recommendation

    def _get_approval_request_for_update(
        self, approval_request_id: int
    ) -> ApprovalRequest | None:
        return self.approval

    def _get_user_for_update(self, user_id: int) -> User | None:
        return User(id=user_id, name="test")

    def _get_order_log(self, recommendation_id: int) -> OrderLog | None:
        return self.existing_order

    def _get_daily_mock_order_amount(self, user_id: int) -> Decimal:
        return self.daily_amount


def set_mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("ALLOWED_MARKETS", "KRW-BTC")
    monkeypatch.setenv("MAX_ORDER_AMOUNT_KRW", "10000")
    monkeypatch.setenv("DAILY_MAX_ORDER_AMOUNT_KRW", "30000")
    get_settings.cache_clear()


def recommendation(
    action: str,
    *,
    amount: Decimal | None = None,
    quantity: Decimal | None = None,
    ratio: object = Decimal("1"),
) -> TradeRecommendation:
    return TradeRecommendation(
        id=1,
        analysis_run_id=1,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action=action,
        trade_ratio=ratio,
        confidence=Decimal("0.8"),
        reason="test",
        recommended_amount_krw=amount,
        recommended_quantity=quantity,
        status="APPROVED",
    )


def test_mock_full_sell_above_buy_maximum_and_consumed_daily_limit_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    approved_quantity = Decimal("2")
    service = StubMockOrderExecutionService(
        recommendation("SELL", quantity=approved_quantity, ratio=Decimal("1")),
        daily_amount=Decimal("1000000"),
    )

    result = service.execute(1, 20)

    assert result.order_log.amount_krw == Decimal("20000.00")
    assert result.order_log.quantity == approved_quantity
    assert result.recommendation.trade_ratio == Decimal("1")


def test_mock_buy_above_maximum_remains_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("BUY", amount=Decimal("10001"))
    )

    with pytest.raises(MockOrderExecutionError, match="exceeds maximum"):
        service.execute(1, 20)


def test_mock_buy_above_remaining_daily_limit_remains_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("BUY", amount=Decimal("5001")),
        daily_amount=Decimal("25000"),
    )

    with pytest.raises(MockOrderExecutionError, match="Daily order amount limit"):
        service.execute(1, 20)


@pytest.mark.parametrize(
    ("quantity", "coin", "match"),
    [
        (Decimal("0"), "2", "greater than 0"),
        (Decimal("3"), "2", "Insufficient coin balance"),
        (Decimal("0.1"), "2", "below minimum"),
    ],
)
def test_mock_sell_specific_validations_remain_enforced(
    monkeypatch: pytest.MonkeyPatch,
    quantity: Decimal,
    coin: str,
    match: str,
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("SELL", quantity=quantity),
        coin=coin,
    )

    with pytest.raises(MockOrderExecutionError, match=match):
        service.execute(1, 20)


def test_mock_unapproved_request_remains_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("SELL", quantity=Decimal("1"))
    )
    service.approval.status = "PENDING"

    with pytest.raises(MockOrderExecutionError, match="not approved"):
        service.execute(1, 20)


def test_mock_duplicate_order_is_returned_without_new_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    rec = recommendation("SELL", quantity=Decimal("1"))
    existing = OrderLog(
        id=99,
        recommendation_id=1,
        approval_request_id=20,
        user_id=1,
        trading_mode="MOCK",
        exchange="UPBIT",
        market="KRW-BTC",
        side="SELL",
        order_type="MARKET",
        amount_krw=Decimal("10000"),
        quantity=Decimal("1"),
        status="MOCK_FILLED",
    )
    service = StubMockOrderExecutionService(rec, existing_order=existing)

    result = service.execute(1, 20)

    assert result.already_executed is True
    assert result.order_log is existing
    assert service.session.added == []  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    get_settings.cache_clear()


def test_daily_mock_buy_total_executes_with_side_and_mode_isolation() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    day_start_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    within_today_utc = (day_start_kst + timedelta(minutes=30)).astimezone(UTC)
    before_today_utc = (day_start_kst - timedelta(minutes=30)).astimezone(UTC)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE order_logs (user_id INTEGER, trading_mode TEXT, side TEXT, "
            "status TEXT, amount_krw NUMERIC, created_at DATETIME)"
        )
        rows = [
            (1, "MOCK", "SELL", "MOCK_FILLED", 1000000, within_today_utc),
            (1, "MOCK", "BUY", "MOCK_FILLED", 5000, within_today_utc),
            (1, "LIVE", "BUY", "MOCK_FILLED", 9000, within_today_utc),
            (1, "MOCK", "BUY", "MOCK_FILLED", 11000, before_today_utc),
        ]
        connection.exec_driver_sql(
            "INSERT INTO order_logs VALUES (?, ?, ?, ?, ?, ?)", rows
        )
    with Session(engine) as session:
        service = MockOrderExecutionService(session=session)
        assert service._get_daily_mock_order_amount(1) == Decimal("5000")


def test_large_prior_mock_sell_does_not_block_later_mock_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("BUY", amount=Decimal("5000")), daily_amount=Decimal("0")
    )
    result = service.execute(1, 20)
    assert result.order_log.side == "BUY"
    assert result.order_log.amount_krw == Decimal("5000.00")


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_mock_null_trade_ratio_is_rejected_without_order_log(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation(
            action,
            amount=Decimal("5000") if action == "BUY" else None,
            quantity=Decimal("1") if action == "SELL" else None,
            ratio=None,
        )
    )
    with pytest.raises(MockOrderExecutionError, match="trade_ratio"):
        service.execute(1, 20)
    assert service.session.added == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "ratio",
    [
        "bad",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("-0.1"),
        Decimal("1.1"),
    ],
)
def test_mock_invalid_trade_ratio_is_rejected_without_order_log(
    monkeypatch: pytest.MonkeyPatch, ratio: object
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("BUY", amount=Decimal("5000"), ratio=ratio)
    )
    with pytest.raises(MockOrderExecutionError, match="trade_ratio|Invalid decimal"):
        service.execute(1, 20)
    assert service.session.added == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("action", ["BUY", "SELL"])
@pytest.mark.parametrize("ratio", [Decimal("0.1"), Decimal("1")])
def test_mock_valid_trade_ratio_preserves_execution(
    monkeypatch: pytest.MonkeyPatch, action: str, ratio: Decimal
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation(
            action,
            amount=Decimal("5000") if action == "BUY" else None,
            quantity=Decimal("1") if action == "SELL" else None,
            ratio=ratio,
        )
    )
    result = service.execute(1, 20)
    assert result.order_log.side == action


@pytest.mark.parametrize(
    "price", [None, "bad", "NaN", "Infinity", "-Infinity", "0", "-1"]
)
def test_mock_invalid_ticker_is_domain_error_without_order_log(
    monkeypatch: pytest.MonkeyPatch, price: object
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("SELL", quantity=Decimal("1")), price=price
    )
    with pytest.raises(MockOrderExecutionError, match="price|decimal"):
        service.execute(1, 20)
    assert service.session.added == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("balance", [None, "bad", "NaN", "Infinity", "-Infinity", "-1"])
def test_mock_invalid_sell_balance_is_domain_error_without_order_log(
    monkeypatch: pytest.MonkeyPatch, balance: object
) -> None:
    set_mock_env(monkeypatch)
    service = StubMockOrderExecutionService(
        recommendation("SELL", quantity=Decimal("1")), coin=balance
    )
    with pytest.raises(MockOrderExecutionError, match="balance|decimal"):
        service.execute(1, 20)
    assert service.session.added == []  # type: ignore[attr-defined]
