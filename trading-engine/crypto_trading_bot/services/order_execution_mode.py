from dataclasses import dataclass

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.live_order_safety import (
    LiveOrderSafetyCheck,
    check_live_order_safety,
)


SUPPORTED_ORDER_EXECUTION_MODES = {"MOCK", "LIVE"}


class OrderExecutionModeError(ValueError):
    """주문 실행 모드 설정이 유효하지 않을 때 발생하는 예외."""


@dataclass(frozen=True)
class OrderExecutionModeCheck:
    mode: str
    ready: bool
    reasons: tuple[str, ...]
    live_safety_ready: bool | None = None
    live_safety_reasons: tuple[str, ...] = ()


def normalize_order_execution_mode(mode: str) -> str:
    normalized_mode = mode.strip().upper()

    if normalized_mode not in SUPPORTED_ORDER_EXECUTION_MODES:
        raise OrderExecutionModeError(
            f"order_execution_mode must be MOCK or LIVE. order_execution_mode={mode}"
        )

    return normalized_mode


def check_order_execution_mode(settings: Settings) -> OrderExecutionModeCheck:
    mode = normalize_order_execution_mode(settings.order_execution_mode)

    if mode == "MOCK":
        return OrderExecutionModeCheck(
            mode=mode,
            ready=True,
            reasons=(),
            live_safety_ready=None,
            live_safety_reasons=(),
        )

    live_safety_check: LiveOrderSafetyCheck = check_live_order_safety(settings)

    if live_safety_check.ready:
        return OrderExecutionModeCheck(
            mode=mode,
            ready=True,
            reasons=(),
            live_safety_ready=True,
            live_safety_reasons=(),
        )

    return OrderExecutionModeCheck(
        mode=mode,
        ready=False,
        reasons=("live order safety check is not ready",),
        live_safety_ready=False,
        live_safety_reasons=live_safety_check.reasons,
    )


def assert_order_execution_mode_ready(settings: Settings) -> str:
    check = check_order_execution_mode(settings)

    if check.ready:
        return check.mode

    raise OrderExecutionModeError(
        "Order execution mode is not ready. "
        f"mode={check.mode}, "
        f"reasons={'; '.join(check.reasons)}, "
        f"live_safety_reasons={'; '.join(check.live_safety_reasons)}"
    )
