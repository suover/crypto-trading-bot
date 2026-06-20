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

    @property
    def allowed_market_list(self) -> list[str]:
        return [
            market.strip()
            for market in self.allowed_markets.split(",")
            if market.strip()
        ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()