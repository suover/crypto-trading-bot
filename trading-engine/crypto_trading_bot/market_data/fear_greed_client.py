from typing import Any

import httpx

from crypto_trading_bot.config.settings import Settings, get_settings


class FearGreedClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def get_latest(self) -> dict[str, Any]:
        response = httpx.get(
            f"{self.settings.fear_greed_api_base_url.rstrip('/')}/fng/",
            params={"limit": 1, "format": "json"},
            timeout=self.settings.fear_greed_request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Fear & Greed response format")
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and metadata.get("error"):
            raise ValueError(f"Fear & Greed API error: {metadata['error']}")
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("Fear & Greed response data must be a non-empty list")
        latest = data[0]
        if not isinstance(latest, dict):
            raise ValueError("Unexpected Fear & Greed data item format")
        time_until_update = latest.get("time_until_update")
        return {
            "available": True,
            "source": "alternative_me_fear_greed",
            "value": int(latest["value"]),
            "value_classification": str(latest["value_classification"]),
            "timestamp": int(latest["timestamp"]),
            "time_until_update": (
                int(time_until_update) if time_until_update not in (None, "") else None
            ),
            "attribution": "alternative.me",
        }
