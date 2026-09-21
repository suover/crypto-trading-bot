from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from crypto_trading_bot.operational.error_classifier import (
    OperationalErrorClassifier,
    OperationalErrorEnvelope,
)


OPERATIONAL_ERROR_FILE_ENV = "CRYPTO_TRADING_OPERATIONAL_ERROR_FILE"
T = TypeVar("T")


def write_operational_error(
    error: BaseException, *, service_hint: str | None = None
) -> OperationalErrorEnvelope:
    envelope = OperationalErrorClassifier.classify(error, service_hint=service_hint)
    raw_path = os.environ.get(OPERATIONAL_ERROR_FILE_ENV, "").strip()
    if not raw_path:
        return envelope
    try:
        descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "category": envelope.category,
                    "code": envelope.code,
                    "safe_message": envelope.safe_message,
                    "http_status_code": envelope.http_status_code,
                },
                output,
            )
        try:
            os.chmod(raw_path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return envelope


def read_operational_error(path: str | Path) -> OperationalErrorEnvelope:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return OperationalErrorEnvelope.unknown()
        return OperationalErrorClassifier.from_safe_values(
            payload.get("category"),
            payload.get("code"),
            payload.get("http_status_code"),
        )
    except OSError, ValueError, TypeError:
        return OperationalErrorEnvelope.unknown()


def run_with_operational_error_reporting(
    function: Callable[[], T], *, service_hint: str | None = None
) -> T:
    try:
        return function()
    except Exception as error:
        envelope = write_operational_error(error, service_hint=service_hint)
        print(
            f"Step failed. category={envelope.category} code={envelope.code} "
            f"http_status_code={envelope.http_status_code}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1) from None
