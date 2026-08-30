from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.market_universe_service import MarketUniverseService


def build_market_universe() -> None:
    with SessionLocal() as session:
        result = MarketUniverseService(session).build_and_persist()
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
