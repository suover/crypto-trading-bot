from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import AnalysisRun, TradeRecommendation
from crypto_trading_bot.notification.telegram_client import TelegramClient
from crypto_trading_bot.notification.trade_recommendation_message import (
    build_trade_recommendation_summary_message,
)


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

        message = build_trade_recommendation_summary_message(
            analysis_run=analysis_run,
            recommendations=recommendations,
        )

        self.telegram_client.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
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