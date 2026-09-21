from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderExecutionAttempt,
    OrderRetryNotification,
    TradeRecommendation,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.mock_order_execution_service import (
    MockOrderExecutionError,
    MockOrderExecutionService,
)


KST = ZoneInfo("Asia/Seoul")

TERMINAL_ATTEMPT_STATUSES = frozenset(
    {
        "EXECUTED",
        "ALREADY_EXECUTED",
        "PERMANENT_FAILED",
        "RETRY_EXHAUSTED",
    }
)

OUTBOX_NOTIFICATION_STATUSES = frozenset(
    {
        "EXECUTED",
        "PERMANENT_FAILED",
        "RETRY_EXHAUSTED",
    }
)

AttemptResultStatus = Literal[
    "EXECUTED",
    "ALREADY_EXECUTED",
    "RETRYABLE_FAILED",
    "PERMANENT_FAILED",
    "RETRY_EXHAUSTED",
]


@dataclass(frozen=True)
class MockOrderAttemptResult:
    recommendation_id: int
    approval_request_id: int
    attempt_id: int
    attempt_number: int
    status: AttemptResultStatus
    order_log_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    next_retry_at: datetime | None = None


@dataclass(frozen=True)
class FailureClassification:
    retryable: bool
    error_code: str
    error_message: str


