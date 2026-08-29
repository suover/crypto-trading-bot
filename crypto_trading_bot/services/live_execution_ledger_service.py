from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import OrderFill, OrderLog


@dataclass(frozen=True)
class NormalizedOrderFill:
    exchange_trade_id: str
    price: Decimal
    volume: Decimal
    funds_krw: Decimal
    side: str | None
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class NormalizedLiveExecution:
    executed_quantity: Decimal | None
    executed_funds_krw: Decimal | None
    average_execution_price: Decimal | None
    paid_fee: Decimal | None
    remaining_quantity: Decimal | None
    trades_count: int | None
    fills: tuple[NormalizedOrderFill, ...]

    @property
    def has_data(self) -> bool:
        return bool(self.fills) or any(
            value is not None
            for value in (
                self.executed_quantity,
                self.executed_funds_krw,
                self.paid_fee,
                self.remaining_quantity,
                self.trades_count,
            )
        )


@dataclass(frozen=True)
class LiveExecutionSyncResult:
    normalized: NormalizedLiveExecution
    inserted_fill_count: int
    execution_synced_at: datetime


class UpbitLiveExecutionNormalizer:
    @classmethod
    def normalize(cls, response: dict[str, Any]) -> NormalizedLiveExecution:
        executed_quantity = cls._non_negative_decimal(response.get("executed_volume"))
        direct_funds = cls._non_negative_decimal(response.get("executed_funds"))
        trades = response.get("trades")
        trade_rows = trades if isinstance(trades, list) else []
        if direct_funds is not None:
            executed_funds = direct_funds
        else:
            valid_trade_funds = [
                value
                for trade in trade_rows
                if isinstance(trade, dict)
                for value in [cls._non_negative_decimal(trade.get("funds"))]
                if value is not None
            ]
            executed_funds = (
                sum(valid_trade_funds, start=Decimal("0"))
                if valid_trade_funds
                else None
            )
        average_price = (
            executed_funds / executed_quantity
            if executed_quantity is not None
            and executed_quantity > 0
            and executed_funds is not None
            else None
        )
        fills = tuple(
            fill
            for trade in trade_rows
            if isinstance(trade, dict)
            for fill in [cls._normalize_fill(trade)]
            if fill is not None
        )
        return NormalizedLiveExecution(
            executed_quantity=executed_quantity,
            executed_funds_krw=executed_funds,
            average_execution_price=average_price,
            paid_fee=cls._non_negative_decimal(response.get("paid_fee")),
            remaining_quantity=cls._non_negative_decimal(
                response.get("remaining_volume")
            ),
            trades_count=cls._non_negative_integer(response.get("trades_count")),
            fills=fills,
        )

    @classmethod
    def _normalize_fill(cls, trade: dict[str, Any]) -> NormalizedOrderFill | None:
        raw_trade_id = trade.get("uuid")
        trade_id = str(raw_trade_id).strip() if raw_trade_id is not None else ""
        if not trade_id or len(trade_id) > 100:
            return None
        price = cls._positive_decimal(trade.get("price"))
        volume = cls._positive_decimal(trade.get("volume"))
        funds = cls._non_negative_decimal(trade.get("funds"))
        if price is None or volume is None or funds is None:
            return None
        raw_side = trade.get("side")
        side = str(raw_side).strip() if raw_side is not None else ""
        return NormalizedOrderFill(
            exchange_trade_id=trade_id,
            price=price,
            volume=volume,
            funds_krw=funds,
            side=side if side and len(side) <= 20 else None,
            raw_data=dict(trade),
        )

    @staticmethod
    def _non_negative_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed >= 0 else None

    @classmethod
    def _positive_decimal(cls, value: object) -> Decimal | None:
        parsed = cls._non_negative_decimal(value)
        return parsed if parsed is not None and parsed > 0 else None

    @staticmethod
    def _non_negative_integer(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
        return None


class LiveExecutionLedgerService:
    def __init__(
        self,
        session: Session,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.now_fn = now_fn

    def sync(
        self, order_log: OrderLog, response: dict[str, Any]
    ) -> LiveExecutionSyncResult:
        if order_log.id is None:
            raise ValueError("OrderLog must be flushed before execution ledger sync")
        normalized = UpbitLiveExecutionNormalizer.normalize(response)
        self._merge_summary(order_log, normalized)
        synced_at = self.now_fn()
        order_log.execution_synced_at = synced_at
        existing_trade_ids = set(
            self.session.scalars(
                select(OrderFill.exchange_trade_id).where(
                    OrderFill.order_log_id == order_log.id
                )
            ).all()
        )
        new_fills: list[OrderFill] = []
        seen_trade_ids = set(existing_trade_ids)
        for fill in normalized.fills:
            if fill.exchange_trade_id in seen_trade_ids:
                continue
            new_fills.append(
                OrderFill(
                    order_log_id=order_log.id,
                    exchange_trade_id=fill.exchange_trade_id,
                    price=fill.price,
                    volume=fill.volume,
                    funds_krw=fill.funds_krw,
                    side=fill.side,
                    raw_data=fill.raw_data,
                )
            )
            seen_trade_ids.add(fill.exchange_trade_id)
        self.session.add_all(new_fills)
        return LiveExecutionSyncResult(normalized, len(new_fills), synced_at)

    @staticmethod
    def _merge_summary(
        order_log: OrderLog, normalized: NormalizedLiveExecution
    ) -> None:
        has_existing_positive_execution = (
            order_log.executed_quantity is not None and order_log.executed_quantity > 0
        ) or (
            order_log.executed_funds_krw is not None
            and order_log.executed_funds_krw > 0
        )
        if (
            normalized.executed_quantity is not None
            and normalized.executed_quantity > 0
            and normalized.executed_funds_krw is not None
        ):
            order_log.executed_quantity = normalized.executed_quantity
            order_log.executed_funds_krw = normalized.executed_funds_krw
            order_log.average_execution_price = normalized.average_execution_price
        elif normalized.executed_quantity == 0 and not has_existing_positive_execution:
            order_log.executed_quantity = Decimal("0")
            if normalized.executed_funds_krw == 0:
                order_log.executed_funds_krw = Decimal("0")
            order_log.average_execution_price = None

        for field_name in (
            "paid_fee",
            "remaining_quantity",
            "trades_count",
        ):
            value = getattr(normalized, field_name)
            if value is not None:
                setattr(order_log, field_name, value)
