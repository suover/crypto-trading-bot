from openai import OpenAI

from crypto_trading_bot.config.settings import get_settings


def check_openai_connection() -> None:
    settings = get_settings()

    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.openai_api_key)
    reasoning_options = (
        {"reasoning": {"effort": settings.openai_reasoning_effort}}
        if settings.openai_reasoning_effort
        else {}
    )

    response = client.responses.create(
        model=settings.openai_trade_model,
        **reasoning_options,
        input="Reply with exactly this text: OPENAI_CONNECTION_OK",
    )

    print(response.output_text)


if __name__ == "__main__":
    check_openai_connection()
