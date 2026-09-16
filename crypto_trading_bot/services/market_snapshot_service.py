from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import AnalysisRun, MarketSnapshot
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)
from crypto_trading_bot.services.pipeline_identity import get_pipeline_run_id
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None

    return Decimal(str(value))


class MarketSnapshotService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
        registry_service: ExchangeMarketRegistryService | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()
        self.registry_service = registry_service or ExchangeMarketRegistryService(
            session
        )

    def collect_market_snapshots(
        self,
        user_id: int,
        pipeline_run_id: str | None = None,
    ) -> tuple[AnalysisRun, list[MarketSnapshot]]:
        settings = get_settings()
        user = RuntimeUserResolver(self.session).resolve(user_id)

        analysis_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=get_pipeline_run_id(pipeline_run_id),
            run_type="MANUAL",
            trading_mode=settings.trading_mode,
            status="STARTED",
        )

        self.session.add(analysis_run)
        self.session.flush()

        try:
            active_markets = (
                self.registry_service.load_allowed_active_markets_for_exchange("UPBIT")
            )
            tickers = self.upbit_client.get_tickers(
                [registry_market.market for registry_market in active_markets]
            )

            snapshots = [
                MarketSnapshot(
                    analysis_run_id=analysis_run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=ticker["market"],
                    current_price=to_decimal(ticker.get("trade_price")),
                    change_rate=to_decimal(ticker.get("signed_change_rate")),
                    volume_24h=to_decimal(ticker.get("acc_trade_price_24h")),
                    raw_data=ticker,
                )
                for ticker in tickers
            ]

            self.session.add_all(snapshots)

            analysis_run.status = "SUCCESS"
            analysis_run.finished_at = datetime.now(UTC)

            self.session.commit()

            return analysis_run, snapshots

        except Exception as error:
            analysis_run.status = "FAILED"
            analysis_run.error_message = str(error)
            analysis_run.finished_at = datetime.now(UTC)

            self.session.commit()

            raise
