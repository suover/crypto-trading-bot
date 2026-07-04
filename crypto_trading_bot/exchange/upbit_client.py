from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha512
from typing import Any
from urllib.parse import urlencode, unquote
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

    def create_market_buy_order(
        self,
        market: str,
        amount_krw: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        if not market.strip():
            raise ValueError("market must not be empty")

        if amount_krw <= 0:
            raise ValueError(
                f"amount_krw must be greater than 0. amount_krw={amount_krw}"
            )

        body: dict[str, str] = {
            "market": market,
            "side": "bid",
            "price": self._format_decimal(amount_krw),
            "ord_type": "price",
        }

        if identifier:
            body["identifier"] = identifier

        return self._create_order(body=body)

    def create_market_sell_order(
        self,
        market: str,
        quantity: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        if not market.strip():
            raise ValueError("market must not be empty")

        if quantity <= 0:
            raise ValueError(f"quantity must be greater than 0. quantity={quantity}")

        body: dict[str, str] = {
            "market": market,
            "side": "ask",
            "volume": self._format_decimal(quantity),
            "ord_type": "market",
        }

        if identifier:
            body["identifier"] = identifier

        return self._create_order(body=body)

    def _create_order(
        self,
        body: dict[str, str],
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.BASE_URL}/v1/orders",
            json=body,
            headers={
                "Authorization": self._create_authorization_header(body),
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            timeout=5.0,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict):
            raise ValueError("Unexpected Upbit order response format")

        return data

    def _create_authorization_header(
        self,
        params: Mapping[str, object] | None = None,
    ) -> str:
        settings = get_settings()

        if not settings.upbit_access_key or not settings.upbit_secret_key:
            raise ValueError("Upbit API keys are not configured")

        payload = {
            "access_key": settings.upbit_access_key,
            "nonce": str(uuid4()),
        }

        query_string = self._build_query_string(params)

        if query_string:
            payload["query_hash"] = sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(
            payload,
            settings.upbit_secret_key,
            algorithm="HS512",
        )

        return f"Bearer {token}"

    @staticmethod
    def _build_query_string(
        params: Mapping[str, object] | None,
    ) -> str:
        if not params:
            return ""

        return unquote(urlencode(params))

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

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value.normalize(), "f")
