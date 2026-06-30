from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.trade_recommendation_notification_service import (
    TradeRecommendationNotificationService,
)


def send_latest_ai_trade_recommendations() -> None:
    with SessionLocal() as session:
        service = TradeRecommendationNotificationService(session)

        analysis_run, recommendation_count = (
            service.send_latest_ai_recommendation_summary()
        )

        print(
            f"AI trade recommendation notification sent. "
            f"analysis_run_id={analysis_run.id}, "
            f"count={recommendation_count}"
        )


if __name__ == "__main__":
    send_latest_ai_trade_recommendations()
