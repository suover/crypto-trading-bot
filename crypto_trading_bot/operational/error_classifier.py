from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderOperationError,
)


OperationalErrorCategory = Literal[
    "UPBIT_AUTH",
    "UPBIT_RATE_LIMIT",
    "UPBIT_NETWORK",
    "UPBIT_API",
    "OPENAI_AUTH",
    "OPENAI_RATE_LIMIT",
    "OPENAI_NETWORK",
    "OPENAI_API",
    "TELEGRAM_NETWORK",
    "TELEGRAM_API",
    "DATABASE",
    "CONFIGURATION",
    "DATA_VALIDATION",
    "UNKNOWN",
]

SAFE_MESSAGES: dict[str, str] = {
    "UPBIT_AUTH": "Upbit 인증 요청에 실패했습니다.",
    "UPBIT_RATE_LIMIT": "Upbit API 요청 한도에 도달했습니다.",
    "UPBIT_NETWORK": "Upbit API 네트워크 요청에 실패했습니다.",
    "UPBIT_API": "Upbit API 처리 중 오류가 발생했습니다.",
    "OPENAI_AUTH": "OpenAI 인증 요청에 실패했습니다.",
    "OPENAI_RATE_LIMIT": "OpenAI API 요청 한도에 도달했습니다.",
    "OPENAI_NETWORK": "OpenAI API 네트워크 요청에 실패했습니다.",
    "OPENAI_API": "OpenAI API 처리 중 오류가 발생했습니다.",
    "TELEGRAM_NETWORK": "Telegram API 네트워크 요청에 실패했습니다.",
    "TELEGRAM_API": "Telegram API 처리 중 오류가 발생했습니다.",
    "DATABASE": "데이터베이스 처리 중 오류가 발생했습니다.",
    "CONFIGURATION": "애플리케이션 설정이 올바르지 않습니다.",
    "DATA_VALIDATION": "처리할 데이터가 유효하지 않습니다.",
    "UNKNOWN": "분류되지 않은 애플리케이션 오류가 발생했습니다.",
}


@dataclass(frozen=True)
class OperationalErrorEnvelope:
    category: OperationalErrorCategory
    code: str
    safe_message: str
    http_status_code: int | None = None

    @classmethod
    def unknown(cls) -> "OperationalErrorEnvelope":
        return cls("UNKNOWN", "UNKNOWN_ERROR", SAFE_MESSAGES["UNKNOWN"])


class OperationalErrorClassifier:
    @classmethod
    def classify(
        cls, error: BaseException, *, service_hint: str | None = None
    ) -> OperationalErrorEnvelope:
        chain = cls._exception_chain(error)
        for candidate in chain:
            if isinstance(candidate, UpbitOrderOperationError):
                return cls._service_http("UPBIT", candidate.safe_error.status_code)
            if isinstance(candidate, SQLAlchemyError):
                return cls._fixed("DATABASE", "DB_OPERATION_ERROR")
            if isinstance(
                candidate, ValidationError
            ) or candidate.__class__.__module__.startswith("pydantic_settings"):
                return cls._fixed("CONFIGURATION", "CONFIGURATION_ERROR")

        for candidate in chain:
            module = candidate.__class__.__module__.lower()
            status = cls._status_code(candidate)
            if module.startswith("openai"):
                if cls._is_network(candidate):
                    return cls._fixed("OPENAI_NETWORK", cls._network_code(candidate))
                return cls._service_http("OPENAI", status)
            if isinstance(candidate, httpx.HTTPStatusError):
                service = cls._httpx_service(candidate) or service_hint
                return cls._service_http(service, status)
            if isinstance(candidate, (httpx.TimeoutException, httpx.TransportError)):
                service = cls._httpx_service(candidate) or service_hint
                if service in {"UPBIT", "OPENAI", "TELEGRAM"}:
                    return cls._fixed(
                        f"{service}_NETWORK", cls._network_code(candidate)
                    )

        normalized_hint = (service_hint or "").strip().upper()
        if normalized_hint in {"UPBIT", "OPENAI", "TELEGRAM"}:
            return cls._fixed(f"{normalized_hint}_API", "API_ERROR")
        if isinstance(error, (ValueError, TypeError, ArithmeticError)):
            return cls._fixed("DATA_VALIDATION", "VALIDATION_ERROR")
        return OperationalErrorEnvelope.unknown()

    @classmethod
    def from_safe_values(
        cls, category: object, code: object, http_status_code: object
    ) -> OperationalErrorEnvelope:
        normalized = str(category).strip().upper()
        if normalized not in SAFE_MESSAGES:
            return OperationalErrorEnvelope.unknown()
        safe_code = str(code).strip().upper()
        if (
            not safe_code
            or len(safe_code) > 50
            or not all(
                character.isalnum() or character == "_" for character in safe_code
            )
        ):
            safe_code = "UNKNOWN_ERROR"
        status = (
            http_status_code
            if isinstance(http_status_code, int)
            and not isinstance(http_status_code, bool)
            and 100 <= http_status_code <= 599
            else None
        )
        return OperationalErrorEnvelope(
            normalized,  # type: ignore[arg-type]
            safe_code,
            SAFE_MESSAGES[normalized],
            status,
        )

    @classmethod
    def _service_http(
        cls, service: str | None, status: int | None
    ) -> OperationalErrorEnvelope:
        normalized = (service or "").strip().upper()
        if normalized not in {"UPBIT", "OPENAI", "TELEGRAM"}:
            return OperationalErrorEnvelope.unknown()
        if normalized != "TELEGRAM" and status in {401, 403}:
            category = f"{normalized}_AUTH"
        elif normalized != "TELEGRAM" and status == 429:
            category = f"{normalized}_RATE_LIMIT"
        else:
            category = f"{normalized}_API"
        code = f"HTTP_{status}" if status is not None else "API_ERROR"
        return cls._fixed(category, code, status)

    @staticmethod
    def _fixed(
        category: str, code: str, status: int | None = None
    ) -> OperationalErrorEnvelope:
        return OperationalErrorEnvelope(  # type: ignore[arg-type]
            category, code, SAFE_MESSAGES[category], status
        )

    @staticmethod
    def _status_code(error: BaseException) -> int | None:
        value = getattr(error, "status_code", None)
        if value is None and isinstance(error, httpx.HTTPStatusError):
            value = error.response.status_code
        return value if isinstance(value, int) and 100 <= value <= 599 else None

    @staticmethod
    def _is_network(error: BaseException) -> bool:
        name = error.__class__.__name__.lower()
        return isinstance(error, (httpx.TimeoutException, httpx.TransportError)) or any(
            token in name for token in ("timeout", "connection", "network")
        )

    @staticmethod
    def _network_code(error: BaseException) -> str:
        return (
            "REQUEST_TIMEOUT"
            if "timeout" in error.__class__.__name__.lower()
            else "CONNECTION_ERROR"
        )

    @staticmethod
    def _httpx_service(error: BaseException) -> str | None:
        request = getattr(error, "request", None)
        host = str(getattr(getattr(request, "url", None), "host", "")).lower()
        if "upbit" in host:
            return "UPBIT"
        if "openai" in host:
            return "OPENAI"
        if "telegram" in host:
            return "TELEGRAM"
        return None

    @staticmethod
    def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
        result: list[BaseException] = []
        current: BaseException | None = error
        while current is not None and current not in result and len(result) < 10:
            result.append(current)
            current = current.__cause__ or current.__context__
        return tuple(result)
