from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"

    database_url: str

    openai_api_key: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    upbit_access_key: str = ""
    upbit_secret_key: str = ""

    trading_mode: str = "AI_APPROVAL"
    max_order_amount_krw: int = 10000
    daily_max_order_amount_krw: int = 30000
    allowed_markets: str = "KRW-BTC,KRW-ETH"

    mock_order_retry_max_retries: int = 3
    mock_order_retry_delays_minutes: str = "5,15,30"

    @property
    def allowed_market_list(self) -> list[str]:
        return [
            market.strip()
            for market in self.allowed_markets.split(",")
            if market.strip()
        ]

    @property
    def mock_order_retry_delay_list(self) -> list[int]:
        delays = [
            int(value.strip())
            for value in self.mock_order_retry_delays_minutes.split(",")
            if value.strip()
        ]

        if not delays:
            raise ValueError(
                "mock_order_retry_delays_minutes must not be empty"
            )

        if any(delay <= 0 for delay in delays):
            raise ValueError(
                "mock order retry delays must be greater than 0"
            )

        return delays

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()