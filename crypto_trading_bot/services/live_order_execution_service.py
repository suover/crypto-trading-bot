from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderAmbiguousError,
    UpbitOrderNotFoundError,
    UpbitOrderOperationError,
    UpbitOrderRejectedError,
)
from crypto_trading_bot.services.live_order_safety import (
    MIN_UPBIT_ORDER_AMOUNT_KRW,
    LiveOrderSafetyError,
    assert_live_order_safety_enabled,
    validate_live_order_request,
)


LIVE_ORDER_PLACED_STATUS = "LIVE_PLACED"
LIVE_ORDER_WAIT_STATUS = "LIVE_WAIT"
LIVE_ORDER_DONE_STATUS = "LIVE_DONE"
LIVE_ORDER_CANCELLED_STATUS = "LIVE_CANCELLED"
LIVE_ORDER_FAILED_STATUS = "LIVE_FAILED"
LIVE_ORDER_UNKNOWN_STATUS = "LIVE_UNKNOWN"
LIVE_ORDER_STATUS = LIVE_ORDER_PLACED_STATUS
COUNTED_DAILY_LIVE_ORDER_STATUSES = (
    LIVE_ORDER_PLACED_STATUS,
    LIVE_ORDER_WAIT_STATUS,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_CANCELLED_STATUS,
    LIVE_ORDER_UNKNOWN_STATUS,
)
KST = ZoneInfo("Asia/Seoul")


class LiveOrderExecutionError(ValueError):
    """실거래 주문 검증 또는 실행 준비 문제."""


@dataclass(frozen=True)
class LiveOrderExecutionPlan:
    recommendation_id: int
    exchange: str
    market: str
    action: str
    amount_krw: Decimal | None
    quantity: Decimal | None
    ready_to_execute: bool


@dataclass(frozen=True)
class LiveOrderExecutionResult:
    order_log: OrderLog
    recommendation: TradeRecommendation
    already_executed: bool
    outcome: str = "CONFIRMED"
    recovered: bool = False
    pending: bool = False

    @property
    def confirmed(self) -> bool:
        return self.outcome == "CONFIRMED"

    @property
    def failed(self) -> bool:
        return self.outcome == "FAILED"

    @property
    def unknown(self) -> bool:
        return self.outcome == "UNKNOWN"


def map_upbit_order_state(state: object) -> str:
    normalized = str(state or "").strip().lower()
    if normalized == "done":
        return LIVE_ORDER_DONE_STATUS
    if normalized in {"wait", "watch"}:
        return LIVE_ORDER_WAIT_STATUS
    if normalized == "cancel":
        return LIVE_ORDER_CANCELLED_STATUS
    return LIVE_ORDER_PLACED_STATUS


def recommendation_status_for_live_order(local_status: str) -> str:
    if local_status == LIVE_ORDER_FAILED_STATUS:
        return "LIVE_EXECUTION_FAILED"
    if local_status == LIVE_ORDER_UNKNOWN_STATUS:
        return "LIVE_EXECUTION_UNKNOWN"
    return "LIVE_EXECUTED"


def outcome_for_live_order_status(local_status: str) -> str:
    if local_status == LIVE_ORDER_FAILED_STATUS:
        return "FAILED"
    if local_status == LIVE_ORDER_UNKNOWN_STATUS:
        return "UNKNOWN"
    return "CONFIRMED"


