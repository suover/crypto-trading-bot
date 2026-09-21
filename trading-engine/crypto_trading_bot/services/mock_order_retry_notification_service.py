from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    ApprovalRequest,
    OrderLog,
    TradeRecommendation,
)
from crypto_trading_bot.notification.telegram_client import (
    TelegramClient,
)
from crypto_trading_bot.services.mock_order_retry_service import (
    MockOrderRetryResult,
)


NotificationResultStatus = Literal[
    "SENT",
    "SKIPPED",
    "FAILED",
]

NOTIFIABLE_RETRY_STATUSES = frozenset(
    {
        "EXECUTED",
        "PERMANENT_FAILED",
        "RETRY_EXHAUSTED",
    }
)


@dataclass(frozen=True)
class MockOrderRetryNotificationResult:
    recommendation_id: int
    approval_request_id: int
    attempt_id: int
    retry_status: str
    notification_status: NotificationResultStatus
    error_message: str | None = None


class MockOrderRetryNotificationService:
    def __init__(
        self,
        session: Session,
        telegram_client: TelegramClient,
    ) -> None:
        self.session = session
        self.telegram_client = telegram_client

    def notify(
        self,
        retry_result: MockOrderRetryResult,
        telegram_chat_id: int | None = None,
    ) -> MockOrderRetryNotificationResult:
        if retry_result.status not in NOTIFIABLE_RETRY_STATUSES:
            return self._build_result(
                retry_result=retry_result,
                notification_status="SKIPPED",
            )

        approval_request = self.session.get(
            ApprovalRequest,
            retry_result.approval_request_id,
        )

        if approval_request is None:
            return self._build_result(
                retry_result=retry_result,
                notification_status="FAILED",
                error_message=(
                    "Approval request was not found. "
                    f"approval_request_id="
                    f"{retry_result.approval_request_id}"
                ),
            )

        recommendation = self.session.get(
            TradeRecommendation,
            retry_result.recommendation_id,
        )

        if recommendation is None:
            return self._build_result(
                retry_result=retry_result,
                notification_status="FAILED",
                error_message=(
                    "Trade recommendation was not found. "
                    f"recommendation_id="
                    f"{retry_result.recommendation_id}"
                ),
            )

        # Outbox에 저장된 채팅 ID를 넘겨받은 경우 우선 사용하고,
        # 없으면 기존 승인 요청에 저장된 채팅 ID를 사용한다.
        resolved_chat_id = (
            telegram_chat_id
            if telegram_chat_id is not None
            else approval_request.telegram_chat_id
        )

        if resolved_chat_id is None:
            return self._build_result(
                retry_result=retry_result,
                notification_status="SKIPPED",
                error_message=("Telegram chat ID is not available"),
            )

        order_log = None

        if retry_result.order_log_id is not None:
            order_log = self.session.get(
                OrderLog,
                retry_result.order_log_id,
            )

            if order_log is None:
                return self._build_result(
                    retry_result=retry_result,
                    notification_status="FAILED",
                    error_message=(
                        "Order log was not found. "
                        f"order_log_id="
                        f"{retry_result.order_log_id}"
                    ),
                )

        message = self._build_message(
            retry_result=retry_result,
            recommendation=recommendation,
            order_log=order_log,
        )

        try:
            self.telegram_client.send_message(
                chat_id=resolved_chat_id,
                text=message,
            )

        except Exception as error:
            return self._build_result(
                retry_result=retry_result,
                notification_status="FAILED",
                error_message=(f"{type(error).__name__}: {error}"),
            )

        return self._build_result(
            retry_result=retry_result,
            notification_status="SENT",
        )

    @classmethod
    def _build_message(
        cls,
        retry_result: MockOrderRetryResult,
        recommendation: TradeRecommendation,
        order_log: OrderLog | None,
    ) -> str:
        if retry_result.status == "EXECUTED":
            return cls._build_executed_message(
                retry_result=retry_result,
                recommendation=recommendation,
                order_log=order_log,
            )

        if retry_result.status == "PERMANENT_FAILED":
            return cls._build_permanent_failure_message(
                retry_result=retry_result,
                recommendation=recommendation,
            )

        return cls._build_exhausted_message(
            retry_result=retry_result,
            recommendation=recommendation,
        )

    @classmethod
    def _build_executed_message(
        cls,
        retry_result: MockOrderRetryResult,
        recommendation: TradeRecommendation,
        order_log: OrderLog | None,
    ) -> str:
        message_lines = [
            "모의 주문 재시도 결과",
            "",
            "처리 결과: 재시도 성공",
            f"추천 ID: {retry_result.recommendation_id}",
            f"마켓: {recommendation.market}",
            f"매매 구분: {recommendation.action}",
            (f"실행 시도 번호: {retry_result.attempt_number}"),
        ]

        if order_log is not None:
            message_lines.extend(
                [
                    (
                        "주문 금액: "
                        f"{
                            cls._format_decimal(
                                order_log.amount_krw,
                                decimal_places=2,
                            )
                        }원"
                    ),
                    (
                        "기준 가격: "
                        f"{
                            cls._format_decimal(
                                order_log.price,
                                decimal_places=2,
                            )
                        }원"
                    ),
                    (
                        "모의 수량: "
                        f"{
                            cls._format_decimal(
                                order_log.quantity,
                                decimal_places=10,
                            )
                        }"
                    ),
                ]
            )

        message_lines.extend(
            [
                "",
                "※ 실제 업비트 주문은 실행되지 않았습니다.",
            ]
        )

        return "\n".join(message_lines)

    @staticmethod
    def _build_permanent_failure_message(
        retry_result: MockOrderRetryResult,
        recommendation: TradeRecommendation,
    ) -> str:
        return "\n".join(
            [
                "모의 주문 재시도 결과",
                "",
                "처리 결과: 재시도 불가",
                (f"추천 ID: {retry_result.recommendation_id}"),
                f"마켓: {recommendation.market}",
                f"매매 구분: {recommendation.action}",
                (f"실행 시도 번호: {retry_result.attempt_number}"),
                (f"실패 코드: {retry_result.error_code}"),
                (f"실패 사유: {retry_result.error_message}"),
                "",
                ("※ 해당 요청은 더 이상 자동 재시도되지 않습니다."),
                ("※ 실제 업비트 주문은 실행되지 않았습니다."),
            ]
        )

    @staticmethod
    def _build_exhausted_message(
        retry_result: MockOrderRetryResult,
        recommendation: TradeRecommendation,
    ) -> str:
        return "\n".join(
            [
                "모의 주문 재시도 결과",
                "",
                "처리 결과: 최종 실패",
                (f"추천 ID: {retry_result.recommendation_id}"),
                f"마켓: {recommendation.market}",
                f"매매 구분: {recommendation.action}",
                (f"실행 시도 번호: {retry_result.attempt_number}"),
                (f"실패 코드: {retry_result.error_code}"),
                (f"실패 사유: {retry_result.error_message}"),
                "",
                ("※ 최대 재시도 횟수를 모두 사용했습니다."),
                ("※ 실제 업비트 주문은 실행되지 않았습니다."),
            ]
        )

    @staticmethod
    def _format_decimal(
        value: object | None,
        decimal_places: int,
    ) -> str:
        if value is None:
            return "-"

        decimal_value = Decimal(str(value))

        return f"{decimal_value:,.{decimal_places}f}"

    @staticmethod
    def _build_result(
        retry_result: MockOrderRetryResult,
        notification_status: NotificationResultStatus,
        error_message: str | None = None,
    ) -> MockOrderRetryNotificationResult:
        return MockOrderRetryNotificationResult(
            recommendation_id=(retry_result.recommendation_id),
            approval_request_id=(retry_result.approval_request_id),
            attempt_id=retry_result.attempt_id,
            retry_status=retry_result.status,
            notification_status=notification_status,
            error_message=error_message,
        )
