from decimal import Decimal

import pytest

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.services.approved_order_execution_service import (
    ApprovedOrderExecutionService,
)
from crypto_trading_bot.services.live_order_execution_service import (
    LIVE_ORDER_STATUS,
    LiveOrderExecutionError,
    LiveOrderExecutionResult,
)
from crypto_trading_bot.services.live_order_safety import LIVE_ORDER_CONFIRMATION_TEXT
from crypto_trading_bot.services.mock_order_attempt_service import (
    MockOrderAttemptResult,
)
from crypto_trading_bot.services.mock_order_execution_service import (
    MOCK_ORDER_STATUS,
    MockOrderExecutionError,
)


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def set_order_execution_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    order_execution_mode: str = "MOCK",
    live_ready: bool = False,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("ORDER_EXECUTION_MODE", order_execution_mode)
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "access")
    monkeypatch.setenv(
        "UPBIT_SECRET_KEY",
        "test-secret-key-for-approved-order-execution-service-0123456789abcdef",
    )
    monkeypatch.setenv("ALLOWED_MARKETS", "KRW-BTC,KRW-ETH")
    monkeypatch.setenv("MAX_ORDER_AMOUNT_KRW", "10000")
    monkeypatch.setenv("DAILY_MAX_ORDER_AMOUNT_KRW", "30000")

    if live_ready:
        monkeypatch.setenv("LIVE_ORDER_ENABLED", "true")
        monkeypatch.setenv("LIVE_ORDER_CONFIRMATION", LIVE_ORDER_CONFIRMATION_TEXT)
    else:
        monkeypatch.setenv("LIVE_ORDER_ENABLED", "false")
        monkeypatch.setenv("LIVE_ORDER_CONFIRMATION", "")

    clear_settings_cache()


def build_order_log(
    *,
    order_log_id: int = 10,
    trading_mode: str = "MOCK",
    status: str = MOCK_ORDER_STATUS,
    exchange_order_id: str | None = None,
) -> OrderLog:
    return OrderLog(
        id=order_log_id,
        recommendation_id=1,
        approval_request_id=20,
        user_id=1,
        trading_mode=trading_mode,
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        amount_krw=Decimal("5000"),
        quantity=Decimal("0.0001"),
        price=Decimal("50000000"),
        status=status,
        exchange_order_id=exchange_order_id,
        error_message=None,
        raw_response={},
    )


def build_recommendation() -> TradeRecommendation:
    return TradeRecommendation(
        id=1,
        analysis_run_id=1,
        market_snapshot_id=None,
        user_id=1,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
        confidence=Decimal("0.7500"),
        reason="test recommendation",
        recommended_amount_krw=Decimal("5000"),
        recommended_quantity=None,
        ai_model="TEST",
        ai_response={},
        status="APPROVED",
    )


class FakeSession:
    def __init__(
        self,
        order_log: OrderLog | None = None,
    ) -> None:
        self.order_log = order_log

    def get(
        self,
        model: type[OrderLog],
        object_id: int,
    ) -> object | None:
        assert model is OrderLog

        if self.order_log is not None and self.order_log.id == object_id:
            return self.order_log

        return None


class FakeMockOrderAttemptService:
    def __init__(
        self,
        result: MockOrderAttemptResult | None = None,
        error: MockOrderExecutionError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, int]] = []

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int,
    ) -> MockOrderAttemptResult:
        self.calls.append(
            {
                "recommendation_id": recommendation_id,
                "approval_request_id": approval_request_id,
            }
        )

        if self.error is not None:
            raise self.error

        if self.result is None:
            raise AssertionError("Fake mock order attempt result is not configured")

        return self.result


class FakeLiveOrderExecutionService:
    def __init__(
        self,
        result: LiveOrderExecutionResult | None = None,
        error: LiveOrderExecutionError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, int]] = []

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int | None = None,
    ) -> LiveOrderExecutionResult:
        if approval_request_id is None:
            raise AssertionError("approval_request_id must not be None")

        self.calls.append(
            {
                "recommendation_id": recommendation_id,
                "approval_request_id": approval_request_id,
            }
        )

        if self.error is not None:
            raise self.error

        if self.result is None:
            raise AssertionError("Fake live order execution result is not configured")

        return self.result


