from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class UpbitOrderChanceValidationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(
            f"Upbit order chance validation failed. reason_code={reason_code}"
        )


@dataclass(frozen=True)
class UpbitOrderChanceAccount:
    currency: str
    available_balance: Decimal


@dataclass(frozen=True)
class UpbitOrderChanceMarketRules:
    market: str
    order_sides: tuple[str, ...]
    bid_types: tuple[str, ...]
    ask_types: tuple[str, ...]
    bid_min_total: Decimal
    ask_min_total: Decimal
    max_total: Decimal


@dataclass(frozen=True)
class UpbitOrderChance:
    bid_fee: Decimal
    ask_fee: Decimal
    rules: UpbitOrderChanceMarketRules
    bid_account: UpbitOrderChanceAccount
    ask_account: UpbitOrderChanceAccount


@dataclass(frozen=True)
class UpbitOrderChancePreflightResult:
    audit: dict[str, Any]


def parse_upbit_order_chance(
    payload: Mapping[str, Any], *, expected_market: str
) -> UpbitOrderChance:
    market = _mapping(payload.get("market"))
    market_id = _required_text(market.get("id"))
    if market_id != expected_market:
        raise UpbitOrderChanceValidationError("MARKET_MISMATCH")

    quote_asset, base_asset = _split_market(expected_market)
    bid_rules = _mapping(market.get("bid"))
    ask_rules = _mapping(market.get("ask"))
    bid_account = _parse_account(payload.get("bid_account"), quote_asset)
    ask_account = _parse_account(payload.get("ask_account"), base_asset)

    return UpbitOrderChance(
        bid_fee=_decimal(
            payload.get("bid_fee"), allow_zero=True, reason_code="INVALID_FEE"
        ),
        ask_fee=_decimal(
            payload.get("ask_fee"), allow_zero=True, reason_code="INVALID_FEE"
        ),
        rules=UpbitOrderChanceMarketRules(
            market=market_id,
            order_sides=_string_tuple(market.get("order_sides")),
            bid_types=_string_tuple(market.get("bid_types")),
            ask_types=_string_tuple(market.get("ask_types")),
            bid_min_total=_decimal(bid_rules.get("min_total")),
            ask_min_total=_decimal(ask_rules.get("min_total")),
            max_total=_decimal(market.get("max_total")),
        ),
        bid_account=bid_account,
        ask_account=ask_account,
    )


