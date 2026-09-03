from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    AccountActivitySyncState,
    AccountActivity,
    AccountCashFlowValuation,
    PortfolioPerformanceSnapshot,
    PortfolioSnapshot,
)
from crypto_trading_bot.services.cash_flow_valuation_service import (
    CashFlowValuationPlan,
)


RETURN_METHOD = "MODIFIED_DIETZ"
INDEX_BASE = Decimal("100")
PERCENT = Decimal("100")
NUMERIC_QUANTUM = Decimal("0.0000000001")
REQUIRED_COVERAGE_SOURCES = ("UPBIT_DEPOSIT", "UPBIT_WITHDRAWAL")


@dataclass(frozen=True)
class TimedCashFlow:
    event_time: datetime
    signed_value_krw: Decimal


@dataclass(frozen=True)
class PerformancePlan:
    user_id: int
    exchange: str
    portfolio_snapshot_id: int
    previous_portfolio_snapshot_id: int | None
    period_start_at: datetime | None
    period_end_at: datetime
    start_value_krw: Decimal | None
    end_value_krw: Decimal | None
    external_inflow_krw: Decimal | None
    external_outflow_krw: Decimal | None
    net_external_flow_krw: Decimal | None
    return_method: str
    period_return_percentage: Decimal | None
    cumulative_return_percentage: Decimal | None
    performance_index: Decimal | None
    high_water_mark_index: Decimal | None
    high_water_mark_krw: Decimal | None
    drawdown_index: Decimal | None
    drawdown_krw: Decimal | None
    drawdown_percentage: Decimal | None
    max_drawdown_percentage: Decimal | None
    performance_status: str
    safe_reason: str | None
    calculated_at: datetime


@dataclass(frozen=True)
class PortfolioPerformanceResult:
    plans: tuple[PerformancePlan, ...]
    new_count: int
    update_count: int


def modified_dietz_return(
    start_value_krw: Decimal,
    end_value_krw: Decimal,
    period_start_at: datetime,
    period_end_at: datetime,
    flows: Iterable[TimedCashFlow],
) -> Decimal | None:
    """Return a cash-flow weighted period return ratio, or None if unsafe."""
    duration = _microseconds(period_end_at - period_start_at)
    if duration <= 0:
        return None
    total_flow = Decimal("0")
    weighted_flow = Decimal("0")
    for flow in flows:
        if not period_start_at < flow.event_time <= period_end_at:
            return None
        remaining = _microseconds(period_end_at - flow.event_time)
        weight = Decimal(remaining) / Decimal(duration)
        total_flow += flow.signed_value_krw
        weighted_flow += weight * flow.signed_value_krw
    denominator = start_value_krw + weighted_flow
    if denominator <= 0:
        return None
    return (end_value_krw - start_value_krw - total_flow) / denominator


