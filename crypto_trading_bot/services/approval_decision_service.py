from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import ApprovalRequest, TradeRecommendation


ApprovalDecision = Literal["APPROVE", "REJECT"]

SUPPORTED_DECISIONS = {"APPROVE", "REJECT"}


@dataclass(frozen=True)
class ApprovalDecisionResult:
    approval_request: ApprovalRequest
    recommendation: TradeRecommendation
    decision: ApprovalDecision
    already_processed: bool


class ApprovalDecisionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def process_decision(
        self,
        callback_token: str,
        decision: str,
        telegram_chat_id: str | int,
        telegram_message_id: int,
    ) -> ApprovalDecisionResult:
        normalized_token = callback_token.strip()
        normalized_decision = self._normalize_decision(decision)
        normalized_chat_id = self._normalize_chat_id(telegram_chat_id)

        if not normalized_token:
            raise ValueError("callback_token must not be empty")

        if telegram_message_id <= 0:
            raise ValueError("telegram_message_id must be greater than 0")

        approval_request = self._get_approval_request_for_update(
            callback_token=normalized_token,
        )

        if approval_request is None:
            raise ValueError("Approval request not found")

        recommendation = self.session.get(
            TradeRecommendation,
            approval_request.recommendation_id,
        )

        if recommendation is None:
            raise ValueError(
                "Trade recommendation not found. "
                f"recommendation_id={approval_request.recommendation_id}"
            )

        self._validate_telegram_source(
            approval_request=approval_request,
            telegram_chat_id=normalized_chat_id,
            telegram_message_id=telegram_message_id,
        )

        existing_result = self._handle_already_processed_request(
            approval_request=approval_request,
            recommendation=recommendation,
            decision=normalized_decision,
        )

        if existing_result is not None:
            return existing_result

        if approval_request.status != "PENDING":
            raise ValueError(
                "Approval request is not pending. "
                f"status={approval_request.status}"
            )

        now = datetime.now(UTC)

        if approval_request.expires_at <= now:
            approval_request.status = "EXPIRED"
            recommendation.status = "APPROVAL_EXPIRED"

            self.session.commit()

            raise ValueError(
                "Approval request has expired. "
                f"approval_request_id={approval_request.id}"
            )

        if normalized_decision == "APPROVE":
            approval_request.status = "APPROVED"
            approval_request.approved_at = now
            approval_request.rejected_at = None

            recommendation.status = "APPROVED"
        else:
            approval_request.status = "REJECTED"
            approval_request.rejected_at = now
            approval_request.approved_at = None

            recommendation.status = "REJECTED"

        self.session.commit()
        self.session.refresh(approval_request)
        self.session.refresh(recommendation)

        return ApprovalDecisionResult(
            approval_request=approval_request,
            recommendation=recommendation,
            decision=normalized_decision,
            already_processed=False,
        )

    def _get_approval_request_for_update(
        self,
        callback_token: str,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(ApprovalRequest.callback_token == callback_token)
            .with_for_update()
        )

        return self.session.scalar(statement)

    @staticmethod
    def _validate_telegram_source(
        approval_request: ApprovalRequest,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> None:
        if approval_request.telegram_chat_id != telegram_chat_id:
            raise ValueError(
                "Telegram chat ID does not match approval request"
            )

        if approval_request.telegram_message_id is None:
            raise ValueError(
                "Approval request does not have a Telegram message ID"
            )

        if approval_request.telegram_message_id != telegram_message_id:
            raise ValueError(
                "Telegram message ID does not match approval request"
            )

    @staticmethod
    def _handle_already_processed_request(
        approval_request: ApprovalRequest,
        recommendation: TradeRecommendation,
        decision: ApprovalDecision,
    ) -> ApprovalDecisionResult | None:
        expected_status = (
            "APPROVED" if decision == "APPROVE" else "REJECTED"
        )

        if approval_request.status == expected_status:
            return ApprovalDecisionResult(
                approval_request=approval_request,
                recommendation=recommendation,
                decision=decision,
                already_processed=True,
            )

        if approval_request.status in {"APPROVED", "REJECTED"}:
            raise ValueError(
                "Approval request has already been processed with a "
                "different decision. "
                f"status={approval_request.status}"
            )

        return None

    @staticmethod
    def _normalize_decision(decision: str) -> ApprovalDecision:
        normalized_decision = decision.strip().upper()

        if normalized_decision not in SUPPORTED_DECISIONS:
            raise ValueError(
                "Unsupported approval decision. "
                f"decision={decision}"
            )

        if normalized_decision == "APPROVE":
            return "APPROVE"

        return "REJECT"

    @staticmethod
    def _normalize_chat_id(
        telegram_chat_id: str | int,
    ) -> int:
        try:
            return int(str(telegram_chat_id).strip())
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid Telegram chat ID. value={telegram_chat_id}"
            ) from error