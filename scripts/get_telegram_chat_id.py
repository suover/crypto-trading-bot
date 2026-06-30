from typing import Any

from crypto_trading_bot.notification.telegram_client import TelegramClient


def get_chat_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    message = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
    )

    if not isinstance(message, dict):
        return None

    chat = message.get("chat")

    if not isinstance(chat, dict):
        return None

    return chat


def get_chat_name(chat: dict[str, Any]) -> str:
    title = chat.get("title")
    username = chat.get("username")
    first_name = chat.get("first_name")
    last_name = chat.get("last_name")

    return " ".join(
        str(value) for value in [title, username, first_name, last_name] if value
    )


def get_telegram_chat_id() -> None:
    client = TelegramClient()
    updates = client.get_updates()

    if not updates:
        print("No Telegram updates found.")
        print("Open your bot in Telegram and send /start, then run this again.")
        return

    printed_chat_ids: set[int] = set()

    for update in updates:
        chat = get_chat_from_update(update)

        if chat is None:
            continue

        chat_id = chat.get("id")

        if not isinstance(chat_id, int):
            continue

        if chat_id in printed_chat_ids:
            continue

        printed_chat_ids.add(chat_id)

        print(
            f"chat_id={chat_id} | type={chat.get('type')} | name={get_chat_name(chat)}"
        )


if __name__ == "__main__":
    get_telegram_chat_id()
