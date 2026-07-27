from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    app_env: str = "local"

    # Secret file location used by Docker Compose on the host.
    secret_dir: str = ".secrets"

    # PostgreSQL
    database_url: str = Field(default="", repr=False)
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "crypto_trading_bot"
    database_user: str = "trading_user"
    database_password: str = Field(default="", repr=False)
    database_password_file: str = ""

    # OpenAI
    openai_api_key: str = Field(default="", repr=False)
    openai_api_key_file: str = ""

    # Telegram
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_bot_token_file: str = ""
    telegram_chat_id: str = ""

    # Upbit
    upbit_access_key: str = Field(default="", repr=False)
    upbit_access_key_file: str = ""
    upbit_secret_key: str = Field(default="", repr=False)
    upbit_secret_key_file: str = ""

    upbit_orderbook_enabled: bool = True
    upbit_orderbook_count: int = 15

    # CoinGecko
    coingecko_enabled: bool = True
    coingecko_api_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str = Field(default="", repr=False)
    coingecko_request_timeout_seconds: float = 5.0

    # Fear & Greed Index
    fear_greed_enabled: bool = True
    fear_greed_api_base_url: str = "https://api.alternative.me"
    fear_greed_request_timeout_seconds: float = 5.0

    # Trading
    trading_mode: str = "AI_APPROVAL"
    order_execution_mode: str = "MOCK"
    max_order_amount_krw: int = 10000
    daily_max_order_amount_krw: int = 30000
    allowed_markets: str = "KRW-BTC,KRW-ETH"

    live_order_enabled: bool = False
    live_order_confirmation: str = Field(default="", repr=False)

    # Mock order retry
    mock_order_retry_max_retries: int = 3
    mock_order_retry_delays_minutes: str = "5,15,30"

    # Scheduler
    ai_analysis_scheduler_enabled: bool = True
    ai_analysis_schedule_times: str = "09:00"
    ai_analysis_run_on_startup: bool = False

    @staticmethod
    def _read_secret_file(
        file_path: str,
        variable_name: str,
        *,
        required: bool,
    ) -> str:
        path = Path(file_path)

        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"{variable_name}_FILE could not be read: {path}") from exc

        if required and not value:
            raise ValueError(f"{variable_name}_FILE must not be empty")

        return value

    @classmethod
    def _resolve_secret(
        cls,
        *,
        variable_name: str,
        direct_value: str,
        file_path: str,
        required: bool = False,
    ) -> str:
        normalized_file_path = file_path.strip()

        if normalized_file_path:
            return cls._read_secret_file(
                normalized_file_path,
                variable_name,
                required=required,
            )

        normalized_direct_value = direct_value.strip()

        if required and not normalized_direct_value:
            raise ValueError(
                f"{variable_name} or {variable_name}_FILE must be configured"
            )

        return normalized_direct_value

    @model_validator(mode="after")
    def resolve_secret_values(self) -> "Settings":
        self.openai_api_key = self._resolve_secret(
            variable_name="OPENAI_API_KEY",
            direct_value=self.openai_api_key,
            file_path=self.openai_api_key_file,
        )

        self.telegram_bot_token = self._resolve_secret(
            variable_name="TELEGRAM_BOT_TOKEN",
            direct_value=self.telegram_bot_token,
            file_path=self.telegram_bot_token_file,
        )

        self.upbit_access_key = self._resolve_secret(
            variable_name="UPBIT_ACCESS_KEY",
            direct_value=self.upbit_access_key,
            file_path=self.upbit_access_key_file,
        )

        self.upbit_secret_key = self._resolve_secret(
            variable_name="UPBIT_SECRET_KEY",
            direct_value=self.upbit_secret_key,
            file_path=self.upbit_secret_key_file,
        )

        if self.database_password_file.strip() or self.database_password.strip():
            database_password = self._resolve_secret(
                variable_name="DATABASE_PASSWORD",
                direct_value=self.database_password,
                file_path=self.database_password_file,
                required=True,
            )

            self.database_url = URL.create(
                drivername="postgresql+psycopg",
                username=self.database_user,
                password=database_password,
                host=self.database_host,
                port=self.database_port,
                database=self.database_name,
            ).render_as_string(hide_password=False)

        elif not self.database_url.strip():
            raise ValueError(
                "Database configuration requires DATABASE_PASSWORD_FILE or DATABASE_URL"
            )

        return self

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
