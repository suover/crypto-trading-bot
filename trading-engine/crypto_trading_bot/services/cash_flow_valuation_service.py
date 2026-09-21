from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import AccountActivity, AccountCashFlowValuation
from crypto_trading_bot.exchange.market_data import ExchangeMarketDataProvider
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)


COMPLETED_CASH_FLOW_RULES = {
    "UPBIT_DEPOSIT": ("DEPOSIT", frozenset({"ACCEPTED"})),
    "UPBIT_WITHDRAWAL": ("WITHDRAWAL", frozenset({"DONE"})),
}
HISTORICAL_CANDLE_COUNT = 10


@dataclass(frozen=True)
class CashFlowValuationPlan:
    account_activity_id: int
    user_id: int
    exchange: str
    direction: str | None
    currency: str | None
    native_amount: Decimal | None
    event_time: datetime | None
    valuation_price_krw: Decimal | None
    cash_flow_value_krw: Decimal | None
    price_source: str
    valuation_status: str
    safe_reason: str | None
    valued_at: datetime


@dataclass(frozen=True)
class CashFlowValuationResult:
    plans: tuple[CashFlowValuationPlan, ...]
    complete_count: int
    partial_count: int


class CashFlowValuationService:
    """Value completed external account flows without mutating their source ledger."""

    def __init__(
        self,
        session: Session,
        *,
        market_data_provider: ExchangeMarketDataProvider | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.provider = market_data_provider or UpbitMarketDataProvider()
        self.now_fn = now_fn

    def value_scope(
        self,
        user_id: int,
        *,
        exchange: str = "UPBIT",
        apply: bool = False,
    ) -> CashFlowValuationResult:
        normalized_exchange = exchange.strip().upper()
        if normalized_exchange != "UPBIT":
            raise ValueError(
                "Cash-flow historical valuation currently supports UPBIT only"
            )
        activities = tuple(
            self.session.scalars(
                select(AccountActivity)
                .where(
                    AccountActivity.user_id == user_id,
                    AccountActivity.exchange == normalized_exchange,
                    AccountActivity.activity_type.in_(("DEPOSIT", "WITHDRAWAL")),
                    AccountActivity.source_type.in_(tuple(COMPLETED_CASH_FLOW_RULES)),
                )
                .order_by(AccountActivity.completed_at, AccountActivity.id)
            )
        )
        effective = tuple(
            activity for activity in activities if self._is_completed(activity)
        )
        existing = {
            row.account_activity_id: row
            for row in self.session.scalars(
                select(AccountCashFlowValuation).where(
                    AccountCashFlowValuation.user_id == user_id,
                    AccountCashFlowValuation.exchange == normalized_exchange,
                )
            )
        }
        plans = tuple(
            self._plan(activity, existing.get(activity.id)) for activity in effective
        )
        if apply:
            effective_ids = {activity.id for activity in effective}
            for activity_id, row in existing.items():
                if activity_id not in effective_ids:
                    self.session.delete(row)
            for plan in plans:
                self._upsert(existing.get(plan.account_activity_id), plan)
            self.session.flush()
        return CashFlowValuationResult(
            plans=plans,
            complete_count=sum(plan.valuation_status == "COMPLETE" for plan in plans),
            partial_count=sum(plan.valuation_status == "PARTIAL" for plan in plans),
        )

    @staticmethod
    def _is_completed(activity: AccountActivity) -> bool:
        rule = COMPLETED_CASH_FLOW_RULES.get(activity.source_type)
        return bool(
            rule is not None
            and activity.activity_type == rule[0]
            and activity.state.strip().upper() in rule[1]
        )

    def _plan(
        self,
        activity: AccountActivity,
        existing: AccountCashFlowValuation | None,
    ) -> CashFlowValuationPlan:
        direction = (activity.cash_flow_direction or "").strip().upper()
        currency = (activity.currency or "").strip().upper()
        amount = self._positive_decimal(activity.amount)
        event_time = self._aware_utc(activity.completed_at)
        if direction not in {"IN", "OUT"}:
            return self._partial(
                activity,
                None,
                currency or None,
                amount,
                event_time,
                "INVALID_DIRECTION",
            )
        if not currency:
            return self._partial(
                activity, direction, None, amount, event_time, "INVALID_CURRENCY"
            )
        if amount is None:
            return self._partial(
                activity,
                direction,
                currency,
                None,
                event_time,
                "INVALID_AMOUNT",
            )
        if event_time is None:
            return self._partial(
                activity, direction, currency, amount, None, "INVALID_EVENT_TIME"
            )
        if currency == "KRW":
            return self._complete(
                activity,
                direction,
                currency,
                amount,
                event_time,
                Decimal("1"),
                amount,
                "NATIVE_KRW",
            )
        if self._reusable(existing, direction, currency, amount, event_time):
            return CashFlowValuationPlan(
                account_activity_id=activity.id,
                user_id=activity.user_id,
                exchange=activity.exchange,
                direction=direction,
                currency=currency,
                native_amount=amount,
                event_time=event_time,
                valuation_price_krw=Decimal(existing.valuation_price_krw),
                cash_flow_value_krw=Decimal(existing.cash_flow_value_krw),
                price_source=existing.price_source,
                valuation_status="COMPLETE",
                safe_reason=None,
                valued_at=self._aware_utc(existing.valued_at) or self.now_fn(),
            )
        try:
            price = self._historical_price(f"KRW-{currency}", event_time)
        except Exception:
            price = None
        if price is None:
            return self._partial(
                activity,
                direction,
                currency,
                amount,
                event_time,
                "HISTORICAL_PRICE_UNAVAILABLE",
            )
        return self._complete(
            activity,
            direction,
            currency,
            amount,
            event_time,
            price,
            amount * price,
            "UPBIT_MINUTE_CANDLE_1M_CLOSE",
        )

    def _historical_price(self, market: str, event_time: datetime) -> Decimal | None:
        rows = self.provider.get_minute_candles(
            market,
            unit=1,
            count=HISTORICAL_CANDLE_COUNT,
            to=event_time,
        )
        eligible: list[tuple[datetime, Decimal]] = []
        for row in rows:
            candle_at = self._parse_upbit_utc(row.get("candle_date_time_utc"))
            price = self._positive_decimal(row.get("trade_price"))
            # Upbit labels minute candles by their start. Only a candle fully
            # closed by the event can be used, preventing within-minute look-ahead.
            if (
                candle_at is not None
                and price is not None
                and candle_at + timedelta(minutes=1) <= event_time
            ):
                eligible.append((candle_at, price))
        return max(eligible, default=(None, None), key=lambda item: item[0])[1]

    def _partial(
        self,
        activity: AccountActivity,
        direction: str | None,
        currency: str | None,
        amount: Decimal | None,
        event_time: datetime | None,
        reason: str,
    ) -> CashFlowValuationPlan:
        return CashFlowValuationPlan(
            account_activity_id=activity.id,
            user_id=activity.user_id,
            exchange=activity.exchange,
            direction=direction,
            currency=currency,
            native_amount=amount,
            event_time=event_time,
            valuation_price_krw=None,
            cash_flow_value_krw=None,
            price_source="UNAVAILABLE",
            valuation_status="PARTIAL",
            safe_reason=reason,
            valued_at=self.now_fn(),
        )

    def _complete(
        self,
        activity: AccountActivity,
        direction: str,
        currency: str,
        amount: Decimal,
        event_time: datetime,
        price: Decimal,
        value: Decimal,
        source: str,
    ) -> CashFlowValuationPlan:
        return CashFlowValuationPlan(
            account_activity_id=activity.id,
            user_id=activity.user_id,
            exchange=activity.exchange,
            direction=direction,
            currency=currency,
            native_amount=amount,
            event_time=event_time,
            valuation_price_krw=price,
            cash_flow_value_krw=value,
            price_source=source,
            valuation_status="COMPLETE",
            safe_reason=None,
            valued_at=self.now_fn(),
        )

    @staticmethod
    def _reusable(
        row: AccountCashFlowValuation | None,
        direction: str,
        currency: str,
        amount: Decimal,
        event_time: datetime,
    ) -> bool:
        return bool(
            row is not None
            and row.valuation_status == "COMPLETE"
            and row.direction == direction
            and row.currency == currency
            and CashFlowValuationService._positive_decimal(row.native_amount) == amount
            and CashFlowValuationService._aware_utc(row.event_time) == event_time
            and CashFlowValuationService._positive_decimal(row.valuation_price_krw)
            is not None
            and CashFlowValuationService._positive_decimal(row.cash_flow_value_krw)
            is not None
        )

    def _upsert(
        self,
        row: AccountCashFlowValuation | None,
        plan: CashFlowValuationPlan,
    ) -> None:
        values = plan.__dict__
        if row is None:
            self.session.add(AccountCashFlowValuation(**values))
            return
        for key, value in values.items():
            setattr(row, key, value)

    @staticmethod
    def _positive_decimal(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation, TypeError, ValueError:
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC)

    @staticmethod
    def _parse_upbit_utc(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        # This field is explicitly UTC in the Upbit response schema.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