class UpbitOrderChancePreflightService:
    def __init__(self, *, now_fn=lambda: datetime.now(UTC)) -> None:
        self.now_fn = now_fn

    def validate_buy(
        self,
        chance: UpbitOrderChance,
        *,
        market: str,
        approved_amount_krw: Decimal,
    ) -> UpbitOrderChancePreflightResult:
        self._validate_market(chance, market)
        if (
            "bid" not in chance.rules.order_sides
            or "price" not in chance.rules.bid_types
        ):
            raise UpbitOrderChanceValidationError("UNSUPPORTED_BUY_ORDER_TYPE")
        if approved_amount_krw < chance.rules.bid_min_total:
            raise UpbitOrderChanceValidationError("BELOW_EXCHANGE_MINIMUM")
        if approved_amount_krw > chance.rules.max_total:
            raise UpbitOrderChanceValidationError("ABOVE_EXCHANGE_MAXIMUM")
        available = chance.bid_account.available_balance
        if approved_amount_krw > available:
            raise UpbitOrderChanceValidationError("INSUFFICIENT_QUOTE_BALANCE")
        fee_reserve = approved_amount_krw * chance.bid_fee
        if approved_amount_krw + fee_reserve > available:
            raise UpbitOrderChanceValidationError("INSUFFICIENT_FEE_RESERVE")
        return UpbitOrderChancePreflightResult(
            self._audit(
                chance,
                market=market,
                side="BUY",
                required_order_type="price",
                applicable_min_total=chance.rules.bid_min_total,
                approved_amount=approved_amount_krw,
                approved_quantity=None,
                available_balance=available,
                fee=chance.bid_fee,
                fee_reserve=fee_reserve,
            )
        )

    def validate_sell(
        self,
        chance: UpbitOrderChance,
        *,
        market: str,
        approved_quantity: Decimal,
        current_value_krw: Decimal,
    ) -> UpbitOrderChancePreflightResult:
        self._validate_market(chance, market)
        if (
            "ask" not in chance.rules.order_sides
            or "market" not in chance.rules.ask_types
        ):
            raise UpbitOrderChanceValidationError("UNSUPPORTED_SELL_ORDER_TYPE")
        if approved_quantity > chance.ask_account.available_balance:
            raise UpbitOrderChanceValidationError("INSUFFICIENT_BASE_BALANCE")
        if current_value_krw < chance.rules.ask_min_total:
            raise UpbitOrderChanceValidationError("BELOW_EXCHANGE_MINIMUM")
        return UpbitOrderChancePreflightResult(
            self._audit(
                chance,
                market=market,
                side="SELL",
                required_order_type="market",
                applicable_min_total=chance.rules.ask_min_total,
                approved_amount=current_value_krw,
                approved_quantity=approved_quantity,
                available_balance=chance.ask_account.available_balance,
                fee=chance.ask_fee,
                fee_reserve=None,
            )
        )

    def failed_audit(self, *, market: str, reason_code: str) -> dict[str, Any]:
        return {
            "type": "UPBIT_ORDER_CHANCE",
            "market": market,
            "result": "FAILED",
            "reason_code": reason_code,
            "checked_at": self.now_fn().isoformat(),
        }

    @staticmethod
    def _validate_market(chance: UpbitOrderChance, market: str) -> None:
        if chance.rules.market != market:
            raise UpbitOrderChanceValidationError("MARKET_MISMATCH")

    def _audit(
        self,
        chance: UpbitOrderChance,
        *,
        market: str,
        side: str,
        required_order_type: str,
        applicable_min_total: Decimal,
        approved_amount: Decimal,
        approved_quantity: Decimal | None,
        available_balance: Decimal,
        fee: Decimal,
        fee_reserve: Decimal | None,
    ) -> dict[str, Any]:
        return {
            "type": "UPBIT_ORDER_CHANCE",
            "result": "PASSED",
            "checked_at": self.now_fn().isoformat(),
            "market": market,
            "side": side,
            "required_order_type": required_order_type,
            "applicable_min_total": str(applicable_min_total),
            "applicable_max_total": str(chance.rules.max_total),
            "approved_amount_krw": str(approved_amount),
            "approved_quantity": (
                str(approved_quantity) if approved_quantity is not None else None
            ),
            "available_balance": str(available_balance),
            "fee_rate": str(fee),
            "fee_reserve_krw": str(fee_reserve) if fee_reserve is not None else None,
        }


def _parse_account(value: object, expected_currency: str) -> UpbitOrderChanceAccount:
    account = _mapping(value)
    currency = _required_text(account.get("currency"))
    if currency != expected_currency:
        raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
    return UpbitOrderChanceAccount(
        currency=currency,
        available_balance=_decimal(account.get("balance"), allow_zero=True),
    )


def _split_market(market: str) -> tuple[str, str]:
    parts = market.split("-", maxsplit=1)
    if len(parts) != 2 or not all(parts):
        raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
    return parts[0], parts[1]


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
    return value


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
    return value.strip()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
    return tuple(item.strip() for item in value)


def _decimal(
    value: object,
    *,
    allow_zero: bool = False,
    reason_code: str = "INVALID_CHANCE_RESPONSE",
) -> Decimal:
    if isinstance(value, (dict, list, tuple, set, bool)) or value is None:
        raise UpbitOrderChanceValidationError(reason_code)
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation, TypeError, ValueError:
        raise UpbitOrderChanceValidationError(reason_code) from None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise UpbitOrderChanceValidationError(reason_code)
    return result
