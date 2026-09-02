from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    openai_trade_model: str = "gpt-5.6-sol"
    openai_reasoning_effort: Literal["", "none", "low", "medium", "high", "xhigh"] = (
        "medium"
    )

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

    # Market universe. STATIC intentionally remains the rollout-safe default.
    market_universe_mode: Literal["STATIC", "DYNAMIC"] = "STATIC"
    market_universe_exchange: str = "UPBIT"
    market_universe_quote_asset: str = "KRW"
    market_universe_top_n: int = 7
    market_universe_prefilter_n: int = 20
    market_universe_min_24h_trade_value_krw: Decimal = Decimal("0")
    market_universe_exclude_warnings: bool = True
    market_universe_exclude_cautions: bool = True
    market_blocklist: str = ""
    analysis_timeframes: str = "15m,60m,240m,1d"
    analysis_candle_count: int = 50
    live_dynamic_market_enabled: bool = False

    live_order_enabled: bool = False
    live_order_confirmation: str = Field(default="", repr=False)
    live_order_chance_preflight_enabled: bool = False

    # Read-only status polling; the worker stays idle in non-LIVE mode.
    live_order_reconciliation_enabled: bool = True
    live_order_reconciliation_interval_seconds: int = Field(default=60, ge=10, le=3600)
    live_order_reconciliation_batch_size: int = Field(default=20, ge=1, le=100)

    # DB-only derived bot execution accounting. Disabled until explicitly rolled out.
    bot_trading_pnl_enabled: bool = False
    bot_trading_pnl_interval_seconds: int = Field(default=300, ge=30, le=86400)

    # DB + Telegram operational alerts. Background worker rollout is opt-in.
    operational_alerting_enabled: bool = False
    operational_alert_interval_seconds: int = Field(default=60, ge=10, le=3600)
    live_order_stale_alert_after_seconds: int = Field(default=600, ge=1, le=604800)
    operational_alert_max_retries: int = Field(default=3, ge=0, le=10)
    operational_alert_retry_delays_minutes: str = "1,5,15"

    # Mock order retry
    mock_order_retry_max_retries: int = 3
    mock_order_retry_delays_minutes: str = "5,15,30"

    # Scheduler
    ai_analysis_scheduler_enabled: bool = True
    ai_analysis_schedule_times: str = "09:00"
    ai_analysis_run_on_startup: bool = False

    @field_validator("market_universe_mode", mode="before")
    @classmethod
    def normalize_market_universe_mode(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

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

        if self.market_universe_top_n <= 0:
            raise ValueError("MARKET_UNIVERSE_TOP_N must be greater than 0")
        if self.market_universe_prefilter_n < self.market_universe_top_n:
            raise ValueError(
                "MARKET_UNIVERSE_PREFILTER_N must be at least MARKET_UNIVERSE_TOP_N"
            )
        if not 1 <= self.analysis_candle_count <= 200:
            raise ValueError("ANALYSIS_CANDLE_COUNT must be between 1 and 200")
        if (
            not self.market_universe_min_24h_trade_value_krw.is_finite()
            or self.market_universe_min_24h_trade_value_krw < 0
        ):
            raise ValueError(
                "MARKET_UNIVERSE_MIN_24H_TRADE_VALUE_KRW must be finite and non-negative"
            )

        # Validate the configured retry schedule during Settings construction rather
        # than waiting for the background worker to start.
        self.operational_alert_retry_delay_list

        return self

    @property
    def allowed_market_list(self) -> list[str]:
        return [
            market.strip()
            for market in self.allowed_markets.split(",")
            if market.strip()
        ]

    @property
    def market_block_list(self) -> list[str]:
        return [
            market.strip().upper()
            for market in self.market_blocklist.split(",")
            if market.strip()
        ]

    @property
    def analysis_timeframe_list(self) -> list[str]:
        aliases = {
            "15m": "15m",
            "60m": "60m",
            "1h": "60m",
            "240m": "240m",
            "4h": "240m",
            "1d": "1d",
            "day": "1d",
        }
        values: list[str] = []
        for raw_value in self.analysis_timeframes.split(","):
            value = raw_value.strip().lower()
            if not value:
                continue
            try:
                normalized = aliases[value]
            except KeyError:
                raise ValueError(
                    f"Unsupported analysis timeframe: {raw_value}"
                ) from None
            if normalized not in values:
                values.append(normalized)
        if not values:
            raise ValueError("analysis_timeframes must not be empty")
        return values

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

    @property
    def operational_alert_retry_delay_list(self) -> list[int]:
        try:
            delays = [
                int(value.strip())
                for value in self.operational_alert_retry_delays_minutes.split(",")
                if value.strip()
            ]
        except ValueError:
            raise ValueError(
                "OPERATIONAL_ALERT_RETRY_DELAYS_MINUTES must contain integers"
            ) from None
        if len(delays) < self.operational_alert_max_retries:
            raise ValueError(
                "OPERATIONAL_ALERT_RETRY_DELAYS_MINUTES must cover max retries"
            )
        if any(delay <= 0 for delay in delays):
            raise ValueError("operational alert retry delays must be greater than 0")
        return delays

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
