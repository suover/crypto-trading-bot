from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderLog,
    TradeRecommendation,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.mock_order_execution_service import (
    MockOrderExecutionError,
    MockOrderExecutionService,
)


RetryResultStatus = Literal[
    "EXECUTED",
    "ALREADY_EXECUTED",
    "REJECTED",
]


@dataclass(frozen=True)
class MockOrderRetryCandidate:
    recommendation_id: int
    approval_request_id: int


@dataclass(frozen=True)
class MockOrderRetryResult:
    recommendation_id: int
    approval_request_id: int
    status: RetryResultStatus
    order_log_id: int | None = None
    error_message: str | None = None


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
    def rejected_count(self) -> int:
        return self._count_status("REJECTED")

    def _count_status(
        self,
        status: RetryResultStatus,
    ) -> int:
        return sum(
            1
            for result in self.results
            if result.status == status
        )


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

        # 추천별 가장 최근의 승인 완료 요청을 선택
        latest_approved_requests = (
            select(
                ApprovalRequest.recommendation_id.label(
                    "recommendation_id"
                ),
                func.max(ApprovalRequest.id).label(
                    "approval_request_id"
                ),
            )
            .where(
                ApprovalRequest.status == "APPROVED",
                ApprovalRequest.approved_at.is_not(None),
            )
            .group_by(ApprovalRequest.recommendation_id)
            .subquery()
        )

        # 이미 주문 로그가 있는 추천은 재시도 대상에서 제외
        order_log_exists = exists().where(
            OrderLog.recommendation_id
            == TradeRecommendation.id
        )

        statement = (
            select(
                TradeRecommendation.id,
                latest_approved_requests.c.approval_request_id,
            )
            .join(
                latest_approved_requests,
                latest_approved_requests.c.recommendation_id
                == TradeRecommendation.id,
            )
            .where(
                TradeRecommendation.status == "APPROVED",
                TradeRecommendation.action.in_(
                    ("BUY", "SELL")
                ),
                ~order_log_exists,
            )
            .order_by(
                latest_approved_requests.c.approval_request_id.asc()
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
        # 후보마다 별도 세션을 사용하여 한 건의 실패가
        # 다른 후보 처리에 영향을 주지 않도록 함
        with self.session_factory() as session:
            execution_service = MockOrderExecutionService(
                session=session,
                upbit_client=self.upbit_client,
            )

            try:
                execution_result = execution_service.execute(
                    recommendation_id=(
                        candidate.recommendation_id
                    ),
                    approval_request_id=(
                        candidate.approval_request_id
                    ),
                )

            except MockOrderExecutionError as error:
                session.rollback()

                return MockOrderRetryResult(
                    recommendation_id=(
                        candidate.recommendation_id
                    ),
                    approval_request_id=(
                        candidate.approval_request_id
                    ),
                    status="REJECTED",
                    error_message=str(error),
                )

            status: RetryResultStatus = (
                "ALREADY_EXECUTED"
                if execution_result.already_executed
                else "EXECUTED"
            )

            return MockOrderRetryResult(
                recommendation_id=(
                    candidate.recommendation_id
                ),
                approval_request_id=(
                    candidate.approval_request_id
                ),
                status=status,
                order_log_id=execution_result.order_log.id,
            )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit <= 0:
            raise ValueError(
                f"limit must be greater than 0. limit={limit}"
            )

        if limit > 100:
            raise ValueError(
                f"limit must not exceed 100. limit={limit}"
            )