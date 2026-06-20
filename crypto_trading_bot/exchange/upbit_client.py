from typing import Any
from uuid import uuid4

import httpx
import jwt

from crypto_trading_bot.config.settings import get_settings


class UpbitClient:
    BASE_URL = "https://api.upbit.com"

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        if not markets:
            raise ValueError("markets must not be empty")

        response = httpx.get(
            f"{self.BASE_URL}/v1/ticker",
            params={"markets": ",".join(markets)},
            timeout=5.0,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            raise ValueError("Unexpected Upbit ticker response format")

        return data

    def get_accounts(self) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.BASE_URL}/v1/accounts",
            headers={
                "Authorization": self._create_authorization_header(),
                "accept": "application/json",
            },
            timeout=5.0,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            raise ValueError("Unexpected Upbit accounts response format")

        return data

    def _create_authorization_header(self) -> str:
        settings = get_settings()

        if not settings.upbit_access_key or not settings.upbit_secret_key:
            raise ValueError("Upbit API keys are not configured")

        payload = {
            "access_key": settings.upbit_access_key,
            "nonce": str(uuid4()),
        }

        token = jwt.encode(
            payload,
            settings.upbit_secret_key,
            algorithm="HS256",
        )

        return f"Bearer {token}"
    
    def get_minute_candles(
        self,
        market: str,
        unit: int = 15,
        count: int = 50,
    ) -> list[dict[str, Any]]:
        allowed_units = {1, 3, 5, 10, 15, 30, 60, 240}

        if unit not in allowed_units:
            raise ValueError(f"Unsupported minute candle unit. unit={unit}")

        if count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")

        response = httpx.get(
            f"{self.BASE_URL}/v1/candles/minutes/{unit}",
            params={
                "market": market,
                "count": count,
            },
            timeout=5.0,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            raise ValueError("Unexpected Upbit candle response format")

        return data