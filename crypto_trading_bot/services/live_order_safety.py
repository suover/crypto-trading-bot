from dataclasses import dataclass
from decimal import Decimal

from crypto_trading_bot.config.settings import Settings


LIVE_ORDER_CONFIRMATION_TEXT = "ENABLE_LIVE_UPBIT_ORDERS"
MIN_UPBIT_ORDER_AMOUNT_KRW = Decimal("5000")


class LiveOrderSafetyError(ValueError):
    """실거래 주문 안전장치를 통과하지 못했을 때 발생하는 예외."""


@dataclass(frozen=True)
class LiveOrderSafetyCheck:
    ready: bool
    reasons: tuple[str, ...]


def check_live_order_safety(settings: Settings) -> LiveOrderSafetyCheck:
    reasons: list[str] = []

    if not settings.live_order_enabled:
        reasons.append("live_order_enabled is false")

    if settings.live_order_confirmation != LIVE_ORDER_CONFIRMATION_TEXT:
        reasons.append("live_order_confirmation does not match required text")

    if not settings.upbit_access_key:
        reasons.append("upbit_access_key is not configured")

    if not settings.upbit_secret_key:
        reasons.append("upbit_secret_key is not configured")

    if settings.market_universe_mode == "STATIC" and not settings.allowed_market_list:
        reasons.append("allowed_markets is empty")

    if (
        settings.market_universe_mode == "DYNAMIC"
        and not settings.live_dynamic_market_enabled
    ):
        reasons.append("live_dynamic_market_enabled is false")

    max_order_amount_krw = Decimal(str(settings.max_order_amount_krw))
    daily_max_order_amount_krw = Decimal(str(settings.daily_max_order_amount_krw))

    if max_order_amount_krw < MIN_UPBIT_ORDER_AMOUNT_KRW:
        reasons.append(
            "max_order_amount_krw is below minimum Upbit order amount. "
            f"max_order_amount_krw={max_order_amount_krw}, "
            f"minimum={MIN_UPBIT_ORDER_AMOUNT_KRW}"
        )

    if daily_max_order_amount_krw < max_order_amount_krw:
        reasons.append(
            "daily_max_order_amount_krw is lower than max_order_amount_krw. "
            f"daily_max_order_amount_krw={daily_max_order_amount_krw}, "
            f"max_order_amount_krw={max_order_amount_krw}"
        )

    return LiveOrderSafetyCheck(
        ready=not reasons,
        reasons=tuple(reasons),
    )


def assert_live_order_safety_enabled(settings: Settings) -> None:
    check = check_live_order_safety(settings)

    if check.ready:
        return

    raise LiveOrderSafetyError(
        f"Live order safety check failed. reasons={'; '.join(check.reasons)}"
    )


def validate_live_order_request(
    settings: Settings,
    action: str,
    market: str,
    amount_krw: Decimal | None = None,
    quantity: Decimal | None = None,
) -> None:
    assert_live_order_safety_enabled(settings)

    normalized_action = action.strip().upper()

    if normalized_action not in {"BUY", "SELL"}:
        raise LiveOrderSafetyError(
            f"Live order action must be BUY or SELL. action={action}"
        )

    if (
        settings.market_universe_mode == "STATIC"
        and market not in settings.allowed_market_list
    ):
        raise LiveOrderSafetyError(
            "Live order market is not allowed. "
            f"market={market}, "
            f"allowed_markets={settings.allowed_market_list}"
        )

    if (
        settings.market_universe_mode == "DYNAMIC"
        and not settings.live_dynamic_market_enabled
    ):
        raise LiveOrderSafetyError("Dynamic live market execution is disabled")

    max_order_amount_krw = Decimal(str(settings.max_order_amount_krw))

    if normalized_action == "BUY":
        if amount_krw is None:
            raise LiveOrderSafetyError("BUY live order requires amount_krw")

        if not amount_krw.is_finite() or amount_krw <= 0:
            raise LiveOrderSafetyError(
                "BUY live order amount must be finite and positive"
            )

        if (
            not settings.live_order_chance_preflight_enabled
            and amount_krw < MIN_UPBIT_ORDER_AMOUNT_KRW
        ):
            raise LiveOrderSafetyError(
                "BUY live order amount is below minimum Upbit order amount. "
                f"amount_krw={amount_krw}, "
                f"minimum={MIN_UPBIT_ORDER_AMOUNT_KRW}"
            )

        if amount_krw > max_order_amount_krw:
            raise LiveOrderSafetyError(
                "BUY live order amount exceeds max_order_amount_krw. "
                f"amount_krw={amount_krw}, "
                f"max_order_amount_krw={max_order_amount_krw}"
            )

        return

    if quantity is None:
        raise LiveOrderSafetyError("SELL live order requires quantity")

    if not quantity.is_finite() or quantity <= 0:
        raise LiveOrderSafetyError(
            f"SELL live order quantity must be greater than 0. quantity={quantity}"
        )

    if (
        not settings.live_order_chance_preflight_enabled
        and amount_krw is not None
        and (not amount_krw.is_finite() or amount_krw < MIN_UPBIT_ORDER_AMOUNT_KRW)
    ):
        raise LiveOrderSafetyError(
            "SELL live order estimated amount is invalid or below minimum. "
            f"amount_krw={amount_krw}, "
            f"minimum={MIN_UPBIT_ORDER_AMOUNT_KRW}"
        )
