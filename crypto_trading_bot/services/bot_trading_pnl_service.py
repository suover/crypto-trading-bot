from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    BotInventoryLot,
    BotPnlMatch,
    BotSellRealization,
    BotTradingPnlSummary,
    OrderLog,
)


TERMINAL_PNL_SOURCE_STATUSES = ("LIVE_DONE", "LIVE_EXECUTED_CANCELLED")
MONEY_QUANTUM = Decimal("0.0000000001")
ZERO = Decimal("0")


@dataclass
class BotInventoryLotPlan:
    source_buy_order_log_id: int
    user_id: int
    exchange: str
    market: str
    acquired_quantity: Decimal
    remaining_quantity: Decimal
    gross_buy_funds_krw: Decimal
    buy_fee_krw: Decimal
    original_cost_basis_krw: Decimal
    remaining_cost_basis_krw: Decimal
    unit_cost_basis_krw: Decimal
    opened_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class BotPnlMatchPlan:
    source_buy_order_log_id: int
    matched_quantity: Decimal
    allocated_buy_cost_basis_krw: Decimal
    allocated_sell_gross_proceeds_krw: Decimal
    allocated_sell_fee_krw: Decimal
    allocated_sell_net_proceeds_krw: Decimal
    realized_pnl_krw: Decimal


@dataclass(frozen=True)
class BotSellRealizationPlan:
    source_sell_order_log_id: int
    user_id: int
    exchange: str
    market: str
    sold_quantity: Decimal
    gross_sell_proceeds_krw: Decimal
    sell_fee_krw: Decimal
    matched_quantity: Decimal
    unmatched_quantity: Decimal
    recognized_gross_proceeds_krw: Decimal | None
    recognized_sell_fee_krw: Decimal | None
    recognized_net_proceeds_krw: Decimal | None
    recognized_cost_basis_krw: Decimal | None
    recognized_realized_pnl_krw: Decimal | None
    status: str
    matches: tuple[BotPnlMatchPlan, ...]


@dataclass(frozen=True)
class BotTradingPnlSummaryPlan:
    user_id: int
    exchange: str
    processed_order_count: int
    processed_buy_order_count: int
    processed_sell_order_count: int
    gross_buy_funds_krw: Decimal
    gross_sell_funds_krw: Decimal
    total_buy_fees_krw: Decimal
    total_sell_fees_krw: Decimal
    total_fees_krw: Decimal
    recognized_sell_proceeds_krw: Decimal
    recognized_cost_basis_krw: Decimal
    recognized_realized_pnl_krw: Decimal
    recognized_realized_return_percentage: Decimal | None
    open_bot_cost_basis_krw: Decimal
    open_bot_lot_count: int
    fully_matched_sell_count: int
    partially_matched_sell_count: int
    unmatched_sell_count: int
    winning_sell_count: int
    losing_sell_count: int
    breakeven_sell_count: int
    win_rate_percentage: Decimal | None
    incomplete_order_count: int
    accounting_status: str
    source_order_count: int
    source_signature: str
    source_last_updated_at: datetime | None
    calculated_at: datetime


@dataclass(frozen=True)
class BotTradingPnlCalculation:
    lots: tuple[BotInventoryLotPlan, ...]
    sells: tuple[BotSellRealizationPlan, ...]
    summary: BotTradingPnlSummaryPlan


