from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import AccountSnapshot, AnalysisRun, User
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.pipeline_identity import get_pipeline_run_id


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None

    return Decimal(str(value))


class AccountSnapshotService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def collect_account_snapshots(
        self,
        user_name: str = "Minsu",
        pipeline_run_id: str | None = None,
    ) -> tuple[AnalysisRun, list[AccountSnapshot]]:
        settings = get_settings()

        user = self.session.query(User).filter(User.name == user_name).first()

        if user is None:
            raise ValueError(f"User not found. name={user_name}")

        analysis_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=get_pipeline_run_id(pipeline_run_id),
            run_type="ACCOUNT_SNAPSHOT",
            trading_mode=settings.trading_mode,
            status="STARTED",
        )

        self.session.add(analysis_run)
        self.session.flush()

        try:
            accounts = self.upbit_client.get_accounts()

            snapshots = [
                AccountSnapshot(
                    analysis_run_id=analysis_run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    currency=account["currency"],
                    balance=to_decimal(account.get("balance")),
                    locked=to_decimal(account.get("locked")),
                    avg_buy_price=to_decimal(account.get("avg_buy_price")),
                    raw_data=account,
                )
                for account in accounts
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
