from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from time import monotonic, sleep

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    AccountActivity,
    AccountActivitySyncState,
    OrderLog,
    PortfolioSnapshot,
    TradeRecommendation,
)
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import UpbitOrderReadError


UPBIT_CLOSED_ORDER = "UPBIT_CLOSED_ORDER"
UPBIT_DEPOSIT = "UPBIT_DEPOSIT"
UPBIT_WITHDRAWAL = "UPBIT_WITHDRAWAL"
SOURCE_TYPES = (UPBIT_CLOSED_ORDER, UPBIT_DEPOSIT, UPBIT_WITHDRAWAL)
CLOSED_ORDER_LIMIT = 1000
TRANSFER_PAGE_LIMIT = 100
MAX_TRANSFER_PAGES = 10000
MAX_CLOSED_ORDER_WINDOW = timedelta(days=7)
MIN_CLOSED_ORDER_WINDOW = timedelta(seconds=1)
ACCOUNT_ACTIVITY_PRIVATE_REQUEST_INTERVAL_SECONDS = 0.1
BOT_IDENTIFIER_PATTERN = re.compile(r"recommendation-([1-9][0-9]*)\Z")


class AccountActivityNormalizationError(ValueError):
    pass


class AccountActivityCoverageError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedAccountActivity:
    source_type: str
    activity_type: str
    origin: str
    exchange_activity_id: str
    market: str | None
    currency: str | None
    side: str | None
    order_type: str | None
    identifier: str | None
    state: str
    amount: Decimal | None
    quantity: Decimal | None
    executed_quantity: Decimal | None
    executed_funds_krw: Decimal | None
    paid_fee: Decimal | None
    fee_currency: str | None
    cash_flow_direction: str | None
    occurred_at: datetime
    completed_at: datetime | None
    source_metadata: dict[str, Any] | None


@dataclass(frozen=True)
class AccountActivitySourceResult:
    source_type: str
    status: str
    fetched_count: int
    new_count: int
    update_count: int
    bot_order_count: int
    external_order_count: int
    coverage_start_at: datetime
    coverage_end_at: datetime
    safe_error_code: str | None = None


@dataclass(frozen=True)
class AccountActivitySyncResult:
    apply: bool
    sources: tuple[AccountActivitySourceResult, ...]

    @property
    def complete(self) -> bool:
        return all(source.status == "COMPLETE" for source in self.sources)

    @property
    def cash_flow_in_count(self) -> int:
        return sum(
            source.fetched_count
            for source in self.sources
            if source.source_type == UPBIT_DEPOSIT and source.status == "COMPLETE"
        )

    @property
    def cash_flow_out_count(self) -> int:
        return sum(
            source.fetched_count
            for source in self.sources
            if source.source_type == UPBIT_WITHDRAWAL and source.status == "COMPLETE"
        )


