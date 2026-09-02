from collections.abc import Callable, Mapping
from decimal import Decimal
from hashlib import sha512
from time import monotonic, sleep
from typing import Any
from urllib.parse import urlencode, unquote
from uuid import uuid4

import httpx
import jwt

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderAmbiguousError,
    UpbitOrderNotFoundError,
    UpbitOrderOperationError,
    UpbitOrderReadError,
    UpbitOrderRejectedError,
    UpbitSafeError,
)


class UpbitClient:
    BASE_URL = "https://api.upbit.com"

    def __init__(
        self,
        *,
        sleep_fn: Callable[[float], None] = sleep,
        monotonic_fn: Callable[[], float] = monotonic,
        candle_request_interval_seconds: float = 0.12,
        public_429_retries: int = 2,
    ) -> None:
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.candle_request_interval_seconds = max(0.0, candle_request_interval_seconds)
        self.public_429_retries = max(0, public_429_retries)
        self._last_candle_request_at: float | None = None

    def _request_public_list(
        self,
        path: str,
        *,
        params: dict[str, object],
        response_name: str,
        candle_group: bool = False,
    ) -> list[dict[str, Any]]:
        for attempt in range(self.public_429_retries + 1):
            if candle_group:
                self._pace_candle_request()
            response = httpx.get(
                f"{self.BASE_URL}{path}",
                params=params,
                timeout=5.0,
            )
            if getattr(response, "status_code", None) == 429:
                if attempt >= self.public_429_retries:
                    response.raise_for_status()
                headers = getattr(response, "headers", {})
                raw_retry_after = headers.get("Retry-After", "1")
                try:
                    retry_after = max(float(raw_retry_after), 0.1)
                except TypeError, ValueError:
                    retry_after = 1.0
                self.sleep_fn(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected Upbit {response_name} response format")
            return [row for row in data if isinstance(row, dict)]
        raise RuntimeError("Unreachable Upbit public request retry state")

    def _pace_candle_request(self) -> None:
        now = self.monotonic_fn()
        if self._last_candle_request_at is not None:
            elapsed = now - self._last_candle_request_at
            remaining = self.candle_request_interval_seconds - elapsed
            if remaining > 0:
                self.sleep_fn(remaining)
                now = self.monotonic_fn()
        self._last_candle_request_at = now

    def get_orderbooks(
        self,
        markets: list[str],
        count: int = 15,
    ) -> list[dict[str, Any]]:
        if not markets:
            raise ValueError("markets must not be empty")
        if count < 1 or count > 30:
            raise ValueError("count must be between 1 and 30")

        return self._request_public_list(
            "/v1/orderbook",
            params={"markets": ",".join(markets), "count": count},
            response_name="orderbook",
        )

    def get_tickers(self, markets: list[str]) -> list[dict[str, Any]]:
        if not markets:
            raise ValueError("markets must not be empty")

        return self._request_public_list(
            "/v1/ticker",
            params={"markets": ",".join(markets)},
            response_name="ticker",
        )

    def get_markets(self, is_details: bool = True) -> list[dict[str, Any]]:
        return self._request_public_list(
            "/v1/market/all",
            params={"is_details": str(is_details).lower()},
            response_name="market list",
        )

    def get_all_tickers(self, quote_currency: str = "KRW") -> list[dict[str, Any]]:
        normalized_quote = quote_currency.strip().upper()
        if not normalized_quote:
            raise ValueError("quote_currency must not be empty")
        return self._request_public_list(
            "/v1/ticker/all",
            params={"quote_currencies": normalized_quote},
            response_name="all ticker",
        )

    def get_accounts(self) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.BASE_URL}/v1/accounts",
            headers={
                "Authorization": self._create_authorization_header(),
                "accept": "application/json",
            },
            timeout=5.0,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            raise ValueError("Unexpected Upbit accounts response format")

        return data

    def create_market_buy_order(
        self,
        market: str,
        amount_krw: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_market_buy_body(market, amount_krw, identifier)
        return self._create_order(body=body)

    def create_market_sell_order(
        self,
        market: str,
        quantity: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_market_sell_body(market, quantity, identifier)
        return self._create_order(body=body)

    def test_market_buy_order(
        self,
        market: str,
        amount_krw: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_market_buy_body(market, amount_krw, identifier)
        return self._submit_order_test(body)

    def test_market_sell_order(
        self,
        market: str,
        quantity: Decimal,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_market_sell_body(market, quantity, identifier)
        return self._submit_order_test(body)

    def get_order(
        self,
        *,
        uuid: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        if (uuid is None) == (identifier is None):
            raise ValueError("Exactly one of uuid and identifier must be supplied")

        parameter_name = "uuid" if uuid is not None else "identifier"
        raw_value = uuid if uuid is not None else identifier
        value = raw_value.strip() if raw_value is not None else ""
        if not value:
            raise ValueError(f"{parameter_name} must not be blank")

        params = {parameter_name: value}
        response = self._request_order(
            method="GET",
            path="/v1/order",
            operation="get_order",
            params=params,
        )
        return self._decode_order_response(response, "get_order")

    def get_order_chance(self, market: str) -> dict[str, Any]:
        normalized_market = market.strip()
        if not normalized_market:
            raise ValueError("market must not be empty")
        response = self._request_order(
            method="GET",
            path="/v1/orders/chance",
            operation="get_order_chance",
            params={"market": normalized_market},
            read_only=True,
        )
        return self._decode_read_response(response, "get_order_chance")

    def _create_order(
        self,
        body: dict[str, str],
    ) -> dict[str, Any]:
        response = self._request_order(
            method="POST",
            path="/v1/orders",
            operation="create_order",
            json_body=body,
        )
        return self._decode_order_response(response, "create_order")

    def _submit_order_test(self, body: dict[str, str]) -> dict[str, Any]:
        response = self._request_order(
            method="POST",
            path="/v1/orders/test",
            operation="test_order",
            json_body=body,
        )
        return self._decode_order_response(response, "test_order")

    def _request_order(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
        read_only: bool = False,
    ) -> httpx.Response:
        authorization_params = params if params is not None else json_body
        try:
            if method == "GET":
                response = httpx.get(
                    f"{self.BASE_URL}{path}",
                    params=params,
                    headers={
                        "Authorization": self._create_authorization_header(
                            authorization_params
                        ),
                        "accept": "application/json",
                    },
                    timeout=5.0,
                )
            else:
                response = httpx.post(
                    f"{self.BASE_URL}{path}",
                    json=json_body,
                    headers={
                        "Authorization": self._create_authorization_header(
                            authorization_params
                        ),
                        "Content-Type": "application/json",
                        "accept": "application/json",
                    },
                    timeout=5.0,
                )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            if read_only:
                classified = self._classify_http_error(error, operation)
                raise UpbitOrderReadError(classified.safe_error) from None
            raise self._classify_http_error(error, operation) from None
        except (httpx.TimeoutException, httpx.TransportError) as error:
            if read_only:
                raise UpbitOrderReadError(
                    UpbitSafeError(
                        error_type=type(error).__name__,
                        operation=operation,
                        message="Upbit read-only response was not confirmed",
                    )
                ) from None
            raise UpbitOrderAmbiguousError(
                UpbitSafeError(
                    error_type=type(error).__name__,
                    operation=operation,
                    message="Upbit response was not confirmed",
                )
            ) from None

    @staticmethod
    def _decode_order_response(
        response: httpx.Response,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError, TypeError:
            raise UpbitOrderAmbiguousError(
                UpbitSafeError(
                    error_type="InvalidResponse",
                    operation=operation,
                    status_code=response.status_code,
                    message="Upbit returned an invalid response",
                )
            ) from None
        if not isinstance(data, dict):
            raise UpbitOrderAmbiguousError(
                UpbitSafeError(
                    error_type="InvalidResponse",
                    operation=operation,
                    status_code=response.status_code,
                    message="Upbit returned an unexpected response type",
                )
            )
        return data

    @staticmethod
    def _decode_read_response(
        response: httpx.Response,
        operation: str,
    ) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError, TypeError:
            raise UpbitOrderReadError(
                UpbitSafeError(
                    error_type="InvalidResponse",
                    operation=operation,
                    status_code=response.status_code,
                    message="Upbit returned an invalid read-only response",
                )
            ) from None
        if not isinstance(data, dict):
            raise UpbitOrderReadError(
                UpbitSafeError(
                    error_type="InvalidResponse",
                    operation=operation,
                    status_code=response.status_code,
                    message="Upbit returned an unexpected read-only response type",
                )
            )
        return data

    @staticmethod
    def _classify_http_error(
        error: httpx.HTTPStatusError,
        operation: str,
    ) -> UpbitOrderOperationError:
        status_code = error.response.status_code
        error_name: str | None = None
        message = "Upbit rejected the order operation"
        try:
            payload = error.response.json()
            error_payload = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error_payload, dict):
                raw_name = error_payload.get("name")
                raw_message = error_payload.get("message")
                error_name = (
                    UpbitClient._sanitize_error_text(str(raw_name), 100)
                    if raw_name is not None
                    else None
                )
                if raw_message is not None:
                    message = UpbitClient._sanitize_error_text(str(raw_message), 300)
        except ValueError, TypeError:
            message = "Upbit returned an HTTP error with no valid JSON body"

        safe_error = UpbitSafeError(
            error_type="HTTPStatusError",
            operation=operation,
            status_code=status_code,
            upbit_error_name=error_name,
            message=message,
        )
        if operation == "get_order" and status_code == 404:
            return UpbitOrderNotFoundError(safe_error)
        if status_code >= 500:
            return UpbitOrderAmbiguousError(safe_error)
        if operation == "create_order":
            return UpbitOrderRejectedError(safe_error)
        if 400 <= status_code < 500:
            return UpbitOrderRejectedError(safe_error)
        return UpbitOrderAmbiguousError(safe_error)

    @staticmethod
    def _sanitize_error_text(value: str, limit: int) -> str:
        sanitized = value
        settings = get_settings()
        for secret in (settings.upbit_access_key, settings.upbit_secret_key):
            if secret:
                sanitized = sanitized.replace(secret, "[REDACTED]")
        if "Bearer " in sanitized:
            sanitized = sanitized.split("Bearer ", maxsplit=1)[0] + "Bearer [REDACTED]"
        return sanitized[:limit]

    @staticmethod
    def _build_market_buy_body(
        market: str,
        amount_krw: Decimal,
        identifier: str | None,
    ) -> dict[str, str]:
        if not market.strip():
            raise ValueError("market must not be empty")
        if amount_krw <= 0:
            raise ValueError("amount_krw must be greater than 0")
        body = {
            "market": market,
            "side": "bid",
            "price": UpbitClient._format_decimal(amount_krw),
            "ord_type": "price",
        }
        if identifier:
            body["identifier"] = identifier
        return body

    @staticmethod
    def _build_market_sell_body(
        market: str,
        quantity: Decimal,
        identifier: str | None,
    ) -> dict[str, str]:
        if not market.strip():
            raise ValueError("market must not be empty")
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        body = {
            "market": market,
            "side": "ask",
            "volume": UpbitClient._format_decimal(quantity),
            "ord_type": "market",
        }
        if identifier:
            body["identifier"] = identifier
        return body

    def _create_authorization_header(
        self,
        params: Mapping[str, object] | None = None,
    ) -> str:
        settings = get_settings()

        if not settings.upbit_access_key or not settings.upbit_secret_key:
            raise ValueError("Upbit API keys are not configured")

        payload = {
            "access_key": settings.upbit_access_key,
            "nonce": str(uuid4()),
        }

        query_string = self._build_query_string(params)

        if query_string:
            payload["query_hash"] = sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(
            payload,
            settings.upbit_secret_key,
            algorithm="HS512",
        )

        return f"Bearer {token}"

    @staticmethod
    def _build_query_string(
        params: Mapping[str, object] | None,
    ) -> str:
        if not params:
            return ""

        return unquote(urlencode(params))

    def get_minute_candles(
        self,
        market: str,
        unit: int = 15,
        count: int = 50,
    ) -> list[dict[str, Any]]:
        allowed_units = {1, 3, 5, 10, 15, 30, 60, 240}

        if unit not in allowed_units:
            raise ValueError(f"Unsupported minute candle unit. unit={unit}")

        if count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")

        return self._request_public_list(
            f"/v1/candles/minutes/{unit}",
            params={
                "market": market,
                "count": count,
            },
            response_name="candle",
            candle_group=True,
        )

    def get_day_candles(
        self,
        market: str,
        count: int = 50,
    ) -> list[dict[str, Any]]:
        if not market.strip():
            raise ValueError("market must not be empty")
        if count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")
        return self._request_public_list(
            "/v1/candles/days",
            params={"market": market, "count": count},
            response_name="day candle",
            candle_group=True,
        )

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value.normalize(), "f")
