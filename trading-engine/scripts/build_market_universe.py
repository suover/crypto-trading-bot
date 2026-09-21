from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.market_universe_service import MarketUniverseService
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


def build_market_universe() -> None:
    with SessionLocal() as session:
        settings = get_settings()
        user = RuntimeUserResolver(session).resolve_configured(settings.trading_user_id)
        exchange = settings.market_universe_exchange.strip().upper()
        quote_asset = settings.market_universe_quote_asset.strip().upper()
        resolution = LiveRankingPolicyResolver(session, settings=settings).inspect(
            user_id=user.id,
            exchange=exchange,
            quote_asset=quote_asset,
        )
        result = MarketUniverseService(
            session,
            settings=settings,
            policy_resolution=resolution,
        ).build_and_persist(user.id)
        print(
            "Market universe saved. "
            f"analysis_run_id={result.analysis_run.id}, "
            f"pipeline_run_id={result.analysis_run.pipeline_run_id}, "
            f"candidate_count={len(result.candidates)}"
        )


if __name__ == "__main__":
    from crypto_trading_bot.operational.error_reporting import (
        run_with_operational_error_reporting,
    )

    run_with_operational_error_reporting(build_market_universe)
