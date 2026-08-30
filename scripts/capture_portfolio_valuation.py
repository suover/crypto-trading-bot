from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.portfolio_valuation_service import (
    PortfolioValuationService,
)


def capture_portfolio_valuation() -> None:
    with SessionLocal() as session:
        result = PortfolioValuationService(session).capture()
        snapshot = result.portfolio_snapshot
        print(
            "Portfolio valuation saved. "
            f"portfolio_snapshot_id={snapshot.id}, "
            f"analysis_run_id={result.analysis_run.id}, "
            f"pipeline_run_id={snapshot.pipeline_run_id}, "
            f"valuation_status={snapshot.valuation_status}, "
            f"position_count={snapshot.position_count}, "
            f"unpriced_asset_count={snapshot.unpriced_asset_count}, "
            f"already_captured={result.already_captured}"
        )


if __name__ == "__main__":
    capture_portfolio_valuation()
