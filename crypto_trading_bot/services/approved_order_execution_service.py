from dataclasses import dataclass

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderLog
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_execution_service import (
    LiveOrderExecutionError,
    LiveOrderExecutionResult,
    LiveOrderExecutionService,
)
from crypto_trading_bot.services.live_order_safety import LiveOrderSafetyError
from crypto_trading_bot.services.mock_order_attempt_service import (
    MockOrderAttemptResult,
    MockOrderAttemptService,
)
from crypto_trading_bot.services.mock_order_execution_service import (
    MockOrderExecutionError,
)
from crypto_trading_bot.services.order_execution_mode import (
    OrderExecutionModeError,
    assert_order_execution_mode_ready,
)


@dataclass(frozen=True)
class ApprovedOrderExecutionResult:
    execution_mode: str
    order_log: OrderLog | None = None
    mock_order_attempt_result: MockOrderAttemptResult | None = None
    live_order_execution_result: LiveOrderExecutionResult | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error_message is None


class ApprovedOrderExecutionService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
        mock_order_attempt_service: MockOrderAttemptService | None = None,
        live_order_execution_service: LiveOrderExecutionService | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client
        self.mock_order_attempt_service = mock_order_attempt_service
        self.live_order_execution_service = live_order_execution_service

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int,
    ) -> ApprovedOrderExecutionResult:
        settings = get_settings()

        try:
            execution_mode = assert_order_execution_mode_ready(settings)
        except OrderExecutionModeError as error:
            return ApprovedOrderExecutionResult(
                execution_mode="UNKNOWN",
                error_message=str(error),
            )

        if execution_mode == "MOCK":
            return self._execute_mock_order(
                recommendation_id=recommendation_id,
                approval_request_id=approval_request_id,
            )

        return self._execute_live_order(
            recommendation_id=recommendation_id,
            approval_request_id=approval_request_id,
        )

    def _execute_mock_order(
        self,
        recommendation_id: int,
        approval_request_id: int,
    ) -> ApprovedOrderExecutionResult:
        try:
            mock_order_attempt_result = self._get_mock_order_attempt_service().execute(
                recommendation_id=recommendation_id,
                approval_request_id=approval_request_id,
            )

            order_log = self._get_order_log_from_mock_attempt(
                mock_order_attempt_result=mock_order_attempt_result,
            )

            return ApprovedOrderExecutionResult(
                execution_mode="MOCK",
                order_log=order_log,
                mock_order_attempt_result=mock_order_attempt_result,
            )
        except MockOrderExecutionError as error:
            return ApprovedOrderExecutionResult(
                execution_mode="MOCK",
                error_message=str(error),
            )

    def _execute_live_order(
        self,
        recommendation_id: int,
        approval_request_id: int,
    ) -> ApprovedOrderExecutionResult:
        try:
            live_order_execution_result = (
                self._get_live_order_execution_service().execute(
                    recommendation_id=recommendation_id,
                    approval_request_id=approval_request_id,
                )
            )

            return ApprovedOrderExecutionResult(
                execution_mode="LIVE",
                order_log=live_order_execution_result.order_log,
                live_order_execution_result=live_order_execution_result,
            )
        except (LiveOrderExecutionError, LiveOrderSafetyError) as error:
            return ApprovedOrderExecutionResult(
                execution_mode="LIVE",
                error_message=str(error),
            )

    def _get_mock_order_attempt_service(self) -> MockOrderAttemptService:
        if self.mock_order_attempt_service is not None:
            return self.mock_order_attempt_service

        return MockOrderAttemptService(
            session=self.session,
            upbit_client=self.upbit_client,
        )

    def _get_live_order_execution_service(self) -> LiveOrderExecutionService:
        if self.live_order_execution_service is not None:
            return self.live_order_execution_service

        return LiveOrderExecutionService(
            session=self.session,
            upbit_client=self.upbit_client,
        )

    def _get_order_log_from_mock_attempt(
        self,
        mock_order_attempt_result: MockOrderAttemptResult,
    ) -> OrderLog | None:
        if mock_order_attempt_result.order_log_id is None:
            return None

        order_log = self.session.get(
            OrderLog,
            mock_order_attempt_result.order_log_id,
        )

        if order_log is None:
            raise MockOrderExecutionError(
                "Mock order log was not found after execution. "
                f"order_log_id={mock_order_attempt_result.order_log_id}"
            )

        return order_log
