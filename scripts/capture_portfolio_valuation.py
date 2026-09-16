from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.services.portfolio_valuation_service import (
    PortfolioValuationService,
)
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


def capture_portfolio_valuation() -> None:
    with SessionLocal() as session:
        user = RuntimeUserResolver(session).resolve_configured(
            get_settings().trading_user_id
        )
        result = PortfolioValuationService(session).capture(user.id)
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
    from crypto_trading_bot.operational.error_reporting import (
        run_with_operational_error_reporting,
    )

    run_with_operational_error_reporting(capture_portfolio_valuation)
