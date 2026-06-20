from typing import Any

import httpx


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