class BotTradingPnlService:
    """Derive bot-attributed realized PnL from persisted LIVE executions only."""

    def __init__(self, session: Session, *, now_fn=lambda: datetime.now(UTC)) -> None:
        self.session = session
        self.now_fn = now_fn

    def calculate(
        self, user_id: int, *, exchange: str = "UPBIT"
    ) -> BotTradingPnlCalculation:
        if exchange != "UPBIT":
            raise ValueError("Bot trading PnL currently supports UPBIT only")
        source_orders = self._source_orders(user_id, exchange)
        return self._calculate_orders(user_id, exchange, source_orders)

    def rebuild(
        self, user_id: int, *, exchange: str = "UPBIT", apply: bool = False
    ) -> BotTradingPnlCalculation:
        calculation = self.calculate(user_id, exchange=exchange)
        if apply:
            self._replace_derived(calculation)
        return calculation

    def source_changed(self, user_id: int, *, exchange: str = "UPBIT") -> bool:
        source_orders = self._source_orders(user_id, exchange)
        signature = self._source_signature(source_orders)
        stored = self.session.scalar(
            select(BotTradingPnlSummary.source_signature).where(
                BotTradingPnlSummary.user_id == user_id,
                BotTradingPnlSummary.exchange == exchange,
            )
        )
        return stored != signature

    def _source_orders(self, user_id: int, exchange: str) -> tuple[OrderLog, ...]:
        with self.session.no_autoflush:
            return tuple(
                self.session.scalars(
                    select(OrderLog)
                    .where(
                        OrderLog.user_id == user_id,
                        OrderLog.exchange == exchange,
                        OrderLog.trading_mode == "LIVE",
                        OrderLog.status.in_(TERMINAL_PNL_SOURCE_STATUSES),
                    )
                    .order_by(OrderLog.created_at, OrderLog.id)
                )
            )

    def _calculate_orders(
        self, user_id: int, exchange: str, source_orders: tuple[OrderLog, ...]
    ) -> BotTradingPnlCalculation:
        lots: list[BotInventoryLotPlan] = []
        lots_by_market: dict[str, list[BotInventoryLotPlan]] = {}
        sells: list[BotSellRealizationPlan] = []
        blocked_markets: set[str] = set()
        incomplete_count = buy_count = sell_count = 0
        gross_buy = gross_sell = buy_fees = sell_fees = ZERO

        for order in source_orders:
            market = (order.market or "").strip().upper()
            if not market:
                incomplete_count += 1
                blocked_markets.add(market)
                continue
            if market in blocked_markets:
                incomplete_count += 1
                continue
            values = self._valid_execution(order)
            if values is None:
                incomplete_count += 1
                blocked_markets.add(market)
                continue
            side, quantity, funds, fee = values
            if side == "BUY":
                cost = self._money(funds + fee)
                lot = BotInventoryLotPlan(
                    source_buy_order_log_id=order.id,
                    user_id=user_id,
                    exchange=exchange,
                    market=market,
                    acquired_quantity=quantity,
                    remaining_quantity=quantity,
                    gross_buy_funds_krw=funds,
                    buy_fee_krw=fee,
                    original_cost_basis_krw=cost,
                    remaining_cost_basis_krw=cost,
                    unit_cost_basis_krw=self._money(cost / quantity),
                    opened_at=order.created_at,
                )
                lots.append(lot)
                lots_by_market.setdefault(market, []).append(lot)
                buy_count += 1
                gross_buy += funds
                buy_fees += fee
            else:
                sell = self._match_sell(order, quantity, funds, fee, lots_by_market)
                sells.append(sell)
                sell_count += 1
                gross_sell += funds
                sell_fees += fee

        recognized_proceeds = sum(
            (
                sell.recognized_net_proceeds_krw
                for sell in sells
                if sell.recognized_net_proceeds_krw is not None
            ),
            ZERO,
        )
        recognized_cost = sum(
            (
                sell.recognized_cost_basis_krw
                for sell in sells
                if sell.recognized_cost_basis_krw is not None
            ),
            ZERO,
        )
        recognized_pnl = sum(
            (
                sell.recognized_realized_pnl_krw
                for sell in sells
                if sell.recognized_realized_pnl_krw is not None
            ),
            ZERO,
        )
        fully_matched = [sell for sell in sells if sell.status == "FULLY_MATCHED"]
        wins = sum(
            sell.recognized_realized_pnl_krw > 0
            for sell in fully_matched
            if sell.recognized_realized_pnl_krw is not None
        )
        losses = sum(
            sell.recognized_realized_pnl_krw < 0
            for sell in fully_matched
            if sell.recognized_realized_pnl_krw is not None
        )
        breakevens = sum(
            sell.recognized_realized_pnl_krw == 0
            for sell in fully_matched
            if sell.recognized_realized_pnl_krw is not None
        )
        win_denominator = wins + losses + breakevens
        open_lots = [lot for lot in lots if lot.remaining_quantity > 0]
        source_last_updated = max(
            (order.updated_at for order in source_orders),
            key=self._datetime_sort_key,
            default=None,
        )
        partial_count = sum(sell.status == "PARTIALLY_MATCHED" for sell in sells)
        unmatched_count = sum(sell.status == "UNMATCHED" for sell in sells)
        accounting_status = (
            "PARTIAL"
            if incomplete_count or partial_count or unmatched_count
            else "COMPLETE"
        )
        summary = BotTradingPnlSummaryPlan(
            user_id=user_id,
            exchange=exchange,
            processed_order_count=buy_count + sell_count,
            processed_buy_order_count=buy_count,
            processed_sell_order_count=sell_count,
            gross_buy_funds_krw=self._money(gross_buy),
            gross_sell_funds_krw=self._money(gross_sell),
            total_buy_fees_krw=self._money(buy_fees),
            total_sell_fees_krw=self._money(sell_fees),
            total_fees_krw=self._money(buy_fees + sell_fees),
            recognized_sell_proceeds_krw=self._money(recognized_proceeds),
            recognized_cost_basis_krw=self._money(recognized_cost),
            recognized_realized_pnl_krw=self._money(recognized_pnl),
            recognized_realized_return_percentage=(
                self._money(recognized_pnl / recognized_cost * Decimal("100"))
                if recognized_cost > 0
                else None
            ),
            open_bot_cost_basis_krw=self._money(
                sum((lot.remaining_cost_basis_krw for lot in open_lots), ZERO)
            ),
            open_bot_lot_count=len(open_lots),
            fully_matched_sell_count=len(fully_matched),
            partially_matched_sell_count=partial_count,
            unmatched_sell_count=unmatched_count,
            winning_sell_count=wins,
            losing_sell_count=losses,
            breakeven_sell_count=breakevens,
            win_rate_percentage=(
                self._money(Decimal(wins) / Decimal(win_denominator) * Decimal("100"))
                if win_denominator
                else None
            ),
            incomplete_order_count=incomplete_count,
            accounting_status=accounting_status,
            source_order_count=len(source_orders),
            source_signature=self._source_signature(source_orders),
            source_last_updated_at=source_last_updated,
            calculated_at=self.now_fn(),
        )
        return BotTradingPnlCalculation(tuple(lots), tuple(sells), summary)

    def _match_sell(
        self,
        order: OrderLog,
        sold_quantity: Decimal,
        gross_proceeds: Decimal,
        fee: Decimal,
        lots_by_market: dict[str, list[BotInventoryLotPlan]],
    ) -> BotSellRealizationPlan:
        market = order.market.strip().upper()
        remaining_to_match = sold_quantity
        cost_matches: list[tuple[BotInventoryLotPlan, Decimal, Decimal]] = []
        for lot in lots_by_market.get(market, []):
            if remaining_to_match <= 0:
                break
            if lot.remaining_quantity <= 0:
                continue
            matched = min(remaining_to_match, lot.remaining_quantity)
            if matched == lot.remaining_quantity:
                allocated_cost = lot.remaining_cost_basis_krw
            else:
                allocated_cost = self._money(
                    lot.original_cost_basis_krw * matched / lot.acquired_quantity
                )
            lot.remaining_quantity -= matched
            lot.remaining_cost_basis_krw = self._money(
                lot.remaining_cost_basis_krw - allocated_cost
            )
            if lot.remaining_quantity == 0:
                lot.remaining_cost_basis_krw = ZERO
                lot.closed_at = order.created_at
            cost_matches.append((lot, matched, allocated_cost))
            remaining_to_match -= matched

        matched_quantity = sold_quantity - remaining_to_match
        if matched_quantity == 0:
            status = "UNMATCHED"
        elif remaining_to_match > 0:
            status = "PARTIALLY_MATCHED"
        else:
            status = "FULLY_MATCHED"
        if not cost_matches:
            recognized_gross = recognized_fee = recognized_net = None
            recognized_cost = recognized_pnl = None
            matches: tuple[BotPnlMatchPlan, ...] = ()
        else:
            recognized_gross = self._money(
                gross_proceeds * matched_quantity / sold_quantity
            )
            recognized_fee = self._money(fee * matched_quantity / sold_quantity)
            recognized_net = self._money(recognized_gross - recognized_fee)
            recognized_cost = self._money(
                sum((match[2] for match in cost_matches), ZERO)
            )
            recognized_pnl = self._money(recognized_net - recognized_cost)
            allocated_gross = allocated_fee = ZERO
            match_plans: list[BotPnlMatchPlan] = []
            for index, (lot, matched, cost) in enumerate(cost_matches):
                is_last = index == len(cost_matches) - 1
                gross_part = (
                    recognized_gross - allocated_gross
                    if is_last
                    else self._money(gross_proceeds * matched / sold_quantity)
                )
                fee_part = (
                    recognized_fee - allocated_fee
                    if is_last
                    else self._money(fee * matched / sold_quantity)
                )
                net_part = self._money(gross_part - fee_part)
                match_plans.append(
                    BotPnlMatchPlan(
                        source_buy_order_log_id=lot.source_buy_order_log_id,
                        matched_quantity=matched,
                        allocated_buy_cost_basis_krw=cost,
                        allocated_sell_gross_proceeds_krw=gross_part,
                        allocated_sell_fee_krw=fee_part,
                        allocated_sell_net_proceeds_krw=net_part,
                        realized_pnl_krw=self._money(net_part - cost),
                    )
                )
                allocated_gross += gross_part
                allocated_fee += fee_part
            matches = tuple(match_plans)
        return BotSellRealizationPlan(
            source_sell_order_log_id=order.id,
            user_id=order.user_id,
            exchange=order.exchange,
            market=market,
            sold_quantity=sold_quantity,
            gross_sell_proceeds_krw=gross_proceeds,
            sell_fee_krw=fee,
            matched_quantity=matched_quantity,
            unmatched_quantity=remaining_to_match,
            recognized_gross_proceeds_krw=recognized_gross,
            recognized_sell_fee_krw=recognized_fee,
            recognized_net_proceeds_krw=recognized_net,
            recognized_cost_basis_krw=recognized_cost,
            recognized_realized_pnl_krw=recognized_pnl,
            status=status,
            matches=matches,
        )

    def _replace_derived(self, calculation: BotTradingPnlCalculation) -> None:
        summary = calculation.summary
        realization_ids = select(BotSellRealization.id).where(
            BotSellRealization.user_id == summary.user_id,
            BotSellRealization.exchange == summary.exchange,
        )
        lot_ids = select(BotInventoryLot.id).where(
            BotInventoryLot.user_id == summary.user_id,
            BotInventoryLot.exchange == summary.exchange,
        )
        self.session.execute(
            delete(BotPnlMatch).where(
                (BotPnlMatch.sell_realization_id.in_(realization_ids))
                | (BotPnlMatch.buy_lot_id.in_(lot_ids))
            )
        )
        self.session.execute(
            delete(BotSellRealization).where(
                BotSellRealization.user_id == summary.user_id,
                BotSellRealization.exchange == summary.exchange,
            )
        )
        self.session.execute(
            delete(BotInventoryLot).where(
                BotInventoryLot.user_id == summary.user_id,
                BotInventoryLot.exchange == summary.exchange,
            )
        )
        self.session.execute(
            delete(BotTradingPnlSummary).where(
                BotTradingPnlSummary.user_id == summary.user_id,
                BotTradingPnlSummary.exchange == summary.exchange,
            )
        )
        self.session.flush()

        lot_rows = {
            lot.source_buy_order_log_id: BotInventoryLot(**vars(lot))
            for lot in calculation.lots
        }
        self.session.add_all(lot_rows.values())
        self.session.flush()
        for sell in calculation.sells:
            sell_values = vars(sell).copy()
            matches = sell_values.pop("matches")
            realization = BotSellRealization(
                **sell_values, attribution_method="BOT_FIFO"
            )
            self.session.add(realization)
            self.session.flush()
            self.session.add_all(
                [
                    BotPnlMatch(
                        sell_realization_id=realization.id,
                        buy_lot_id=lot_rows[match.source_buy_order_log_id].id,
                        **{
                            key: value
                            for key, value in vars(match).items()
                            if key != "source_buy_order_log_id"
                        },
                    )
                    for match in matches
                ]
            )
        self.session.add(BotTradingPnlSummary(**vars(summary)))
        self.session.flush()

    @classmethod
    def _valid_execution(
        cls, order: OrderLog
    ) -> tuple[str, Decimal, Decimal, Decimal] | None:
        side = (order.side or "").strip().upper()
        quantity = cls._decimal(order.executed_quantity)
        funds = cls._decimal(order.executed_funds_krw)
        fee = cls._decimal(order.paid_fee)
        if (
            side not in {"BUY", "SELL"}
            or quantity is None
            or quantity <= 0
            or funds is None
            or funds <= 0
            or fee is None
            or fee < 0
        ):
            return None
        return side, quantity, funds, fee

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)

    @staticmethod
    def _datetime_sort_key(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )

    @classmethod
    def _source_signature(cls, orders: tuple[OrderLog, ...]) -> str:
        digest = sha256()
        for order in orders:
            fields = (
                order.id,
                order.created_at,
                order.updated_at,
                order.execution_synced_at,
                order.status,
                order.side,
                order.market,
                order.executed_quantity,
                order.executed_funds_krw,
                order.paid_fee,
            )
            digest.update("\x1f".join(str(value) for value in fields).encode())
            digest.update(b"\x1e")
        return digest.hexdigest()
