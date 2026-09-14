from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.live_ranking_policy_resolver import (
    LiveRankingPolicyResolver,
)
from crypto_trading_bot.services.market_universe_service import MarketUniverseService


def build_market_universe() -> None:
    with SessionLocal() as session:
        settings = get_settings()
        with LiveRankingPolicyResolver(session, settings=settings).resolve() as lease:
            result = MarketUniverseService(
                session,
                settings=settings,
                ranking_policy=lease.ranking_policy,
                canary_run_reserver=lease.reserve_run,
            ).build_and_persist()
        print(
            "Market universe saved. "
            f"analysis_run_id={result.analysis_run.id}, "
            f"pipeline_run_id={result.analysis_run.pipeline_run_id}, "
            f"candidate_count={len(result.candidates)}, "
            f"ranking_policy_mode={lease.resolution.mode}"
        )


if __name__ == "__main__":
    from crypto_trading_bot.operational.error_reporting import (
        run_with_operational_error_reporting,
    )

    run_with_operational_error_reporting(build_market_universe)
