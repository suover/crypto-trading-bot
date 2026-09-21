from decimal import Decimal

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver
from crypto_trading_bot.services.trade_recommendation_service import (
    TradeRecommendationService,
)


def format_decimal(value: Decimal | None, digit_count: int = 4) -> str:
    if value is None:
        return "-"

    return f"{value:,.{digit_count}f}"


def generate_trade_recommendations() -> None:
    with SessionLocal() as session:
        user = RuntimeUserResolver(session).resolve_configured(
            get_settings().trading_user_id
        )
        service = TradeRecommendationService(session)
        analysis_run, recommendations = service.create_recommendations(user.id)

        print(
            f"Trade recommendations saved. "
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
            print(f"reason: {recommendation.reason}")


if __name__ == "__main__":
    generate_trade_recommendations()
