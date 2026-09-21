from typing import Any

import httpx

from crypto_trading_bot.config.settings import Settings, get_settings


class CoinGeckoClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def get_markets(
        self, coin_ids: list[str], *, vs_currency: str = "usd"
    ) -> list[dict[str, Any]]:
        if not coin_ids:
            raise ValueError("coin_ids must not be empty")
        normalized_currency = vs_currency.strip().lower()
        if not normalized_currency:
            raise ValueError("vs_currency must not be empty")
        unique_coin_ids = list(dict.fromkeys(coin_ids))
        base_url = self.settings.coingecko_api_base_url.rstrip("/")
        headers: dict[str, str] | None = None
        if self.settings.coingecko_api_key:
            header_name = (
                "x-cg-pro-api-key"
                if "pro-api.coingecko.com" in base_url
                else "x-cg-demo-api-key"
            )
            headers = {header_name: self.settings.coingecko_api_key}

        response = httpx.get(
            f"{base_url}/coins/markets",
            params={
                "vs_currency": normalized_currency,
                "ids": ",".join(unique_coin_ids),
                "price_change_percentage": "1h,24h,7d,30d",
                "sparkline": "false",
            },
            headers=headers,
            timeout=self.settings.coingecko_request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Unexpected CoinGecko markets response format")
        return data
