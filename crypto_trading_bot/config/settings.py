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
    upbit_orderbook_enabled: bool = True
    upbit_orderbook_count: int = 15

    coingecko_enabled: bool = True
    coingecko_api_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str = ""
    coingecko_request_timeout_seconds: float = 5.0

    fear_greed_enabled: bool = True
    fear_greed_api_base_url: str = "https://api.alternative.me"
    fear_greed_request_timeout_seconds: float = 5.0

    trading_mode: str = "AI_APPROVAL"
    order_execution_mode: str = "MOCK"
    max_order_amount_krw: int = 10000
    daily_max_order_amount_krw: int = 30000
    allowed_markets: str = "KRW-BTC,KRW-ETH"

    live_order_enabled: bool = False
    live_order_confirmation: str = ""

    mock_order_retry_max_retries: int = 3
    mock_order_retry_delays_minutes: str = "5,15,30"

    ai_analysis_scheduler_enabled: bool = True
    ai_analysis_schedule_times: str = "09:00"
    ai_analysis_run_on_startup: bool = False

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
            raise ValueError("mock_order_retry_delays_minutes must not be empty")

        if any(delay <= 0 for delay in delays):
            raise ValueError("mock order retry delays must be greater than 0")

        return delays

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
