from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.market_data.coingecko_client import CoinGeckoClient
from crypto_trading_bot.market_data.fear_greed_client import FearGreedClient


@dataclass(frozen=True)
class MarketDataContextResult:
    candidates: list[dict[str, Any]]
    market_sentiment: dict[str, Any]
    external_data_status: dict[str, str]


class MarketDataContextService:
    def __init__(
        self,
        upbit_client: UpbitClient | None = None,
        coingecko_client: CoinGeckoClient | None = None,
        fear_greed_client: FearGreedClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.upbit_client = upbit_client or UpbitClient()
        self.coingecko_client = coingecko_client or CoinGeckoClient(self.settings)
        self.fear_greed_client = fear_greed_client or FearGreedClient(self.settings)

    def enrich_candidates(
        self, candidates: list[dict[str, Any]]
    ) -> MarketDataContextResult:
        enriched = [candidate.copy() for candidate in candidates]
        statuses = dict.fromkeys(
            ("upbit_orderbook", "coingecko", "fear_greed"), "DISABLED"
        )
        if not enriched:
            return MarketDataContextResult(
                [],
                {"available": False, "reason": "no_candidates"},
                statuses,
            )

        self._add_orderbooks(enriched, statuses)
        self._add_global_markets(enriched, statuses)
        sentiment = self._get_sentiment(statuses)
        return MarketDataContextResult(enriched, sentiment, statuses)

    def _add_orderbooks(
        self, candidates: list[dict[str, Any]], statuses: dict[str, str]
    ) -> None:
        if not self.settings.upbit_orderbook_enabled:
            self._set_all(candidates, "orderbook", self._disabled())
            return
        markets = list(
            dict.fromkeys(
                value
                for candidate in candidates
                if isinstance((value := candidate.get("market")), str) and value
            )
        )
        if not markets:
            statuses["upbit_orderbook"] = "UNAVAILABLE"
            self._set_all(
                candidates,
                "orderbook",
                {"available": False, "reason": "market_not_returned"},
            )
            return
        try:
            rows = self.upbit_client.get_orderbooks(
                markets, count=self.settings.upbit_orderbook_count
            )
            mapped = {
                row.get("market"): row
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("market"), str)
            }
            statuses["upbit_orderbook"] = "AVAILABLE"
            for candidate in candidates:
                row = mapped.get(candidate.get("market"))
                candidate["orderbook"] = (
                    self._normalize_orderbook(row)
                    if row is not None
                    else {"available": False, "reason": "market_not_returned"}
                )
        except Exception as error:
            statuses["upbit_orderbook"] = "UNAVAILABLE"
            self._set_all(candidates, "orderbook", self._failure(error))

    def _add_global_markets(
        self, candidates: list[dict[str, Any]], statuses: dict[str, str]
    ) -> None:
        if not self.settings.coingecko_enabled:
            self._set_all(candidates, "global_market", self._disabled())
            return
        coin_ids = list(
            dict.fromkeys(
                value
                for candidate in candidates
                if isinstance((value := candidate.get("coingecko_id")), str) and value
            )
        )
        if not coin_ids:
            statuses["coingecko"] = "AVAILABLE"
            self._set_all(
                candidates,
                "global_market",
                {"available": False, "reason": "missing_coingecko_id"},
            )
            return
        try:
            rows = self.coingecko_client.get_markets(coin_ids)
            mapped = {
                row.get("id"): row
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            }
            statuses["coingecko"] = "AVAILABLE"
            for candidate in candidates:
                coin_id = candidate.get("coingecko_id")
                if not isinstance(coin_id, str) or not coin_id:
                    value = {"available": False, "reason": "missing_coingecko_id"}
                elif coin_id not in mapped:
                    value = {"available": False, "reason": "coin_not_returned"}
                else:
                    value = self._normalize_coingecko(mapped[coin_id])
                candidate["global_market"] = value
        except Exception as error:
            statuses["coingecko"] = "UNAVAILABLE"
            self._set_all(candidates, "global_market", self._failure(error))

    def _get_sentiment(self, statuses: dict[str, str]) -> dict[str, Any]:
        if not self.settings.fear_greed_enabled:
            return self._disabled()
        try:
            result = self.fear_greed_client.get_latest()
            statuses["fear_greed"] = "AVAILABLE"
            return result
        except Exception as error:
            statuses["fear_greed"] = "UNAVAILABLE"
            return self._failure(error)

    def _failure(self, error: Exception) -> dict[str, Any]:
        message = str(error)
        for secret in (
            self.settings.coingecko_api_key,
            self.settings.upbit_access_key,
            self.settings.upbit_secret_key,
        ):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return {
            "available": False,
            "error_type": type(error).__name__,
            "error": message,
        }

    @staticmethod
    def _disabled() -> dict[str, Any]:
        return {"available": False, "reason": "disabled"}

    @staticmethod
    def _set_all(
        candidates: list[dict[str, Any]], key: str, value: dict[str, Any]
    ) -> None:
        for candidate in candidates:
            candidate[key] = value.copy()

    @staticmethod
    def _normalize_orderbook(row: dict[str, Any]) -> dict[str, Any]:
        try:
            units = row.get("orderbook_units")
            if (
                not isinstance(units, list)
                or not units
                or not isinstance(units[0], dict)
            ):
                raise ValueError("missing best orderbook unit")
            bid = Decimal(str(units[0]["bid_price"]))
            ask = Decimal(str(units[0]["ask_price"]))
            bid_size = Decimal(str(row["total_bid_size"]))
            ask_size = Decimal(str(row["total_ask_size"]))
            spread = ask - bid
            spread_rate = spread / ask if ask > 0 else None
            ratio = bid_size / ask_size if ask_size > 0 else None
            if ratio is None:
                pressure = "UNKNOWN"
            elif ratio >= Decimal("1.20"):
                pressure = "BUY_PRESSURE"
            elif ratio <= Decimal("0.80"):
                pressure = "SELL_PRESSURE"
            else:
                pressure = "BALANCED"
            timestamp = row.get("timestamp")
            return {
                "available": True,
                "timestamp": int(timestamp) if timestamp is not None else None,
                "best_bid_price": str(bid),
                "best_ask_price": str(ask),
                "spread": str(spread),
                "spread_rate": str(spread_rate) if spread_rate is not None else None,
                "total_bid_size": str(bid_size),
                "total_ask_size": str(ask_size),
                "bid_ask_size_ratio": str(ratio) if ratio is not None else None,
                "pressure_label": pressure,
            }
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            return {
                "available": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }

    @staticmethod
    def _normalize_coingecko(row: dict[str, Any]) -> dict[str, Any]:
        def number(key: str) -> str | None:
            value = row.get(key)
            return str(value) if value is not None else None

        rank = row.get("market_cap_rank")
        return {
            "available": True,
            "id": str(row["id"]),
            "symbol": str(row["symbol"]) if row.get("symbol") is not None else None,
            "market_cap_rank": int(rank) if rank is not None else None,
            "current_price_usd": number("current_price"),
            "market_cap_usd": number("market_cap"),
            "total_volume_usd": number("total_volume"),
            "price_change_percentage_1h": number(
                "price_change_percentage_1h_in_currency"
            ),
            "price_change_percentage_24h": number(
                "price_change_percentage_24h_in_currency"
            ),
            "price_change_percentage_7d": number(
                "price_change_percentage_7d_in_currency"
            ),
            "price_change_percentage_30d": number(
                "price_change_percentage_30d_in_currency"
            ),
            "last_updated": (
                str(row["last_updated"])
                if row.get("last_updated") is not None
                else None
            ),
        }
