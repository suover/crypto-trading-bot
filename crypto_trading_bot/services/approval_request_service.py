import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import ApprovalRequest, TradeRecommendation


DEFAULT_APPROVAL_EXPIRATION_MINUTES = 30
SUPPORTED_APPROVAL_ACTIONS = {"BUY", "SELL"}


class ApprovalRequestService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_pending_request(
        self,
        recommendation: TradeRecommendation,
        telegram_chat_id: str | int,
        expires_in_minutes: int = DEFAULT_APPROVAL_EXPIRATION_MINUTES,
    ) -> tuple[ApprovalRequest, bool]:
        """
        BUY/SELL 추천에 대한 PENDING 승인 요청을 조회하거나 생성한다.

        반환값:
        - ApprovalRequest: 조회 또는 생성된 승인 요청
        - bool: 이번 호출에서 새로 생성했으면 True, 기존 요청이면 False
        """
        self._validate_recommendation(recommendation)

        if expires_in_minutes <= 0:
            raise ValueError("expires_in_minutes must be greater than 0")

        normalized_chat_id = self._normalize_chat_id(telegram_chat_id)
        now = datetime.now(UTC)

        existing_request = self._get_latest_pending_request(
            recommendation_id=recommendation.id,
        )

        if existing_request is not None:
            if existing_request.expires_at > now:
                existing_request.telegram_chat_id = normalized_chat_id
                recommendation.status = "APPROVAL_PENDING"

                self.session.commit()
                self.session.refresh(existing_request)

                return existing_request, False

            existing_request.status = "EXPIRED"

        approval_request = ApprovalRequest(
            recommendation_id=recommendation.id,
            user_id=recommendation.user_id,
            status="PENDING",
            telegram_chat_id=normalized_chat_id,
            telegram_message_id=None,
            callback_token=secrets.token_urlsafe(18),
            expires_at=now + timedelta(minutes=expires_in_minutes),
            approved_at=None,
            rejected_at=None,
        )

        self.session.add(approval_request)

        recommendation.status = "APPROVAL_PENDING"

        self.session.commit()
        self.session.refresh(approval_request)

        return approval_request, True

    def expire_pending_requests(self) -> int:
        now = datetime.now(UTC)

        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.status == "PENDING",
                ApprovalRequest.expires_at <= now,
            )
            .with_for_update()
        )

        expired_requests = list(self.session.scalars(statement))

        if not expired_requests:
            return 0

        for approval_request in expired_requests:
            approval_request.status = "EXPIRED"

            recommendation = self.session.get(
                TradeRecommendation,
                approval_request.recommendation_id,
            )

            if (
                recommendation is not None
                and recommendation.status == "APPROVAL_PENDING"
            ):
                recommendation.status = "APPROVAL_EXPIRED"

        self.session.commit()

        return len(expired_requests)

    def get_active_pending_request_for_market(
        self,
        recommendation: TradeRecommendation,
    ) -> ApprovalRequest | None:
        """
        같은 사용자/거래소/마켓에 대해 아직 만료되지 않은 PENDING 승인 요청을 조회한다.

        recommendation_id 기준이 아니라 market 기준으로 조회한다.
        새 AI 분석 run에서 같은 마켓의 BUY/SELL 추천이 다시 생성되더라도
        기존 승인 요청이 살아 있으면 중복 승인 요청을 보내지 않기 위함이다.
        """
        self._validate_recommendation(recommendation)

        now = datetime.now(UTC)

        statement = (
            select(ApprovalRequest)
            .join(
                TradeRecommendation,
                ApprovalRequest.recommendation_id == TradeRecommendation.id,
            )
            .where(
                ApprovalRequest.user_id == recommendation.user_id,
                ApprovalRequest.status == "PENDING",
                ApprovalRequest.expires_at > now,
                TradeRecommendation.exchange == recommendation.exchange,
                TradeRecommendation.market == recommendation.market,
                TradeRecommendation.action.in_(SUPPORTED_APPROVAL_ACTIONS),
            )
            .order_by(ApprovalRequest.id.desc())
            .limit(1)
        )

        return self.session.scalar(statement)

    def save_telegram_message_id(
        self,
        approval_request: ApprovalRequest,
        telegram_message_id: int,
    ) -> ApprovalRequest:
        if approval_request.id is None:
            raise ValueError("Approval request must be saved before message ID update")

        if telegram_message_id <= 0:
            raise ValueError("telegram_message_id must be greater than 0")

        approval_request.telegram_message_id = telegram_message_id

        self.session.commit()
        self.session.refresh(approval_request)

        return approval_request

    def _get_latest_pending_request(
        self,
        recommendation_id: int,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.recommendation_id == recommendation_id,
                ApprovalRequest.status == "PENDING",
            )
            .order_by(ApprovalRequest.id.desc())
            .limit(1)
        )

        return self.session.scalar(statement)

    @staticmethod
    def _validate_recommendation(
        recommendation: TradeRecommendation,
    ) -> None:
        if recommendation.id is None:
            raise ValueError(
                "Trade recommendation must be saved before approval request creation"
            )

        action = recommendation.action.strip().upper()

        if action not in SUPPORTED_APPROVAL_ACTIONS:
            raise ValueError(
                "Approval request is only available for BUY or SELL. "
                f"action={recommendation.action}"
            )

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
