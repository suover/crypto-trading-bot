from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_safety import check_live_order_safety
from crypto_trading_bot.services.order_execution_mode import check_order_execution_mode


@dataclass(frozen=True)
class ReadinessCheckItem:
    name: str
    ready: bool
    message: str


@dataclass(frozen=True)
class LiveTradingReadinessReport:
    ready: bool
    items: tuple[ReadinessCheckItem, ...]


class LiveTradingReadinessService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def check(self) -> LiveTradingReadinessReport:
        settings = get_settings()
        ticker_markets = (
            settings.allowed_market_list
            if settings.market_universe_mode == "STATIC"
            else None
        )
        items = (
            self._check_order_execution_mode(settings=settings),
            self._check_live_order_safety(settings=settings),
            self._check_database_connection(),
            self._check_upbit_accounts(),
            self._check_upbit_tickers(
                markets=ticker_markets,
                quote_asset=settings.market_universe_quote_asset,
            ),
        )

        return LiveTradingReadinessReport(
            ready=all(item.ready for item in items),
            items=items,
        )

    @staticmethod
    def _check_order_execution_mode(settings: Any) -> ReadinessCheckItem:
        try:
            check = check_order_execution_mode(settings)
        except Exception as error:
            return ReadinessCheckItem(
                name="order_execution_mode",
                ready=False,
                message=f"error_type={type(error).__name__}, error={error}",
            )

        if check.ready:
            return ReadinessCheckItem(
                name="order_execution_mode",
                ready=True,
                message=f"mode={check.mode}",
            )

        reasons = "; ".join(check.reasons)
        live_safety_reasons = "; ".join(check.live_safety_reasons)
        message_parts = [f"mode={check.mode}"]

        if reasons:
            message_parts.append(f"reasons={reasons}")

        if live_safety_reasons:
            message_parts.append(f"live_safety_reasons={live_safety_reasons}")

        return ReadinessCheckItem(
            name="order_execution_mode",
            ready=False,
            message=", ".join(message_parts),
        )

    @staticmethod
    def _check_live_order_safety(settings: Any) -> ReadinessCheckItem:
        check = check_live_order_safety(settings)

        if check.ready:
            return ReadinessCheckItem(
                name="live_order_safety",
                ready=True,
                message="ready",
            )

        return ReadinessCheckItem(
            name="live_order_safety",
            ready=False,
            message=f"reasons={'; '.join(check.reasons)}",
        )

    def _check_database_connection(self) -> ReadinessCheckItem:
        try:
            self.session.execute(text("SELECT 1"))
        except Exception as error:
            return ReadinessCheckItem(
                name="database_connection",
                ready=False,
                message=f"error_type={type(error).__name__}, error={error}",
            )

        return ReadinessCheckItem(
            name="database_connection",
            ready=True,
            message="connected",
        )

    def _check_upbit_accounts(self) -> ReadinessCheckItem:
        try:
            accounts = self.upbit_client.get_accounts()
        except Exception as error:
            return ReadinessCheckItem(
                name="upbit_accounts",
                ready=False,
                message=f"error_type={type(error).__name__}, error={error}",
            )

        if not isinstance(accounts, list):
            return ReadinessCheckItem(
                name="upbit_accounts",
                ready=False,
                message=f"unexpected_response_type={type(accounts).__name__}",
            )

        return ReadinessCheckItem(
            name="upbit_accounts",
            ready=True,
            message=f"connected, account_count={len(accounts)}",
        )

    def _check_upbit_tickers(
        self, markets: list[str] | None, quote_asset: str = "KRW"
    ) -> ReadinessCheckItem:
        if markets is not None and not markets:
            return ReadinessCheckItem(
                name="upbit_tickers",
                ready=False,
                message="allowed_market_list is empty",
            )

        try:
            tickers = (
                self.upbit_client.get_tickers(markets)
                if markets is not None
                else self.upbit_client.get_all_tickers(quote_asset)
            )
        except Exception as error:
            return ReadinessCheckItem(
                name="upbit_tickers",
                ready=False,
                message=f"error_type={type(error).__name__}, error={error}",
            )

        if not isinstance(tickers, list):
            return ReadinessCheckItem(
                name="upbit_tickers",
                ready=False,
                message=f"unexpected_response_type={type(tickers).__name__}",
            )

        return ReadinessCheckItem(
            name="upbit_tickers",
            ready=True,
            message=f"connected, market_count={len(tickers)}",
        )
