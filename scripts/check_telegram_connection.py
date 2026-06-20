from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.notification.telegram_client import TelegramClient


def check_telegram_connection() -> None:
    settings = get_settings()

    if not settings.telegram_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is not configured")

    client = TelegramClient()

    client.send_message(
        chat_id=settings.telegram_chat_id,
        text="TELEGRAM_CONNECTION_OK",
    )

    print("TELEGRAM_CONNECTION_OK")


if __name__ == "__main__":
    check_telegram_connection()