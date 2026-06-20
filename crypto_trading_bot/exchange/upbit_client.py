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