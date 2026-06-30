from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import AnalysisRun, MarketSnapshot, User
from crypto_trading_bot.exchange.upbit_client import UpbitClient


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None

    return Decimal(str(value))


class MarketSnapshotService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def collect_market_snapshots(
        self,
        user_name: str = "Minsu",
    ) -> tuple[AnalysisRun, list[MarketSnapshot]]:
        settings = get_settings()

        user = self.session.query(User).filter(User.name == user_name).first()

        if user is None:
            raise ValueError(f"User not found. name={user_name}")

        analysis_run = AnalysisRun(
            user_id=user.id,
            run_type="MANUAL",
            trading_mode=settings.trading_mode,
            status="STARTED",
        )

        self.session.add(analysis_run)
        self.session.flush()

        try:
            tickers = self.upbit_client.get_tickers(settings.allowed_market_list)

            snapshots = [
                MarketSnapshot(
                    analysis_run_id=analysis_run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=ticker["market"],
                    current_price=to_decimal(ticker.get("trade_price")),
                    change_rate=to_decimal(ticker.get("signed_change_rate")),
                    volume_24h=to_decimal(ticker.get("acc_trade_volume_24h")),
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
