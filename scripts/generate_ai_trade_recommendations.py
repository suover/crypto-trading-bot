from decimal import Decimal

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.ai_trade_recommendation_service import (
    AiTradeRecommendationService,
)


def format_decimal(value: Decimal | None, digit_count: int = 4) -> str:
    if value is None:
        return "-"

    return f"{value:,.{digit_count}f}"


def generate_ai_trade_recommendations() -> None:
    with SessionLocal() as session:
        service = AiTradeRecommendationService(session)

        analysis_run, recommendations = service.create_ai_recommendations()

        print(
            f"AI trade recommendations saved. "
            f"analysis_run_id={analysis_run.id}, "
            f"count={len(recommendations)}"
        )

        for recommendation in recommendations:
            print("=" * 60)
            print(f"market: {recommendation.market}")
            print(f"action: {recommendation.action}")
            print(f"confidence: {format_decimal(recommendation.confidence)}")
            print(
                "recommended_amount_krw: "
                f"{format_decimal(recommendation.recommended_amount_krw, 2)}"
            )
            print(
                "recommended_quantity: "
                f"{format_decimal(recommendation.recommended_quantity, 10)}"
            )
            print(f"ai_model: {recommendation.ai_model}")
            print(f"reason: {recommendation.reason}")


if __name__ == "__main__":
    generate_ai_trade_recommendations()