class AccountActivitySyncService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        overlap: timedelta = timedelta(days=7),
        sleep_fn: Callable[[float], None] = sleep,
        monotonic_fn: Callable[[], float] = monotonic,
        private_request_interval_seconds: float = (
            ACCOUNT_ACTIVITY_PRIVATE_REQUEST_INTERVAL_SECONDS
        ),
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()
        self.now_fn = now_fn
        self.overlap = overlap
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.private_request_interval_seconds = max(
            ACCOUNT_ACTIVITY_PRIVATE_REQUEST_INTERVAL_SECONDS,
            private_request_interval_seconds,
        )
        self._last_private_request_at: float | None = None

    def run(
        self,
        user_id: int,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        apply: bool = False,
    ) -> AccountActivitySyncResult:
        end = _aware_utc(end_at or self.now_fn())
        explicit_start = _aware_utc(start_at) if start_at is not None else None
        baseline = self._initial_baseline(user_id) if explicit_start is None else None
        self.session.rollback()
        results: list[AccountActivitySourceResult] = []

        for source_type in SOURCE_TYPES:
            source_start = explicit_start or self._source_start(
                user_id, source_type, baseline
            )
            self.session.rollback()
            if source_start >= end:
                raise ValueError("start_at must be earlier than end_at")
            try:
                normalized = self._fetch_source(source_type, source_start, end)
                if source_type == UPBIT_CLOSED_ORDER:
                    normalized = self._classify_orders(user_id, normalized)
                result = self._diff_and_optionally_apply(
                    user_id,
                    source_type,
                    normalized,
                    source_start,
                    end,
                    apply=apply,
                )
            except (
                UpbitOrderReadError,
                AccountActivityNormalizationError,
                AccountActivityCoverageError,
            ) as error:
                self.session.rollback()
                status, code = self._safe_failure(error)
                if apply:
                    self._record_failure(user_id, source_type, status, code)
                    self.session.commit()
                result = AccountActivitySourceResult(
                    source_type=source_type,
                    status=status,
                    fetched_count=0,
                    new_count=0,
                    update_count=0,
                    bot_order_count=0,
                    external_order_count=0,
                    coverage_start_at=source_start,
                    coverage_end_at=end,
                    safe_error_code=code,
                )
            results.append(result)

        if not apply:
            self.session.rollback()
        return AccountActivitySyncResult(apply=apply, sources=tuple(results))

    def _initial_baseline(self, user_id: int) -> datetime:
        first_live_order = self.session.scalar(
            select(func.min(OrderLog.created_at)).where(
                OrderLog.user_id == user_id,
                OrderLog.exchange == "UPBIT",
                OrderLog.trading_mode == "LIVE",
            )
        )
        first_portfolio = self.session.scalar(
            select(func.min(PortfolioSnapshot.captured_at)).where(
                PortfolioSnapshot.user_id == user_id,
                PortfolioSnapshot.exchange == "UPBIT",
            )
        )
        candidates = [
            _database_utc(value)
            for value in (first_live_order, first_portfolio)
            if value is not None
        ]
        if not candidates:
            raise ValueError(
                "No account activity baseline exists; provide an explicit --start-at"
            )
        return min(candidates) - self.overlap

    def _source_start(
        self, user_id: int, source_type: str, baseline: datetime | None
    ) -> datetime:
        coverage_end = self.session.scalar(
            select(AccountActivitySyncState.coverage_end_at).where(
                AccountActivitySyncState.user_id == user_id,
                AccountActivitySyncState.exchange == "UPBIT",
                AccountActivitySyncState.source_type == source_type,
            )
        )
        if coverage_end is not None:
            return _database_utc(coverage_end) - self.overlap
        if baseline is None:
            raise ValueError("No initial account activity baseline is available")
        return baseline

    def _fetch_source(
        self, source_type: str, start_at: datetime, end_at: datetime
    ) -> list[NormalizedAccountActivity]:
        if source_type == UPBIT_CLOSED_ORDER:
            rows = self._fetch_closed_orders(start_at, end_at)
            return [normalize_closed_order(row) for row in rows]
        if source_type == UPBIT_DEPOSIT:
            rows = self._fetch_transfer_pages(
                self.upbit_client.get_deposits, start_at, end_at
            )
            return [normalize_deposit(row) for row in rows]
        rows = self._fetch_transfer_pages(
            self.upbit_client.get_withdrawals, start_at, end_at
        )
        return [normalize_withdrawal(row) for row in rows]

    def _fetch_closed_orders(
        self, start_at: datetime, end_at: datetime
    ) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        window_start = start_at
        while window_start < end_at:
            window_end = min(window_start + MAX_CLOSED_ORDER_WINDOW, end_at)
            rows.extend(self._fetch_closed_window(window_start, window_end))
            window_start = window_end
        return _deduplicate_by_uuid(rows)

    def _fetch_closed_window(
        self, start_at: datetime, end_at: datetime
    ) -> list[Mapping[str, Any]]:
        self._pace_private_read()
        rows = self.upbit_client.get_closed_orders(
            start_time=start_at.isoformat(),
            end_time=end_at.isoformat(),
            limit=CLOSED_ORDER_LIMIT,
            order_by="asc",
        )
        if len(rows) < CLOSED_ORDER_LIMIT:
            return rows
        if end_at - start_at <= MIN_CLOSED_ORDER_WINDOW:
            raise AccountActivityCoverageError(
                "Closed-order response remains truncated at minimum window"
            )
        midpoint = start_at + (end_at - start_at) / 2
        return self._fetch_closed_window(
            start_at, midpoint
        ) + self._fetch_closed_window(midpoint, end_at)

    def _fetch_transfer_pages(
        self,
        fetch_page: Callable[..., list[dict[str, Any]]],
        start_at: datetime,
        end_at: datetime,
    ) -> list[Mapping[str, Any]]:
        collected: list[Mapping[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        request_count = 0
        while True:
            request_count += 1
            if request_count > MAX_TRANSFER_PAGES:
                raise AccountActivityCoverageError(
                    "Transfer pagination exceeded safe page limit"
                )
            self._pace_private_read()
            params: dict[str, object] = {
                "limit": TRANSFER_PAGE_LIMIT,
                "order_by": "desc",
            }
            if cursor is not None:
                params["to"] = cursor
            rows = fetch_page(**params)
            if not rows:
                break
            next_cursor = _required_text(rows[-1].get("uuid"), "uuid")
            if next_cursor in seen_cursors:
                raise AccountActivityCoverageError(
                    "Repeated transfer pagination cursor"
                )
            seen_cursors.add(next_cursor)
            timestamps = [
                _timestamp(row.get("created_at"), "created_at") for row in rows
            ]
            collected.extend(
                row
                for row, occurred_at in zip(rows, timestamps, strict=True)
                if start_at <= occurred_at <= end_at
            )
            if len(rows) < TRANSFER_PAGE_LIMIT or min(timestamps) < start_at:
                break
            cursor = next_cursor
        return _deduplicate_by_uuid(collected)

    def _pace_private_read(self) -> None:
        now = self.monotonic_fn()
        if self._last_private_request_at is not None:
            remaining = self.private_request_interval_seconds - (
                now - self._last_private_request_at
            )
            if remaining > 0:
                self.sleep_fn(remaining)
                now = self.monotonic_fn()
        self._last_private_request_at = now

    def _classify_orders(
        self, user_id: int, activities: list[NormalizedAccountActivity]
    ) -> list[NormalizedAccountActivity]:
        uuids = [activity.exchange_activity_id for activity in activities]
        matching_uuids = set(
            self.session.scalars(
                select(OrderLog.exchange_order_id).where(
                    OrderLog.user_id == user_id,
                    OrderLog.exchange == "UPBIT",
                    OrderLog.exchange_order_id.in_(uuids),
                )
            )
        )
        recommendation_ids = {
            int(match.group(1))
            for activity in activities
            if activity.identifier
            and (match := BOT_IDENTIFIER_PATTERN.fullmatch(activity.identifier))
        }
        matching_recommendations = set(
            self.session.scalars(
                select(OrderLog.recommendation_id)
                .join(
                    TradeRecommendation,
                    TradeRecommendation.id == OrderLog.recommendation_id,
                )
                .where(
                    OrderLog.user_id == user_id,
                    OrderLog.exchange == "UPBIT",
                    TradeRecommendation.user_id == user_id,
                    TradeRecommendation.exchange == "UPBIT",
                    OrderLog.recommendation_id.in_(recommendation_ids),
                )
            )
        )
        classified: list[NormalizedAccountActivity] = []
        for activity in activities:
            match = (
                BOT_IDENTIFIER_PATTERN.fullmatch(activity.identifier)
                if activity.identifier
                else None
            )
            bot_identifier = bool(
                match and int(match.group(1)) in matching_recommendations
            )
            origin = (
                "BOT"
                if activity.exchange_activity_id in matching_uuids or bot_identifier
                else "EXTERNAL"
            )
            classified.append(replace(activity, origin=origin))
        return classified

    def _diff_and_optionally_apply(
        self,
        user_id: int,
        source_type: str,
        activities: list[NormalizedAccountActivity],
        coverage_start_at: datetime,
        coverage_end_at: datetime,
        *,
        apply: bool,
    ) -> AccountActivitySourceResult:
        ids = [activity.exchange_activity_id for activity in activities]
        existing = {
            row.exchange_activity_id: row
            for row in self.session.scalars(
                select(AccountActivity).where(
                    AccountActivity.user_id == user_id,
                    AccountActivity.exchange == "UPBIT",
                    AccountActivity.source_type == source_type,
                    AccountActivity.exchange_activity_id.in_(ids),
                )
            )
        }
        new_count = 0
        update_count = 0
        for activity in activities:
            row = existing.get(activity.exchange_activity_id)
            if row is None:
                new_count += 1
                if apply:
                    self.session.add(self._new_model(user_id, activity))
            elif self._has_mutable_change(row, activity):
                update_count += 1
                if apply:
                    self._update_mutable_fields(row, activity)
        if apply:
            self._record_success(
                user_id, source_type, coverage_start_at, coverage_end_at
            )
            self.session.commit()
        bot_count = sum(activity.origin == "BOT" for activity in activities)
        external_count = sum(activity.origin == "EXTERNAL" for activity in activities)
        return AccountActivitySourceResult(
            source_type=source_type,
            status="COMPLETE",
            fetched_count=len(activities),
            new_count=new_count,
            update_count=update_count,
            bot_order_count=bot_count,
            external_order_count=external_count,
            coverage_start_at=coverage_start_at,
            coverage_end_at=coverage_end_at,
        )

    @staticmethod
    def _new_model(
        user_id: int, activity: NormalizedAccountActivity
    ) -> AccountActivity:
        return AccountActivity(
            user_id=user_id,
            exchange="UPBIT",
            **activity.__dict__,
        )

    @staticmethod
    def _mutable_values(activity: NormalizedAccountActivity) -> tuple[object, ...]:
        return (
            activity.state,
            activity.completed_at,
            activity.executed_quantity,
            activity.executed_funds_krw,
            activity.paid_fee,
            activity.source_metadata,
        )

    @classmethod
    def _has_mutable_change(
        cls, row: AccountActivity, activity: NormalizedAccountActivity
    ) -> bool:
        return (
            row.state,
            row.completed_at,
            row.executed_quantity,
            row.executed_funds_krw,
            row.paid_fee,
            row.source_metadata,
        ) != cls._mutable_values(activity)

    @staticmethod
    def _update_mutable_fields(
        row: AccountActivity, activity: NormalizedAccountActivity
    ) -> None:
        row.state = activity.state
        row.completed_at = activity.completed_at
        row.executed_quantity = activity.executed_quantity
        row.executed_funds_krw = activity.executed_funds_krw
        row.paid_fee = activity.paid_fee
        row.source_metadata = activity.source_metadata

    def _get_or_create_state(
        self, user_id: int, source_type: str
    ) -> AccountActivitySyncState:
        state = self.session.scalar(
            select(AccountActivitySyncState).where(
                AccountActivitySyncState.user_id == user_id,
                AccountActivitySyncState.exchange == "UPBIT",
                AccountActivitySyncState.source_type == source_type,
            )
        )
        if state is None:
            state = AccountActivitySyncState(
                user_id=user_id,
                exchange="UPBIT",
                source_type=source_type,
                sync_status="NEVER_SYNCED",
            )
            self.session.add(state)
        return state

    def _record_success(
        self,
        user_id: int,
        source_type: str,
        coverage_start_at: datetime,
        coverage_end_at: datetime,
    ) -> None:
        now = _aware_utc(self.now_fn())
        state = self._get_or_create_state(user_id, source_type)
        state.sync_status = "COMPLETE"
        state.last_attempt_at = now
        state.last_success_at = now
        if state.coverage_start_at is None:
            state.coverage_start_at = coverage_start_at
        else:
            state.coverage_start_at = min(
                _database_utc(state.coverage_start_at), coverage_start_at
            )
        state.coverage_end_at = coverage_end_at
        state.last_safe_error_code = None

    def _record_failure(
        self, user_id: int, source_type: str, status: str, code: str
    ) -> None:
        state = self._get_or_create_state(user_id, source_type)
        state.sync_status = status
        state.last_attempt_at = _aware_utc(self.now_fn())
        state.last_safe_error_code = code

    @staticmethod
    def _safe_failure(error: Exception) -> tuple[str, str]:
        if isinstance(error, UpbitOrderReadError):
            name = error.safe_error.upbit_error_name
            return (
                ("OUT_OF_SCOPE", "OUT_OF_SCOPE")
                if name == "out_of_scope"
                else ("FAILED", name or error.safe_error.error_type)
            )
        return "FAILED", type(error).__name__


def normalize_closed_order(payload: Mapping[str, Any]) -> NormalizedAccountActivity:
    market = _required_text(payload.get("market"), "market")
    quote_asset, _ = _split_market(market)
    raw_side = _required_text(payload.get("side"), "side")
    side = {"bid": "BUY", "ask": "SELL"}.get(raw_side)
    if side is None:
        raise AccountActivityNormalizationError("Invalid closed-order side")
    order_type = _required_text(payload.get("ord_type"), "ord_type")
    price = _optional_decimal(payload.get("price"), "price")
    return NormalizedAccountActivity(
        source_type=UPBIT_CLOSED_ORDER,
        activity_type="ORDER",
        origin="EXTERNAL",
        exchange_activity_id=_required_text(payload.get("uuid"), "uuid"),
        market=market,
        currency=None,
        side=side,
        order_type=order_type,
        identifier=_optional_text(payload.get("identifier")),
        state=_required_text(payload.get("state"), "state"),
        amount=price if side == "BUY" and order_type == "price" else None,
        quantity=_optional_decimal(payload.get("volume"), "volume"),
        executed_quantity=_optional_decimal(
            payload.get("executed_volume"), "executed_volume"
        ),
        executed_funds_krw=(
            _optional_decimal(payload.get("executed_funds"), "executed_funds")
            if quote_asset == "KRW"
            else None
        ),
        paid_fee=_optional_decimal(payload.get("paid_fee"), "paid_fee"),
        fee_currency=quote_asset,
        cash_flow_direction=None,
        occurred_at=_timestamp(payload.get("created_at"), "created_at"),
        completed_at=None,
        source_metadata=None,
    )


def normalize_deposit(payload: Mapping[str, Any]) -> NormalizedAccountActivity:
    return _normalize_transfer(payload, source_type=UPBIT_DEPOSIT)


def normalize_withdrawal(payload: Mapping[str, Any]) -> NormalizedAccountActivity:
    return _normalize_transfer(payload, source_type=UPBIT_WITHDRAWAL)


def _normalize_transfer(
    payload: Mapping[str, Any], *, source_type: str
) -> NormalizedAccountActivity:
    currency = _required_text(payload.get("currency"), "currency").upper()
    activity_type = "DEPOSIT" if source_type == UPBIT_DEPOSIT else "WITHDRAWAL"
    metadata = {
        key: value
        for key in ("net_type", "transaction_type")
        if isinstance((value := payload.get(key)), str) and value.strip()
    }
    return NormalizedAccountActivity(
        source_type=source_type,
        activity_type=activity_type,
        origin="ACCOUNT_EXTERNAL",
        exchange_activity_id=_required_text(payload.get("uuid"), "uuid"),
        market=None,
        currency=currency,
        side=None,
        order_type=None,
        identifier=None,
        state=_required_text(payload.get("state"), "state"),
        amount=_required_decimal(payload.get("amount"), "amount"),
        quantity=None,
        executed_quantity=None,
        executed_funds_krw=None,
        paid_fee=_optional_decimal(payload.get("fee"), "fee"),
        fee_currency=currency,
        cash_flow_direction="IN" if source_type == UPBIT_DEPOSIT else "OUT",
        occurred_at=_timestamp(payload.get("created_at"), "created_at"),
        completed_at=_optional_timestamp(payload.get("done_at"), "done_at"),
        source_metadata=metadata or None,
    )


def _deduplicate_by_uuid(
    rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    deduplicated: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        deduplicated[_required_text(row.get("uuid"), "uuid")] = row
    return list(deduplicated.values())


def _split_market(market: str) -> tuple[str, str]:
    parts = market.split("-", maxsplit=1)
    if len(parts) != 2 or not all(parts):
        raise AccountActivityNormalizationError("Invalid market")
    return parts[0], parts[1]


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccountActivityNormalizationError(f"Invalid {field}")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AccountActivityNormalizationError("Invalid optional text")
    return value.strip() or None


def _required_decimal(value: object, field: str) -> Decimal:
    result = _optional_decimal(value, field)
    if result is None:
        raise AccountActivityNormalizationError(f"Missing {field}")
    return result


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (bool, dict, list, tuple, set)):
        raise AccountActivityNormalizationError(f"Invalid {field}")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation, TypeError, ValueError:
        raise AccountActivityNormalizationError(f"Invalid {field}") from None
    if not result.is_finite() or result < 0:
        raise AccountActivityNormalizationError(f"Invalid {field}")
    return result


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AccountActivityNormalizationError(f"Invalid {field}")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise AccountActivityNormalizationError(f"Invalid {field}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AccountActivityNormalizationError(f"Invalid {field}")
    return parsed.astimezone(UTC)


def _optional_timestamp(value: object, field: str) -> datetime | None:
    return None if value is None else _timestamp(value, field)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
