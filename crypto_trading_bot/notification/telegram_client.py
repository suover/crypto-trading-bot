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

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        if timeout < 0:
            raise ValueError("timeout must be greater than or equal to 0")

        request_data: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query"],
        }

        if offset is not None:
            request_data["offset"] = offset

        result = self._request(
            method="getUpdates",
            request_data=request_data,
            timeout=max(10.0, timeout + 10.0),
        )

        if not isinstance(result, list):
            raise ValueError("Unexpected Telegram getUpdates response format")

        return result

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_data: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
        }

        if reply_markup is not None:
            request_data["reply_markup"] = reply_markup

        result = self._request(
            method="sendMessage",
            request_data=request_data,
        )

        if not isinstance(result, dict):
            raise ValueError("Unexpected Telegram sendMessage response format")

        return result

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        if not callback_query_id.strip():
            raise ValueError("callback_query_id must not be empty")

        request_data: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }

        if text is not None:
            request_data["text"] = text

        result = self._request(
            method="answerCallbackQuery",
            request_data=request_data,
        )

        if result is not True:
            raise ValueError(
                "Unexpected Telegram answerCallbackQuery response format"
            )

        return True

    def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bool:
        if message_id <= 0:
            raise ValueError("message_id must be greater than 0")

        if not text.strip():
            raise ValueError("text must not be empty")

        request_data: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "text": text,
        }

        if reply_markup is not None:
            request_data["reply_markup"] = reply_markup

        result = self._request(
            method="editMessageText",
            request_data=request_data,
        )

        if not isinstance(result, (dict, bool)):
            raise ValueError(
                "Unexpected Telegram editMessageText response format"
            )

        return result

    def edit_message_reply_markup(
        self,
        chat_id: str | int,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bool:
        if message_id <= 0:
            raise ValueError("message_id must be greater than 0")

        request_data: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": message_id,
        }

        if reply_markup is not None:
            request_data["reply_markup"] = reply_markup

        result = self._request(
            method="editMessageReplyMarkup",
            request_data=request_data,
        )

        if not isinstance(result, (dict, bool)):
            raise ValueError(
                "Unexpected Telegram editMessageReplyMarkup response format"
            )

        return result

    def _request(
        self,
        method: str,
        request_data: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> Any:
        response = httpx.post(
            self._build_url(method),
            json=request_data or {},
            timeout=timeout,
        )
        response.raise_for_status()

        payload = response.json()

        if not payload.get("ok"):
            raise ValueError(
                f"Telegram {method} failed. payload={payload}"
            )

        return payload.get("result")

    def _build_url(self, method: str) -> str:
        return f"{self.BASE_URL}/bot{self.bot_token}/{method}"