def test_execute_uses_mock_order_when_mode_is_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order_execution_env(
        monkeypatch,
        order_execution_mode="MOCK",
    )

    order_log = build_order_log(
        trading_mode="MOCK",
        status=MOCK_ORDER_STATUS,
    )
    mock_result = MockOrderAttemptResult(
        recommendation_id=1,
        approval_request_id=20,
        attempt_id=100,
        attempt_number=1,
        status="EXECUTED",
        order_log_id=order_log.id,
    )
    fake_mock_service = FakeMockOrderAttemptService(
        result=mock_result,
    )
    fake_live_service = FakeLiveOrderExecutionService(
        result=LiveOrderExecutionResult(
            order_log=build_order_log(
                trading_mode="LIVE",
                status=LIVE_ORDER_STATUS,
                exchange_order_id="live-order-id",
            ),
            recommendation=build_recommendation(),
            already_executed=False,
        )
    )

    service = ApprovedOrderExecutionService(
        session=FakeSession(order_log),  # type: ignore[arg-type]
        mock_order_attempt_service=fake_mock_service,  # type: ignore[arg-type]
        live_order_execution_service=fake_live_service,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.succeeded is True
    assert result.execution_mode == "MOCK"
    assert result.mock_order_attempt_result == mock_result
    assert result.live_order_execution_result is None
    assert result.order_log is order_log
    assert fake_mock_service.calls == [
        {
            "recommendation_id": 1,
            "approval_request_id": 20,
        }
    ]
    assert fake_live_service.calls == []


def test_execute_uses_live_order_when_mode_is_live_and_safety_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order_execution_env(
        monkeypatch,
        order_execution_mode="LIVE",
        live_ready=True,
    )

    live_order_log = build_order_log(
        trading_mode="LIVE",
        status=LIVE_ORDER_STATUS,
        exchange_order_id="live-order-id",
    )
    live_result = LiveOrderExecutionResult(
        order_log=live_order_log,
        recommendation=build_recommendation(),
        already_executed=False,
    )
    fake_mock_service = FakeMockOrderAttemptService(
        result=MockOrderAttemptResult(
            recommendation_id=1,
            approval_request_id=20,
            attempt_id=100,
            attempt_number=1,
            status="EXECUTED",
            order_log_id=10,
        )
    )
    fake_live_service = FakeLiveOrderExecutionService(
        result=live_result,
    )

    service = ApprovedOrderExecutionService(
        session=FakeSession(),  # type: ignore[arg-type]
        mock_order_attempt_service=fake_mock_service,  # type: ignore[arg-type]
        live_order_execution_service=fake_live_service,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.succeeded is True
    assert result.execution_mode == "LIVE"
    assert result.mock_order_attempt_result is None
    assert result.live_order_execution_result == live_result
    assert result.order_log is live_order_log
    assert fake_mock_service.calls == []
    assert fake_live_service.calls == [
        {
            "recommendation_id": 1,
            "approval_request_id": 20,
        }
    ]


def test_execute_blocks_live_order_when_live_safety_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order_execution_env(
        monkeypatch,
        order_execution_mode="LIVE",
        live_ready=False,
    )

    fake_mock_service = FakeMockOrderAttemptService()
    fake_live_service = FakeLiveOrderExecutionService()

    service = ApprovedOrderExecutionService(
        session=FakeSession(),  # type: ignore[arg-type]
        mock_order_attempt_service=fake_mock_service,  # type: ignore[arg-type]
        live_order_execution_service=fake_live_service,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.succeeded is False
    assert result.execution_mode == "UNKNOWN"
    assert result.error_message is not None
    assert "Order execution mode is not ready" in result.error_message
    assert fake_mock_service.calls == []
    assert fake_live_service.calls == []


def test_execute_returns_mock_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order_execution_env(
        monkeypatch,
        order_execution_mode="MOCK",
    )

    fake_mock_service = FakeMockOrderAttemptService(
        error=MockOrderExecutionError("mock order failed"),
    )

    service = ApprovedOrderExecutionService(
        session=FakeSession(),  # type: ignore[arg-type]
        mock_order_attempt_service=fake_mock_service,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.succeeded is False
    assert result.execution_mode == "MOCK"
    assert result.error_message == "mock order failed"


def test_execute_returns_live_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order_execution_env(
        monkeypatch,
        order_execution_mode="LIVE",
        live_ready=True,
    )

    fake_live_service = FakeLiveOrderExecutionService(
        error=LiveOrderExecutionError("live order failed"),
    )

    service = ApprovedOrderExecutionService(
        session=FakeSession(),  # type: ignore[arg-type]
        live_order_execution_service=fake_live_service,  # type: ignore[arg-type]
    )

    result = service.execute(
        recommendation_id=1,
        approval_request_id=20,
    )

    assert result.succeeded is False
    assert result.execution_mode == "LIVE"
    assert result.error_message == "live order failed"


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test() -> None:
    yield
    clear_settings_cache()