class MockOrderAttemptService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int,
    ) -> MockOrderAttemptResult:
        settings = get_settings()

        recommendation = self._get_recommendation_for_update(
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            raise MockOrderExecutionError(
                f"Trade recommendation not found. recommendation_id={recommendation_id}"
            )

        approval_request = self._get_approval_request_for_update(
            approval_request_id=approval_request_id,
        )

        if approval_request is None:
            raise MockOrderExecutionError(
                f"Approval request not found. approval_request_id={approval_request_id}"
            )

        latest_attempt = self._get_latest_attempt(
            recommendation_id=recommendation.id,
        )

        if latest_attempt is None:
            attempt_number = 1
        else:
            if latest_attempt.approval_request_id != approval_request.id:
                raise MockOrderExecutionError(
                    "Latest execution attempt approval request "
                    "does not match. "
                    f"latest_approval_request_id="
                    f"{latest_attempt.approval_request_id}, "
                    f"requested_approval_request_id="
                    f"{approval_request.id}"
                )

            if latest_attempt.status in TERMINAL_ATTEMPT_STATUSES:
                return self._build_result_from_attempt(
                    attempt=latest_attempt,
                )

            if latest_attempt.status != "RETRYABLE_FAILED":
                raise MockOrderExecutionError(
                    "Unexpected latest execution attempt status. "
                    f"status={latest_attempt.status}"
                )

            if latest_attempt.next_retry_at is None:
                raise MockOrderExecutionError(
                    "Retryable execution attempt does not have next_retry_at"
                )

            if datetime.now(KST) < latest_attempt.next_retry_at:
                return self._build_result_from_attempt(
                    attempt=latest_attempt,
                )

            attempt_number = latest_attempt.attempt_number + 1

        maximum_attempts = 1 + settings.mock_order_retry_max_retries

        if attempt_number > maximum_attempts:
            raise MockOrderExecutionError(
                "Mock order execution attempt limit exceeded. "
                f"recommendation_id={recommendation.id}, "
                f"maximum_attempts={maximum_attempts}"
            )

        execution_service = MockOrderExecutionService(
            session=self.session,
            upbit_client=self.upbit_client,
        )

        try:
            execution_result = execution_service.execute(
                recommendation_id=recommendation.id,
                approval_request_id=approval_request.id,
                commit=False,
            )

        except MockOrderExecutionError as error:
            classification = self._classify_execution_error(error)

            return self._record_failure(
                recommendation=recommendation,
                approval_request=approval_request,
                attempt_number=attempt_number,
                maximum_attempts=maximum_attempts,
                classification=classification,
            )

        except httpx.HTTPStatusError as error:
            classification = self._classify_http_status_error(error)

            return self._record_failure(
                recommendation=recommendation,
                approval_request=approval_request,
                attempt_number=attempt_number,
                maximum_attempts=maximum_attempts,
                classification=classification,
            )

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as error:
            classification = FailureClassification(
                retryable=True,
                error_code="UPBIT_NETWORK_ERROR",
                error_message=str(error),
            )

            return self._record_failure(
                recommendation=recommendation,
                approval_request=approval_request,
                attempt_number=attempt_number,
                maximum_attempts=maximum_attempts,
                classification=classification,
            )

        status: AttemptResultStatus = (
            "ALREADY_EXECUTED" if execution_result.already_executed else "EXECUTED"
        )

        attempt = OrderExecutionAttempt(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            order_log_id=execution_result.order_log.id,
            user_id=recommendation.user_id,
            trading_mode="MOCK",
            attempt_number=attempt_number,
            status=status,
            error_code=None,
            error_message=None,
            next_retry_at=None,
        )

        self.session.add(attempt)

        # attempt.id가 발급된 뒤 같은 트랜잭션에서
        # 알림 Outbox를 함께 저장한다.
        self.session.flush()

        self._enqueue_retry_notification(
            attempt=attempt,
            approval_request=approval_request,
        )

        # order_log, execution attempt, notification outbox를
        # 하나의 트랜잭션으로 확정한다.
        self.session.commit()
        self.session.refresh(attempt)

        return MockOrderAttemptResult(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=status,
            order_log_id=execution_result.order_log.id,
        )

    def _record_failure(
        self,
        recommendation: TradeRecommendation,
        approval_request: ApprovalRequest,
        attempt_number: int,
        maximum_attempts: int,
        classification: FailureClassification,
    ) -> MockOrderAttemptResult:
        settings = get_settings()

        if not classification.retryable:
            status: AttemptResultStatus = "PERMANENT_FAILED"
            next_retry_at = None

        elif attempt_number >= maximum_attempts:
            status = "RETRY_EXHAUSTED"
            next_retry_at = None

        else:
            status = "RETRYABLE_FAILED"

            retry_delays = settings.mock_order_retry_delay_list
            retry_index = attempt_number - 1

            if retry_index >= len(retry_delays):
                raise ValueError(
                    "Retry delay configuration is shorter than "
                    "mock_order_retry_max_retries"
                )

            next_retry_at = datetime.now(KST) + timedelta(
                minutes=retry_delays[retry_index]
            )

        attempt = OrderExecutionAttempt(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            order_log_id=None,
            user_id=recommendation.user_id,
            trading_mode="MOCK",
            attempt_number=attempt_number,
            status=status,
            error_code=classification.error_code,
            error_message=classification.error_message,
            next_retry_at=next_retry_at,
        )

        self.session.add(attempt)

        # attempt.id가 발급된 뒤 같은 트랜잭션에서
        # 알림 Outbox를 함께 저장한다.
        self.session.flush()

        self._enqueue_retry_notification(
            attempt=attempt,
            approval_request=approval_request,
        )

        self.session.commit()
        self.session.refresh(attempt)

        return MockOrderAttemptResult(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request.id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=status,
            error_code=attempt.error_code,
            error_message=attempt.error_message,
            next_retry_at=attempt.next_retry_at,
        )

    def _enqueue_retry_notification(
        self,
        attempt: OrderExecutionAttempt,
        approval_request: ApprovalRequest,
    ) -> None:
        # 최초 승인 시도의 결과는 텔레그램 승인 메시지를
        # 직접 수정하므로 별도 후속 알림을 만들지 않는다.
        if attempt.attempt_number < 2:
            return

        # 재시도가 끝났거나 성공한 경우에만
        # 후속 텔레그램 알림을 생성한다.
        if attempt.status not in OUTBOX_NOTIFICATION_STATUSES:
            return

        if approval_request.telegram_chat_id is None:
            return

        notification = OrderRetryNotification(
            attempt_id=attempt.id,
            recommendation_id=attempt.recommendation_id,
            approval_request_id=approval_request.id,
            telegram_chat_id=(approval_request.telegram_chat_id),
            retry_status=attempt.status,
            delivery_status="PENDING",
            retry_count=0,
            next_retry_at=None,
            error_message=None,
            sent_at=None,
        )

        self.session.add(notification)

    def _get_recommendation_for_update(
        self,
        recommendation_id: int,
    ) -> TradeRecommendation | None:
        statement = (
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_approval_request_for_update(
        self,
        approval_request_id: int,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_request_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_latest_attempt(
        self,
        recommendation_id: int,
    ) -> OrderExecutionAttempt | None:
        statement = (
            select(OrderExecutionAttempt)
            .where(
                OrderExecutionAttempt.recommendation_id == recommendation_id,
                OrderExecutionAttempt.trading_mode == "MOCK",
            )
            .order_by(
                OrderExecutionAttempt.attempt_number.desc(),
            )
            .limit(1)
        )

        return self.session.scalar(statement)

    @staticmethod
    def _build_result_from_attempt(
        attempt: OrderExecutionAttempt,
    ) -> MockOrderAttemptResult:
        return MockOrderAttemptResult(
            recommendation_id=attempt.recommendation_id,
            approval_request_id=(attempt.approval_request_id),
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            order_log_id=attempt.order_log_id,
            error_code=attempt.error_code,
            error_message=attempt.error_message,
            next_retry_at=attempt.next_retry_at,
        )

    @staticmethod
    def _classify_execution_error(
        error: MockOrderExecutionError,
    ) -> FailureClassification:
        error_message = str(error)

        retryable_prefixes = (
            "Insufficient KRW balance.",
            "Insufficient coin balance.",
            "Daily order amount limit exceeded.",
            "Current market price was not found.",
        )

        retryable = error_message.startswith(retryable_prefixes)

        return FailureClassification(
            retryable=retryable,
            error_code=(
                "MOCK_ORDER_RETRYABLE_ERROR"
                if retryable
                else "MOCK_ORDER_PERMANENT_ERROR"
            ),
            error_message=error_message,
        )

    @staticmethod
    def _classify_http_status_error(
        error: httpx.HTTPStatusError,
    ) -> FailureClassification:
        status_code = error.response.status_code
        retryable = status_code == 429 or status_code >= 500

        return FailureClassification(
            retryable=retryable,
            error_code=f"UPBIT_HTTP_{status_code}",
            error_message=str(error),
        )
