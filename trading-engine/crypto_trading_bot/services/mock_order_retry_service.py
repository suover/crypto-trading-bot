from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderExecutionAttempt,
    OrderLog,
    TradeRecommendation,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.mock_order_attempt_service import (
    MockOrderAttemptService,
)


KST = ZoneInfo("Asia/Seoul")

RetryResultStatus = Literal[
    "EXECUTED",
    "ALREADY_EXECUTED",
    "RETRYABLE_FAILED",
    "PERMANENT_FAILED",
    "RETRY_EXHAUSTED",
]


@dataclass(frozen=True)
class MockOrderRetryCandidate:
    recommendation_id: int
    approval_request_id: int


@dataclass(frozen=True)
class MockOrderRetryResult:
    recommendation_id: int
    approval_request_id: int
    attempt_id: int
    attempt_number: int
    status: RetryResultStatus
    order_log_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    next_retry_at: datetime | None = None


@dataclass(frozen=True)
class MockOrderRetrySummary:
    candidates: tuple[MockOrderRetryCandidate, ...]
    results: tuple[MockOrderRetryResult, ...]

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def executed_count(self) -> int:
        return self._count_status("EXECUTED")

    @property
    def already_executed_count(self) -> int:
        return self._count_status("ALREADY_EXECUTED")

    @property
    def retryable_failed_count(self) -> int:
        return self._count_status("RETRYABLE_FAILED")

    @property
    def permanent_failed_count(self) -> int:
        return self._count_status("PERMANENT_FAILED")

    @property
    def retry_exhausted_count(self) -> int:
        return self._count_status("RETRY_EXHAUSTED")

    def _count_status(
        self,
        status: RetryResultStatus,
    ) -> int:
        return sum(1 for result in self.results if result.status == status)


class MockOrderRetryService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.upbit_client = upbit_client

    def get_candidates(
        self,
        limit: int = 20,
    ) -> tuple[MockOrderRetryCandidate, ...]:
        self._validate_limit(limit)

        now = datetime.now(KST)

        # 추천별 가장 최근 승인 완료 요청
        latest_approved_requests = (
            select(
                ApprovalRequest.recommendation_id.label("recommendation_id"),
                func.max(ApprovalRequest.id).label("approval_request_id"),
            )
            .where(
                ApprovalRequest.status == "APPROVED",
                ApprovalRequest.approved_at.is_not(None),
            )
            .group_by(
                ApprovalRequest.recommendation_id,
            )
            .subquery()
        )

        # 추천별 가장 큰 MOCK 시도 번호
        latest_mock_attempt_numbers = (
            select(
                OrderExecutionAttempt.recommendation_id.label("recommendation_id"),
                func.max(OrderExecutionAttempt.attempt_number).label("attempt_number"),
            )
            .where(
                OrderExecutionAttempt.trading_mode == "MOCK",
            )
            .group_by(
                OrderExecutionAttempt.recommendation_id,
            )
            .subquery()
        )

        # 가장 최근 MOCK 시도의 상태와 다음 재시도 시각
        latest_mock_attempts = (
            select(
                OrderExecutionAttempt.recommendation_id.label("recommendation_id"),
                OrderExecutionAttempt.status.label("status"),
                OrderExecutionAttempt.next_retry_at.label("next_retry_at"),
            )
            .join(
                latest_mock_attempt_numbers,
                and_(
                    latest_mock_attempt_numbers.c.recommendation_id
                    == OrderExecutionAttempt.recommendation_id,
                    latest_mock_attempt_numbers.c.attempt_number
                    == OrderExecutionAttempt.attempt_number,
                ),
            )
            .where(
                OrderExecutionAttempt.trading_mode == "MOCK",
            )
            .subquery()
        )

        # 주문 로그가 존재하면 재시도 대상에서 무조건 제외
        order_log_exists = exists().where(
            OrderLog.recommendation_id == TradeRecommendation.id
        )

        statement = (
            select(
                TradeRecommendation.id,
                latest_approved_requests.c.approval_request_id,
            )
            .join(
                latest_approved_requests,
                latest_approved_requests.c.recommendation_id == TradeRecommendation.id,
            )
            .outerjoin(
                latest_mock_attempts,
                latest_mock_attempts.c.recommendation_id == TradeRecommendation.id,
            )
            .where(
                TradeRecommendation.status == "APPROVED",
                TradeRecommendation.action.in_(("BUY", "SELL")),
                ~order_log_exists,
                or_(
                    # 실행 이력이 없는 최초 실행 대상
                    latest_mock_attempts.c.recommendation_id.is_(None),
                    # 재시도 예정 시각이 지난 대상
                    and_(
                        latest_mock_attempts.c.status == "RETRYABLE_FAILED",
                        latest_mock_attempts.c.next_retry_at.is_not(None),
                        latest_mock_attempts.c.next_retry_at <= now,
                    ),
                ),
            )
            .order_by(
                latest_mock_attempts.c.next_retry_at.asc().nullsfirst(),
                latest_approved_requests.c.approval_request_id.asc(),
            )
            .limit(limit)
        )

        with self.session_factory() as session:
            rows = session.execute(statement).all()

        return tuple(
            MockOrderRetryCandidate(
                recommendation_id=int(row[0]),
                approval_request_id=int(row[1]),
            )
            for row in rows
        )

    def retry_pending(
        self,
        limit: int = 20,
    ) -> MockOrderRetrySummary:
        candidates = self.get_candidates(limit=limit)

        return self.retry_candidates(
            candidates=candidates,
        )

    def retry_candidates(
        self,
        candidates: tuple[MockOrderRetryCandidate, ...],
    ) -> MockOrderRetrySummary:
        results: list[MockOrderRetryResult] = []

        for candidate in candidates:
            result = self._retry_candidate(
                candidate=candidate,
            )
            results.append(result)

        return MockOrderRetrySummary(
            candidates=candidates,
            results=tuple(results),
        )

    def _retry_candidate(
        self,
        candidate: MockOrderRetryCandidate,
    ) -> MockOrderRetryResult:
        # 후보마다 세션을 분리해 한 건의 처리 결과가
        # 다른 후보의 트랜잭션에 영향을 주지 않도록 함
        with self.session_factory() as session:
            attempt_service = MockOrderAttemptService(
                session=session,
                upbit_client=self.upbit_client,
            )

            attempt_result = attempt_service.execute(
                recommendation_id=candidate.recommendation_id,
                approval_request_id=candidate.approval_request_id,
            )

        return MockOrderRetryResult(
            recommendation_id=(attempt_result.recommendation_id),
            approval_request_id=(attempt_result.approval_request_id),
            attempt_id=attempt_result.attempt_id,
            attempt_number=attempt_result.attempt_number,
            status=attempt_result.status,
            order_log_id=attempt_result.order_log_id,
            error_code=attempt_result.error_code,
            error_message=attempt_result.error_message,
            next_retry_at=attempt_result.next_retry_at,
        )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit <= 0:
            raise ValueError(f"limit must be greater than 0. limit={limit}")

        if limit > 100:
            raise ValueError(f"limit must not exceed 100. limit={limit}")