class LiveOrderExecutionService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
        *,
        reconciliation_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()
        self.reconciliation_attempts = max(1, reconciliation_attempts)
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep_fn = sleep_fn

    def build_execution_plan(self, recommendation_id: int) -> LiveOrderExecutionPlan:
        recommendation = self._get_recommendation(recommendation_id)
        self._validate_recommendation_for_live_order(recommendation)
        action = recommendation.action.strip().upper()
        amount_krw = self._to_decimal_or_none(recommendation.recommended_amount_krw)
        quantity = self._to_decimal_or_none(recommendation.recommended_quantity)
        validate_live_order_request(
            settings=get_settings(),
            action=action,
            market=recommendation.market,
            amount_krw=amount_krw,
            quantity=quantity,
        )
        return LiveOrderExecutionPlan(
            recommendation_id=recommendation.id,
            exchange=recommendation.exchange,
            market=recommendation.market,
            action=action,
            amount_krw=amount_krw,
            quantity=quantity,
            ready_to_execute=True,
        )

    def execute(
        self,
        recommendation_id: int,
        approval_request_id: int | None = None,
        commit: bool = True,
    ) -> LiveOrderExecutionResult:
        recommendation = self._get_recommendation_for_update(recommendation_id)
        if recommendation is None:
            raise LiveOrderExecutionError(
                f"Trade recommendation was not found. recommendation_id={recommendation_id}"
            )
        existing = self._get_order_log(recommendation.id)
        if existing is not None:
            return self._existing_result(existing, recommendation)

        self._validate_recommendation_for_remote_lookup(recommendation)
        identifier = f"recommendation-{recommendation.id}"

        try:
            existing_exchange_order = self.upbit_client.get_order(identifier=identifier)
        except UpbitOrderNotFoundError:
            existing_exchange_order = None
        except UpbitOrderOperationError as error:
            plan = self.build_execution_plan(recommendation.id)
            return self._persist_result(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                status=LIVE_ORDER_FAILED_STATUS,
                exchange_order_id=None,
                audit=self._audit(
                    executed=False,
                    identifier=identifier,
                    safe_error=error.safe_error.as_dict(),
                ),
                error_message=str(error),
                commit=commit,
            )

        if existing_exchange_order is not None:
            recovery_plan = self._build_recovery_plan(recommendation)
            return self._persist_exchange_order(
                recommendation=recommendation,
                plan=recovery_plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                order_response=existing_exchange_order,
                recovered=True,
                create_response=None,
                commit=commit,
            )

        plan = self.build_execution_plan(recommendation.id)
        plan = self._revalidate_sell_at_execution(plan)
        self._validate_daily_live_order_limit(recommendation.user_id, plan)
        self._validate_sell_balance(plan)

        try:
            create_response = self._place_live_order(plan, identifier)
        except UpbitOrderRejectedError as error:
            return self._persist_result(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                status=LIVE_ORDER_FAILED_STATUS,
                exchange_order_id=None,
                audit=self._audit(
                    executed=False,
                    identifier=identifier,
                    safe_error=error.safe_error.as_dict(),
                ),
                error_message=str(error),
                commit=commit,
            )
        except UpbitOrderAmbiguousError as error:
            return self._recover_ambiguous_create(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                create_error=error,
                commit=commit,
            )

        exchange_order_id = self._extract_exchange_order_id(create_response)
        try:
            if exchange_order_id:
                order_response = self.upbit_client.get_order(uuid=exchange_order_id)
            else:
                order_response = self.upbit_client.get_order(identifier=identifier)
        except UpbitOrderOperationError as error:
            return self._persist_result(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                status=LIVE_ORDER_PLACED_STATUS,
                exchange_order_id=exchange_order_id,
                audit=self._audit(
                    executed=True,
                    identifier=identifier,
                    create_response=create_response,
                    safe_error=error.safe_error.as_dict(),
                ),
                error_message=str(error),
                commit=commit,
            )

        return self._persist_exchange_order(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            identifier=identifier,
            order_response=order_response,
            recovered=False,
            create_response=create_response,
            commit=commit,
        )

    def _recover_ambiguous_create(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        identifier: str,
        create_error: UpbitOrderAmbiguousError,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        last_error: UpbitOrderOperationError = create_error
        for attempt in range(self.reconciliation_attempts):
            if attempt:
                self.sleep_fn(self.retry_delay_seconds)
            try:
                order_response = self.upbit_client.get_order(identifier=identifier)
            except UpbitOrderOperationError as error:
                last_error = error
                continue
            return self._persist_exchange_order(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                order_response=order_response,
                recovered=True,
                create_response=None,
                commit=commit,
            )
        return self._persist_result(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            status=LIVE_ORDER_UNKNOWN_STATUS,
            exchange_order_id=None,
            audit=self._audit(
                executed=None,
                identifier=identifier,
                safe_error=last_error.safe_error.as_dict(),
            ),
            error_message=str(last_error),
            commit=commit,
        )

    def _persist_exchange_order(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        identifier: str,
        order_response: dict[str, Any],
        recovered: bool,
        create_response: dict[str, Any] | None,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        return self._persist_result(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            status=map_upbit_order_state(order_response.get("state")),
            exchange_order_id=self._extract_exchange_order_id(order_response),
            audit=self._audit(
                executed=True,
                identifier=identifier,
                recovered=recovered,
                create_response=create_response,
                order_status_response=order_response,
            ),
            error_message=None,
            recovered=recovered,
            commit=commit,
        )

    def _persist_result(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        status: str,
        exchange_order_id: str | None,
        audit: dict[str, Any],
        error_message: str | None,
        commit: bool,
        recovered: bool = False,
    ) -> LiveOrderExecutionResult:
        order_log = OrderLog(
            recommendation_id=recommendation.id,
            approval_request_id=approval_request_id,
            user_id=recommendation.user_id,
            trading_mode="LIVE",
            exchange=recommendation.exchange,
            market=recommendation.market,
            side=plan.action,
            order_type="MARKET",
            amount_krw=plan.amount_krw,
            quantity=plan.quantity,
            price=None,
            status=status,
            exchange_order_id=exchange_order_id,
            error_message=error_message,
            raw_response={
                **audit,
                "recommendation_id": recommendation.id,
                "approval_request_id": approval_request_id,
                "market": recommendation.market,
                "side": plan.action,
                "amount_krw": str(plan.amount_krw)
                if plan.amount_krw is not None
                else None,
                "quantity": str(plan.quantity) if plan.quantity is not None else None,
            },
        )
        recommendation.status = recommendation_status_for_live_order(status)
        self.session.add(order_log)
        self.session.flush()
        if commit:
            self.session.commit()
            self.session.refresh(order_log)
            self.session.refresh(recommendation)
        outcome = outcome_for_live_order_status(status)
        return LiveOrderExecutionResult(
            order_log=order_log,
            recommendation=recommendation,
            already_executed=False,
            outcome=outcome,
            recovered=recovered,
            pending=status in {LIVE_ORDER_PLACED_STATUS, LIVE_ORDER_WAIT_STATUS},
        )

    @staticmethod
    def _audit(
        *,
        executed: bool | None,
        identifier: str,
        recovered: bool = False,
        create_response: dict[str, Any] | None = None,
        order_status_response: dict[str, Any] | None = None,
        safe_error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "actual_order_executed": executed,
            "identifier": identifier,
            "recovered_by_identifier": recovered,
            "create_response": create_response,
            "order_status_response": order_status_response,
            "safe_error": safe_error,
        }

    @staticmethod
    def _existing_result(
        order_log: OrderLog,
        recommendation: TradeRecommendation,
    ) -> LiveOrderExecutionResult:
        outcome = outcome_for_live_order_status(order_log.status)
        raw_response = order_log.raw_response or {}
        return LiveOrderExecutionResult(
            order_log=order_log,
            recommendation=recommendation,
            already_executed=True,
            outcome=outcome,
            recovered=bool(raw_response.get("recovered_by_identifier")),
            pending=order_log.status
            in {LIVE_ORDER_PLACED_STATUS, LIVE_ORDER_WAIT_STATUS},
        )

    def _validate_daily_live_order_limit(
        self, user_id: int, plan: LiveOrderExecutionPlan
    ) -> None:
        plan_amount = self._get_plan_amount_for_daily_limit(plan)
        if plan_amount is None:
            return
        current = self._get_today_live_order_amount_krw(user_id)
        maximum = Decimal(str(get_settings().daily_max_order_amount_krw))
        if current + plan_amount > maximum:
            raise LiveOrderExecutionError(
                "Daily live order amount limit exceeded. "
                f"today_live_order_amount_krw={current}, "
                f"new_order_amount_krw={plan_amount}, "
                f"daily_max_order_amount_krw={maximum}"
            )

    def _get_today_live_order_amount_krw(self, user_id: int) -> Decimal:
        start_utc, end_utc = self._get_today_range_in_utc()
        statement = select(func.coalesce(func.sum(OrderLog.amount_krw), 0)).where(
            OrderLog.user_id == user_id,
            OrderLog.trading_mode == "LIVE",
            OrderLog.side == "BUY",
            OrderLog.status.in_(COUNTED_DAILY_LIVE_ORDER_STATUSES),
            OrderLog.amount_krw.is_not(None),
            OrderLog.created_at >= start_utc,
            OrderLog.created_at < end_utc,
        )
        return Decimal(str(self.session.scalar(statement) or 0))

    @staticmethod
    def _get_plan_amount_for_daily_limit(
        plan: LiveOrderExecutionPlan,
    ) -> Decimal | None:
        if plan.action != "BUY" or plan.amount_krw is None:
            return None
        return Decimal(str(plan.amount_krw))

    @staticmethod
    def _get_today_range_in_utc() -> tuple[datetime, datetime]:
        today_kst = datetime.now(KST).date()
        start_kst = datetime.combine(today_kst, time.min, tzinfo=KST)
        return start_kst.astimezone(UTC), (start_kst + timedelta(days=1)).astimezone(
            UTC
        )

    def _validate_sell_balance(self, plan: LiveOrderExecutionPlan) -> None:
        if plan.action != "SELL":
            return
        if plan.quantity is None:
            raise LiveOrderExecutionError("SELL live order requires quantity")
        currency = self._get_currency_from_market(plan.market)
        for account in self.upbit_client.get_accounts():
            if account.get("currency") != currency:
                continue
            raw_balance = account.get("balance")
            try:
                available = Decimal(str(raw_balance))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise LiveOrderExecutionError(
                    f"Live sell account balance is invalid. market={plan.market}"
                ) from error
            if not available.is_finite() or available < 0:
                raise LiveOrderExecutionError(
                    f"Live sell account balance is invalid. market={plan.market}"
                )
            if available < plan.quantity:
                raise LiveOrderExecutionError(
                    "Insufficient available balance for live sell order. "
                    f"required_quantity={plan.quantity}, available_balance={available}"
                )
            return
        raise LiveOrderExecutionError(
            f"Live sell account balance was not found. market={plan.market}"
        )

    def _revalidate_sell_at_execution(
        self, plan: LiveOrderExecutionPlan
    ) -> LiveOrderExecutionPlan:
        if plan.action != "SELL":
            return plan
        if plan.quantity is None:
            raise LiveOrderExecutionError("SELL live order requires quantity")
        tickers = self.upbit_client.get_tickers([plan.market])
        if (
            not isinstance(tickers, list)
            or len(tickers) != 1
            or tickers[0].get("market") != plan.market
        ):
            raise LiveOrderExecutionError(
                f"Expected exactly one matching ticker. market={plan.market}"
            )
        try:
            current_price = Decimal(str(tickers[0].get("trade_price")))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise LiveOrderExecutionError(
                f"Current ticker price is invalid. market={plan.market}"
            ) from error
        if not current_price.is_finite() or current_price <= 0:
            raise LiveOrderExecutionError(
                f"Current ticker price must be finite and positive. market={plan.market}"
            )
        current_amount = plan.quantity * current_price
        if current_amount < MIN_UPBIT_ORDER_AMOUNT_KRW:
            raise LiveOrderExecutionError(
                "Current estimated SELL amount is below minimum Upbit order amount. "
                f"amount_krw={current_amount}, minimum={MIN_UPBIT_ORDER_AMOUNT_KRW}"
            )
        return replace(plan, amount_krw=current_amount)

    @staticmethod
    def _get_currency_from_market(market: str) -> str:
        parts = market.split("-", maxsplit=1)
        if len(parts) != 2 or not all(parts):
            raise LiveOrderExecutionError(
                f"Live sell order market format is invalid. market={market}"
            )
        return parts[1]

    def _place_live_order(
        self, plan: LiveOrderExecutionPlan, identifier: str
    ) -> dict[str, Any]:
        if plan.action == "BUY":
            if plan.amount_krw is None:
                raise LiveOrderExecutionError("BUY live order requires amount_krw")
            return self.upbit_client.create_market_buy_order(
                market=plan.market,
                amount_krw=plan.amount_krw,
                identifier=identifier,
            )
        if plan.quantity is None:
            raise LiveOrderExecutionError("SELL live order requires quantity")
        return self.upbit_client.create_market_sell_order(
            market=plan.market,
            quantity=plan.quantity,
            identifier=identifier,
        )

    def _get_recommendation(self, recommendation_id: int) -> TradeRecommendation:
        recommendation = self.session.get(TradeRecommendation, recommendation_id)
        if recommendation is None:
            raise LiveOrderExecutionError(
                f"Trade recommendation was not found. recommendation_id={recommendation_id}"
            )
        return recommendation

    def _get_recommendation_for_update(
        self, recommendation_id: int
    ) -> TradeRecommendation | None:
        return self.session.scalar(
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update()
        )

    def _get_order_log(self, recommendation_id: int) -> OrderLog | None:
        return self.session.scalar(
            select(OrderLog)
            .where(OrderLog.recommendation_id == recommendation_id)
            .with_for_update()
        )

    @staticmethod
    def _validate_recommendation_for_remote_lookup(
        recommendation: TradeRecommendation,
    ) -> None:
        LiveOrderExecutionService._validate_recommendation_for_live_order(
            recommendation, require_trade_ratio=False
        )
        settings = get_settings()
        assert_live_order_safety_enabled(settings)
        if recommendation.market not in settings.allowed_market_list:
            raise LiveOrderSafetyError(
                "Live order market is not allowed. "
                f"market={recommendation.market}, "
                f"allowed_markets={settings.allowed_market_list}"
            )

    @staticmethod
    def _build_recovery_plan(
        recommendation: TradeRecommendation,
    ) -> LiveOrderExecutionPlan:
        return LiveOrderExecutionPlan(
            recommendation_id=recommendation.id,
            exchange=recommendation.exchange,
            market=recommendation.market,
            action=recommendation.action.strip().upper(),
            amount_krw=LiveOrderExecutionService._to_decimal_or_none(
                recommendation.recommended_amount_krw
            ),
            quantity=LiveOrderExecutionService._to_decimal_or_none(
                recommendation.recommended_quantity
            ),
            ready_to_execute=True,
        )

    @staticmethod
    def _validate_recommendation_for_live_order(
        recommendation: TradeRecommendation,
        *,
        require_trade_ratio: bool = True,
    ) -> None:
        if recommendation.exchange != "UPBIT":
            raise LiveOrderExecutionError(
                f"Live order only supports UPBIT. exchange={recommendation.exchange}"
            )
        if recommendation.status != "APPROVED":
            raise LiveOrderExecutionError(
                "Trade recommendation must be APPROVED for live order. "
                f"recommendation_id={recommendation.id}, status={recommendation.status}"
            )
        if recommendation.action not in {"BUY", "SELL"}:
            raise LiveOrderExecutionError(
                f"Trade recommendation action must be BUY or SELL. action={recommendation.action}"
            )
        if not require_trade_ratio:
            return
        try:
            ratio = Decimal(str(recommendation.trade_ratio))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise LiveOrderExecutionError(
                "BUY or SELL recommendation trade_ratio is invalid. "
                f"trade_ratio={recommendation.trade_ratio}"
            ) from error
        if not ratio.is_finite() or ratio <= 0 or ratio > 1:
            raise LiveOrderExecutionError(
                "BUY or SELL recommendation requires a finite trade_ratio "
                f"greater than 0 and at most 1. trade_ratio={recommendation.trade_ratio}"
            )

    @staticmethod
    def _to_decimal_or_none(value: object) -> Decimal | None:
        return Decimal(str(value)) if value is not None else None

    @staticmethod
    def _extract_exchange_order_id(
        exchange_response: dict[str, Any],
    ) -> str | None:
        value = exchange_response.get("uuid")
        return str(value) if value is not None else None