class PortfolioPerformanceService:
    """Rebuild DB-only cash-flow-neutral portfolio performance accounting."""

    def __init__(
        self,
        session: Session,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.now_fn = now_fn

    def rebuild(
        self,
        user_id: int,
        *,
        exchange: str = "UPBIT",
        valuation_plans: Iterable[CashFlowValuationPlan] | None = None,
        apply: bool = False,
    ) -> PortfolioPerformanceResult:
        normalized_exchange = exchange.strip().upper()
        snapshots = tuple(
            self.session.scalars(
                select(PortfolioSnapshot)
                .where(
                    PortfolioSnapshot.user_id == user_id,
                    PortfolioSnapshot.exchange == normalized_exchange,
                )
                .order_by(PortfolioSnapshot.captured_at, PortfolioSnapshot.id)
            )
        )
        valuations = tuple(
            valuation_plans or self._stored_valuations(user_id, normalized_exchange)
        )
        activities = tuple(
            self.session.scalars(
                select(AccountActivity)
                .where(
                    AccountActivity.user_id == user_id,
                    AccountActivity.exchange == normalized_exchange,
                    or_(
                        (
                            (AccountActivity.source_type == "UPBIT_DEPOSIT")
                            & (AccountActivity.activity_type == "DEPOSIT")
                            & (AccountActivity.state == "ACCEPTED")
                        ),
                        (
                            (AccountActivity.source_type == "UPBIT_WITHDRAWAL")
                            & (AccountActivity.activity_type == "WITHDRAWAL")
                            & (AccountActivity.state == "DONE")
                        ),
                    ),
                )
                .order_by(AccountActivity.completed_at, AccountActivity.id)
            )
        )
        coverage = {
            row.source_type: row
            for row in self.session.scalars(
                select(AccountActivitySyncState).where(
                    AccountActivitySyncState.user_id == user_id,
                    AccountActivitySyncState.exchange == normalized_exchange,
                    AccountActivitySyncState.source_type.in_(REQUIRED_COVERAGE_SOURCES),
                )
            )
        }
        plans = self._calculate(snapshots, valuations, coverage, activities)
        existing = {
            row.portfolio_snapshot_id: row
            for row in self.session.scalars(
                select(PortfolioPerformanceSnapshot).where(
                    PortfolioPerformanceSnapshot.user_id == user_id,
                    PortfolioPerformanceSnapshot.exchange == normalized_exchange,
                )
            )
        }
        new_count = sum(plan.portfolio_snapshot_id not in existing for plan in plans)
        if apply:
            for plan in plans:
                row = existing.get(plan.portfolio_snapshot_id)
                values = asdict(plan)
                if row is None:
                    self.session.add(PortfolioPerformanceSnapshot(**values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
            self.session.flush()
        return PortfolioPerformanceResult(
            plans=plans,
            new_count=new_count,
            update_count=len(plans) - new_count,
        )

    def _calculate(
        self,
        snapshots: tuple[PortfolioSnapshot, ...],
        valuations: tuple[CashFlowValuationPlan, ...],
        coverage: dict[str, AccountActivitySyncState],
        activities: tuple[AccountActivity, ...] | None = None,
    ) -> tuple[PerformancePlan, ...]:
        plans: list[PerformancePlan] = []
        for index, snapshot in enumerate(snapshots):
            previous = snapshots[index - 1] if index else None
            if previous is None:
                plans.append(self._baseline(snapshot))
                continue
            if (
                _complete_nav(snapshot) is not None
                and plans[-1].performance_index is None
            ):
                plans.append(
                    self._baseline(
                        snapshot,
                        previous=previous,
                        safe_reason="REBASELINE_AFTER_PARTIAL_GAP",
                    )
                )
                continue
            plans.append(
                self._period(
                    snapshot,
                    previous,
                    plans[-1],
                    valuations,
                    coverage,
                    activities,
                )
            )
        return tuple(plans)

    def _baseline(
        self,
        snapshot: PortfolioSnapshot,
        *,
        previous: PortfolioSnapshot | None = None,
        safe_reason: str = "NO_PREVIOUS_SNAPSHOT",
    ) -> PerformancePlan:
        end_at = _db_utc(snapshot.captured_at)
        end_value = _complete_nav(snapshot)
        if end_value is None:
            return self._partial(snapshot, None, None, end_at, "NAV_INCOMPLETE")
        return PerformancePlan(
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            portfolio_snapshot_id=snapshot.id,
            previous_portfolio_snapshot_id=previous.id
            if previous is not None
            else None,
            period_start_at=None,
            period_end_at=end_at,
            start_value_krw=None,
            end_value_krw=end_value,
            external_inflow_krw=Decimal("0"),
            external_outflow_krw=Decimal("0"),
            net_external_flow_krw=Decimal("0"),
            return_method=RETURN_METHOD,
            period_return_percentage=None,
            cumulative_return_percentage=Decimal("0"),
            performance_index=INDEX_BASE,
            high_water_mark_index=INDEX_BASE,
            high_water_mark_krw=end_value,
            drawdown_index=Decimal("0"),
            drawdown_krw=Decimal("0"),
            drawdown_percentage=Decimal("0"),
            max_drawdown_percentage=Decimal("0"),
            performance_status="BASELINE",
            safe_reason=safe_reason,
            calculated_at=self.now_fn(),
        )

    def _period(
        self,
        snapshot: PortfolioSnapshot,
        previous: PortfolioSnapshot,
        previous_plan: PerformancePlan,
        valuations: tuple[CashFlowValuationPlan, ...],
        coverage: dict[str, AccountActivitySyncState],
        activities: tuple[AccountActivity, ...] | None,
    ) -> PerformancePlan:
        start_at = _db_utc(previous.captured_at)
        end_at = _db_utc(snapshot.captured_at)
        start_value = _complete_nav(previous)
        end_value = _complete_nav(snapshot)
        if start_value is None or end_value is None:
            return self._partial(snapshot, previous, start_at, end_at, "NAV_INCOMPLETE")
        if end_at <= start_at:
            return self._partial(snapshot, previous, start_at, end_at, "INVALID_PERIOD")
        if previous_plan.performance_index is None:
            return self._partial(
                snapshot, previous, start_at, end_at, "CUMULATIVE_CHAIN_BROKEN"
            )
        if not self._coverage_complete(coverage, start_at, end_at):
            return self._partial(
                snapshot,
                previous,
                start_at,
                end_at,
                "ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE",
            )
        effective_activity_times = {
            activity.id: _optional_db_utc(activity.completed_at)
            for activity in activities or ()
        }
        if any(value is None for value in effective_activity_times.values()):
            return self._partial(
                snapshot,
                previous,
                start_at,
                end_at,
                "CASH_FLOW_COMPLETION_TIME_MISSING",
            )
        valuation_by_activity = {plan.account_activity_id: plan for plan in valuations}
        if activities is None:
            if any(plan.event_time is None for plan in valuations):
                return self._partial(
                    snapshot,
                    previous,
                    start_at,
                    end_at,
                    "CASH_FLOW_COMPLETION_TIME_MISSING",
                )
            period_values = tuple(
                plan for plan in valuations if start_at < plan.event_time <= end_at
            )
        else:
            expected_ids = tuple(
                activity.id
                for activity in activities
                if start_at < effective_activity_times[activity.id] <= end_at
            )
            if any(
                activity_id not in valuation_by_activity for activity_id in expected_ids
            ):
                return self._partial(
                    snapshot,
                    previous,
                    start_at,
                    end_at,
                    "CASH_FLOW_VALUATION_MISSING",
                )
            period_values = tuple(
                valuation_by_activity[activity_id] for activity_id in expected_ids
            )
            if any(
                plan.event_time != effective_activity_times[plan.account_activity_id]
                for plan in period_values
            ):
                return self._partial(
                    snapshot,
                    previous,
                    start_at,
                    end_at,
                    "CASH_FLOW_VALUATION_EVENT_TIME_STALE",
                )
        if any(
            plan.valuation_status != "COMPLETE"
            or plan.direction not in {"IN", "OUT"}
            or _positive_decimal(plan.cash_flow_value_krw) is None
            or plan.event_time is None
            for plan in period_values
        ):
            return self._partial(
                snapshot, previous, start_at, end_at, "CASH_FLOW_VALUATION_INCOMPLETE"
            )
        inflow = sum(
            (
                plan.cash_flow_value_krw
                for plan in period_values
                if plan.direction == "IN"
            ),
            Decimal("0"),
        )
        outflow = sum(
            (
                plan.cash_flow_value_krw
                for plan in period_values
                if plan.direction == "OUT"
            ),
            Decimal("0"),
        )
        timed_flows = tuple(
            TimedCashFlow(
                event_time=plan.event_time,
                signed_value_krw=(
                    plan.cash_flow_value_krw
                    if plan.direction == "IN"
                    else -plan.cash_flow_value_krw
                ),
            )
            for plan in period_values
        )
        period_return = modified_dietz_return(
            start_value, end_value, start_at, end_at, timed_flows
        )
        if period_return is None:
            return self._partial(
                snapshot, previous, start_at, end_at, "INVALID_DIETZ_DENOMINATOR"
            )
        performance_index = previous_plan.performance_index * (
            Decimal("1") + period_return
        )
        high_water_mark = max(previous_plan.high_water_mark_index, performance_index)
        performance_base_value = (
            previous_plan.high_water_mark_krw
            * INDEX_BASE
            / previous_plan.high_water_mark_index
        )
        adjusted_value_krw = performance_base_value * performance_index / INDEX_BASE
        high_water_mark_krw = max(previous_plan.high_water_mark_krw, adjusted_value_krw)
        drawdown_index = performance_index - high_water_mark
        drawdown_krw = adjusted_value_krw - high_water_mark_krw
        drawdown_percentage = (
            performance_index / high_water_mark - Decimal("1")
        ) * PERCENT
        max_drawdown = min(previous_plan.max_drawdown_percentage, drawdown_percentage)
        return PerformancePlan(
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            portfolio_snapshot_id=snapshot.id,
            previous_portfolio_snapshot_id=previous.id,
            period_start_at=start_at,
            period_end_at=end_at,
            start_value_krw=start_value,
            end_value_krw=end_value,
            external_inflow_krw=_q(inflow),
            external_outflow_krw=_q(outflow),
            net_external_flow_krw=_q(inflow - outflow),
            return_method=RETURN_METHOD,
            period_return_percentage=_q(period_return * PERCENT),
            cumulative_return_percentage=_q(
                (performance_index / INDEX_BASE - Decimal("1")) * PERCENT
            ),
            performance_index=_q(performance_index),
            high_water_mark_index=_q(high_water_mark),
            high_water_mark_krw=_q(high_water_mark_krw),
            drawdown_index=_q(drawdown_index),
            drawdown_krw=_q(drawdown_krw),
            drawdown_percentage=_q(drawdown_percentage),
            max_drawdown_percentage=_q(max_drawdown),
            performance_status="COMPLETE",
            safe_reason=None,
            calculated_at=self.now_fn(),
        )

    def _partial(
        self,
        snapshot: PortfolioSnapshot,
        previous: PortfolioSnapshot | None,
        start_at: datetime | None,
        end_at: datetime,
        reason: str,
    ) -> PerformancePlan:
        return PerformancePlan(
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            portfolio_snapshot_id=snapshot.id,
            previous_portfolio_snapshot_id=previous.id if previous else None,
            period_start_at=start_at,
            period_end_at=end_at,
            start_value_krw=_complete_nav(previous) if previous else None,
            end_value_krw=_complete_nav(snapshot),
            external_inflow_krw=None,
            external_outflow_krw=None,
            net_external_flow_krw=None,
            return_method=RETURN_METHOD,
            period_return_percentage=None,
            cumulative_return_percentage=None,
            performance_index=None,
            high_water_mark_index=None,
            high_water_mark_krw=None,
            drawdown_index=None,
            drawdown_krw=None,
            drawdown_percentage=None,
            max_drawdown_percentage=None,
            performance_status="PARTIAL",
            safe_reason=reason,
            calculated_at=self.now_fn(),
        )

    @staticmethod
    def _coverage_complete(
        coverage: dict[str, AccountActivitySyncState],
        start_at: datetime,
        end_at: datetime,
    ) -> bool:
        for source in REQUIRED_COVERAGE_SOURCES:
            state = coverage.get(source)
            if (
                state is None
                or state.sync_status != "COMPLETE"
                or state.coverage_start_at is None
                or state.coverage_end_at is None
                or _db_utc(state.coverage_start_at) > start_at
                or _db_utc(state.coverage_end_at) < end_at
            ):
                return False
        return True

    def _stored_valuations(
        self, user_id: int, exchange: str
    ) -> tuple[CashFlowValuationPlan, ...]:
        rows = self.session.scalars(
            select(AccountCashFlowValuation)
            .where(
                AccountCashFlowValuation.user_id == user_id,
                AccountCashFlowValuation.exchange == exchange,
            )
            .order_by(AccountCashFlowValuation.event_time, AccountCashFlowValuation.id)
        )
        return tuple(
            CashFlowValuationPlan(
                account_activity_id=row.account_activity_id,
                user_id=row.user_id,
                exchange=row.exchange,
                direction=row.direction,
                currency=row.currency,
                native_amount=(
                    Decimal(row.native_amount)
                    if row.native_amount is not None
                    else None
                ),
                event_time=_optional_db_utc(row.event_time),
                valuation_price_krw=(
                    Decimal(row.valuation_price_krw)
                    if row.valuation_price_krw is not None
                    else None
                ),
                cash_flow_value_krw=(
                    Decimal(row.cash_flow_value_krw)
                    if row.cash_flow_value_krw is not None
                    else None
                ),
                price_source=row.price_source,
                valuation_status=row.valuation_status,
                safe_reason=row.safe_reason,
                valued_at=_db_utc(row.valued_at),
            )
            for row in rows
        )


def _complete_nav(snapshot: PortfolioSnapshot | None) -> Decimal | None:
    if snapshot is None or snapshot.valuation_status != "COMPLETE":
        return None
    return _finite_decimal(snapshot.total_value_krw)


def _finite_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _positive_decimal(value: object) -> Decimal | None:
    parsed = _finite_decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_db_utc(value: datetime | None) -> datetime | None:
    return _db_utc(value) if value is not None else None


def _microseconds(value) -> int:
    return ((value.days * 86400 + value.seconds) * 1_000_000) + value.microseconds


def _q(value: Decimal) -> Decimal:
    return value.quantize(NUMERIC_QUANTUM, rounding=ROUND_HALF_EVEN)
