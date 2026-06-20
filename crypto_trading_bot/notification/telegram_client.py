from typing import Any

import httpx

from crypto_trading_bot.config.settings import get_settings


class TelegramClient:
    BASE_URL = "https://api.telegram.org"

    def __init__(self, bot_token: str | None = None) -> None:
        settings = get_settings()
        self.bot_token = bot_token or settings.telegram_bot_token

        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

    def get_updates(self) -> list[dict[str, Any]]:
        response = httpx.get(
            self._build_url("getUpdates"),
            timeout=10.0,
        )
        response.raise_for_status()

        payload = response.json()

        if not payload.get("ok"):
            raise ValueError(f"Telegram getUpdates failed. payload={payload}")

        result = payload.get("result")

        if not isinstance(result, list):
            raise ValueError("Unexpected Telegram getUpdates response format")

        return result

    def send_message(
        self,
        chat_id: str | int,
        text: str,
    ) -> dict[str, Any]:
        response = httpx.post(
            self._build_url("sendMessage"),
            json={
                "chat_id": str(chat_id),
                "text": text,
            },
            timeout=10.0,
        )
        response.raise_for_status()

        payload = response.json()

        if not payload.get("ok"):
            raise ValueError(f"Telegram sendMessage failed. payload={payload}")

        result = payload.get("result")

        if not isinstance(result, dict):
            raise ValueError("Unexpected Telegram sendMessage response format")

        return result

    def _build_url(self, method: str) -> str:
        return f"{self.BASE_URL}/bot{self.bot_token}/{method}"