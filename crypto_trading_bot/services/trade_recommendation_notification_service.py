from datetime import UTC, datetime
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    TradeRecommendation,
)
from crypto_trading_bot.notification.approval_request_message import (
    build_approval_request_message,
    build_approval_request_reply_markup,
)
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.notification.trade_recommendation_message import (
    build_trade_recommendation_summary_message,
)
from crypto_trading_bot.services.approval_request_service import (
    ApprovalRequestService,
)


SUPPORTED_APPROVAL_ACTIONS = {"BUY", "SELL"}


class TradeRecommendationNotificationService:
    def __init__(
        self,
        session: Session,
        telegram_client: TelegramClient | None = None,
    ) -> None:
        self.session = session
        self.telegram_client = telegram_client or TelegramClient()

    def send_latest_ai_recommendation_summary(self) -> tuple[AnalysisRun, int]:
        settings = get_settings()

        if not settings.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is not configured")

        analysis_run = self._get_latest_ai_analysis_run()
        recommendations = self._get_recommendations(analysis_run.id)

        if not recommendations:
            raise ValueError(
                f"No trade recommendations found. analysis_run_id={analysis_run.id}"
            )

        # 모든 분석 대상의 BUY/SELL/HOLD 판단과 사유를 요약해서 먼저 전송
        summary_message = build_trade_recommendation_summary_message(
            analysis_run=analysis_run,
            recommendations=recommendations,
        )

        self.telegram_client.send_message(
            chat_id=settings.telegram_chat_id,
            text=summary_message,
        )

        approval_request_service = ApprovalRequestService(self.session)

        expired_request_count = approval_request_service.expire_pending_requests()

        if expired_request_count > 0:
            print(
                "Expired pending approval requests before notification. "
                f"count={expired_request_count}"
            )

        # 실제 행동이 필요한 BUY/SELL 추천에만 개별 승인 요청 전송
        for recommendation in recommendations:
            action = recommendation.action.strip().upper()

            if action not in SUPPORTED_APPROVAL_ACTIONS:
                continue

            active_pending_request = (
                approval_request_service.get_active_pending_request_for_market(
                    recommendation=recommendation,
                )
            )

            if (
                active_pending_request is not None
                and active_pending_request.recommendation_id != recommendation.id
            ):
                recommendation.status = "APPROVAL_SKIPPED_DUPLICATE"

                self.session.commit()

                print(
                    "Approval request skipped because active pending request exists. "
                    f"recommendation_id={recommendation.id}, "
                    f"active_approval_request_id={active_pending_request.id}, "
                    f"active_recommendation_id="
                    f"{active_pending_request.recommendation_id}, "
                    f"exchange={recommendation.exchange}, "
                    f"market={recommendation.market}, "
                    f"action={recommendation.action}"
                )

                continue

            approval_request, _ = (
                approval_request_service.get_or_create_pending_request(
                    recommendation=recommendation,
                    telegram_chat_id=settings.telegram_chat_id,
                )
            )

            # 이미 텔레그램 메시지까지 발송된 PENDING 요청이면 중복 전송하지 않음
            if approval_request.telegram_message_id is not None:
                continue

            expires_in_minutes = self._get_remaining_minutes(
                approval_request=approval_request,
            )

            approval_message = build_approval_request_message(
                recommendation=recommendation,
                expires_in_minutes=expires_in_minutes,
            )

            reply_markup = build_approval_request_reply_markup(
                action=recommendation.action,
                callback_token=approval_request.callback_token,
            )

            telegram_result = self.telegram_client.send_message(
                chat_id=settings.telegram_chat_id,
                text=approval_message,
                reply_markup=reply_markup,
            )

            telegram_message_id = telegram_result.get("message_id")

            if not isinstance(telegram_message_id, int):
                raise ValueError(
                    "Telegram response does not contain a valid message_id. "
                    f"result={telegram_result}"
                )

            approval_request_service.save_telegram_message_id(
                approval_request=approval_request,
                telegram_message_id=telegram_message_id,
            )

        return analysis_run, len(recommendations)

    def _get_latest_ai_analysis_run(self) -> AnalysisRun:
        statement = (
            select(AnalysisRun)
            .where(
                AnalysisRun.run_type == "AI_RECOMMENDATION",
                AnalysisRun.status == "SUCCESS",
            )
            .order_by(AnalysisRun.id.desc())
            .limit(1)
        )

        analysis_run = self.session.scalar(statement)

        if analysis_run is None:
            raise ValueError("No successful AI_RECOMMENDATION analysis run found")

        return analysis_run

    def _get_recommendations(
        self,
        analysis_run_id: int,
    ) -> list[TradeRecommendation]:
        statement = (
            select(TradeRecommendation)
            .where(TradeRecommendation.analysis_run_id == analysis_run_id)
            .order_by(TradeRecommendation.market.asc())
        )

        return list(self.session.scalars(statement))

    @staticmethod
    def _get_remaining_minutes(
        approval_request: ApprovalRequest,
    ) -> int:
        remaining_seconds = (
            approval_request.expires_at - datetime.now(UTC)
        ).total_seconds()

        if remaining_seconds <= 0:
            raise ValueError(
                "Approval request has already expired. "
                f"approval_request_id={approval_request.id}"
            )

        return max(1, ceil(remaining_seconds / 60))
