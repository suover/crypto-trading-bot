from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    LivePolicyCanaryRun,
    MarketUniverseCandidate,
    OrderLog,
    TradeRecommendation,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderAmbiguousError,
    UpbitOrderNotFoundError,
    UpbitOrderOperationError,
    UpbitOrderReadError,
    UpbitOrderRejectedError,
)
from crypto_trading_bot.exchange.upbit_market_data_provider import (
    UpbitMarketDataProvider,
)
from crypto_trading_bot.services.live_order_safety import (
    MIN_UPBIT_ORDER_AMOUNT_KRW,
    LiveOrderSafetyError,
    assert_live_order_safety_enabled,
    validate_live_order_request,
)
from crypto_trading_bot.services.live_execution_ledger_service import (
    LiveExecutionLedgerService,
)
from crypto_trading_bot.services.canary_trade_provenance_service import (
    CANARY_RECOMMENDATION,
    INVALID_CANARY_PROVENANCE,
    CanaryTradeProvenance,
    CanaryTradeProvenanceService,
)
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertService,
)
from crypto_trading_bot.services.upbit_order_chance_service import (
    UpbitOrderChancePreflightService,
    UpbitOrderChanceValidationError,
    parse_upbit_order_chance,
)


LIVE_ORDER_PLACED_STATUS = "LIVE_PLACED"
LIVE_ORDER_WAIT_STATUS = "LIVE_WAIT"
LIVE_ORDER_DONE_STATUS = "LIVE_DONE"
LIVE_ORDER_CANCELLED_STATUS = "LIVE_CANCELLED"
LIVE_ORDER_EXECUTED_CANCELLED_STATUS = "LIVE_EXECUTED_CANCELLED"
LIVE_ORDER_FAILED_STATUS = "LIVE_FAILED"
LIVE_ORDER_UNKNOWN_STATUS = "LIVE_UNKNOWN"
LIVE_ORDER_STATUS = LIVE_ORDER_PLACED_STATUS
COUNTED_DAILY_LIVE_ORDER_STATUSES = (
    LIVE_ORDER_PLACED_STATUS,
    LIVE_ORDER_WAIT_STATUS,
    LIVE_ORDER_DONE_STATUS,
    LIVE_ORDER_CANCELLED_STATUS,
    LIVE_ORDER_EXECUTED_CANCELLED_STATUS,
    LIVE_ORDER_UNKNOWN_STATUS,
)
KST = ZoneInfo("Asia/Seoul")
CANARY_BUY_BUDGET_LOCK_NAMESPACE = "limited-live-canary-buy-budget-v1b"


def canary_buy_budget_lock_key(activation_id: int, kst_date: str) -> int:
    if activation_id < 1 or not kst_date:
        raise ValueError("Canary budget lock identity is invalid")
    digest = sha256(
        f"{CANARY_BUY_BUDGET_LOCK_NAMESPACE}:{activation_id}:{kst_date}".encode()
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class LiveOrderExecutionError(ValueError):
    """실거래 주문 검증 또는 실행 준비 문제."""


class CanaryOrderSafetyError(LiveOrderExecutionError):
    """Canary BUY 신규 주문이 고정된 자금 안전 정책을 통과하지 못함."""


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


def map_upbit_order_state(state: object, executed_volume: object = None) -> str:
    normalized = str(state or "").strip().lower()
    if normalized == "done":
        return LIVE_ORDER_DONE_STATUS
    if normalized in {"wait", "watch"}:
        return LIVE_ORDER_WAIT_STATUS
    if normalized == "cancel":
        if _has_positive_executed_volume(executed_volume):
            return LIVE_ORDER_EXECUTED_CANCELLED_STATUS
        return LIVE_ORDER_CANCELLED_STATUS
    return LIVE_ORDER_PLACED_STATUS


def _has_positive_executed_volume(value: object) -> bool:
    try:
        executed_volume = Decimal(str(value).strip())
    except InvalidOperation, TypeError, ValueError:
        return False
    return executed_volume.is_finite() and executed_volume > 0


def recommendation_status_for_live_order(local_status: str) -> str:
    if local_status in {LIVE_ORDER_PLACED_STATUS, LIVE_ORDER_WAIT_STATUS}:
        return "LIVE_EXECUTION_PENDING"
    if local_status == LIVE_ORDER_CANCELLED_STATUS:
        return "LIVE_EXECUTION_CANCELLED"
    if local_status == LIVE_ORDER_FAILED_STATUS:
        return "LIVE_EXECUTION_FAILED"
    if local_status == LIVE_ORDER_UNKNOWN_STATUS:
        return "LIVE_EXECUTION_UNKNOWN"
    if local_status in {LIVE_ORDER_DONE_STATUS, LIVE_ORDER_EXECUTED_CANCELLED_STATUS}:
        return "LIVE_EXECUTED"
    return "LIVE_EXECUTION_UNKNOWN"


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
        lock_factory: Callable[[int], PostgresAdvisoryLock] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()
        self.reconciliation_attempts = max(1, reconciliation_attempts)
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep_fn = sleep_fn
        self.lock_factory = lock_factory or PostgresAdvisoryLock
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def build_execution_plan(self, recommendation_id: int) -> LiveOrderExecutionPlan:
        recommendation = self._get_recommendation(recommendation_id)
        self._validate_recommendation_for_live_order(recommendation)
        self._validate_persisted_dynamic_candidate(recommendation)
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
        self._validate_persisted_dynamic_candidate(recommendation)
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
            provenance = self._resolve_canary_provenance_for_audit(recommendation)
            return self._persist_exchange_order(
                recommendation=recommendation,
                plan=recovery_plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                order_response=existing_exchange_order,
                recovered=True,
                create_response=None,
                canary=self._canary_audit(provenance),
                commit=commit,
            )

        plan = self.build_execution_plan(recommendation.id)
        self._revalidate_dynamic_market_at_execution(plan)
        plan = self._revalidate_sell_at_execution(plan)
        try:
            preflight_audit = self._run_order_chance_preflight(plan)
        except UpbitOrderReadError:
            return self._persist_preflight_failure(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                reason_code="ORDER_CHANCE_API_FAILED",
                commit=commit,
            )
        except UpbitOrderChanceValidationError as error:
            return self._persist_preflight_failure(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                reason_code=error.reason_code,
                commit=commit,
            )
        provenance = CanaryTradeProvenanceService(self.session).resolve(recommendation)
        if plan.action == "BUY" and provenance.mode == INVALID_CANARY_PROVENANCE:
            self._block_canary_buy(
                recommendation,
                reason_code="INVALID_CANARY_PROVENANCE",
                detail=provenance.safe_reason,
                commit=commit,
            )
        if plan.action == "BUY" and provenance.mode == CANARY_RECOMMENDATION:
            if plan.amount_krw is None or provenance.per_order_buy_cap is None:
                self._block_canary_buy(
                    recommendation,
                    reason_code="INVALID_CANARY_PROVENANCE",
                    detail="Canary BUY cap is missing",
                    commit=commit,
                )
            if plan.amount_krw > provenance.per_order_buy_cap:
                self._block_canary_buy(
                    recommendation,
                    reason_code="PER_ORDER_LIMIT",
                    detail=(
                        f"amount_krw={plan.amount_krw}, "
                        f"max_buy_order_amount_krw={provenance.per_order_buy_cap}"
                    ),
                    commit=commit,
                )

        return self._execute_new_order(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            identifier=identifier,
            preflight_audit=preflight_audit,
            provenance=provenance,
            commit=commit,
        )

    def _execute_new_order(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        identifier: str,
        preflight_audit: dict[str, Any] | None,
        provenance: CanaryTradeProvenance,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        budget_lock = None
        if plan.action == "BUY" and provenance.mode == CANARY_RECOMMENDATION:
            if provenance.activation is None:
                self._block_canary_buy(
                    recommendation,
                    reason_code="INVALID_CANARY_PROVENANCE",
                    detail="Canary activation is missing",
                    commit=commit,
                )
            day = self.now_fn().astimezone(KST).date()
            budget_lock = self.lock_factory(
                canary_buy_budget_lock_key(provenance.activation.id, day.isoformat())
            )
            if not budget_lock.acquire():
                self._block_canary_buy(
                    recommendation,
                    reason_code="BUDGET_LOCK_BUSY",
                    detail=f"canary_activation_id={provenance.activation.id}",
                    commit=commit,
                )

        try:
            return self._submit_new_order(
                recommendation=recommendation,
                plan=plan,
                approval_request_id=approval_request_id,
                identifier=identifier,
                preflight_audit=preflight_audit,
                provenance=provenance,
                commit=commit,
            )
        finally:
            if budget_lock is not None:
                budget_lock.release()

    def _submit_new_order(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        identifier: str,
        preflight_audit: dict[str, Any] | None,
        provenance: CanaryTradeProvenance,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        self._validate_daily_live_order_limit(recommendation.user_id, plan)
        self._validate_sell_balance(plan)
        daily_used = None
        if plan.action == "BUY" and provenance.mode == CANARY_RECOMMENDATION:
            daily_used = self._validate_canary_daily_buy_limit(
                provenance, plan, commit=commit
            )
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
                    preflight=preflight_audit,
                    canary=self._canary_audit(provenance, daily_used),
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
                preflight=preflight_audit,
                canary=self._canary_audit(provenance, daily_used),
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
                    preflight=preflight_audit,
                    canary=self._canary_audit(provenance, daily_used),
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
            preflight=preflight_audit,
            canary=self._canary_audit(provenance, daily_used),
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
        preflight: dict[str, Any] | None,
        canary: dict[str, Any] | None,
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
                preflight=preflight,
                canary=canary,
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
                preflight=preflight,
                canary=canary,
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
        preflight: dict[str, Any] | None = None,
        canary: dict[str, Any] | None = None,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        return self._persist_result(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            status=map_upbit_order_state(
                order_response.get("state"), order_response.get("executed_volume")
            ),
            exchange_order_id=self._extract_exchange_order_id(order_response),
            audit=self._audit(
                executed=True,
                identifier=identifier,
                recovered=recovered,
                create_response=create_response,
                order_status_response=order_response,
                preflight=preflight,
                canary=canary,
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
        order_status_response = audit.get("order_status_response")
        if isinstance(order_status_response, dict):
            LiveExecutionLedgerService(self.session).sync(
                order_log, order_status_response
            )
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
        preflight: dict[str, Any] | None = None,
        canary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit = {
            "actual_order_executed": executed,
            "identifier": identifier,
            "recovered_by_identifier": recovered,
            "create_response": create_response,
            "order_status_response": order_status_response,
            "safe_error": safe_error,
        }
        if preflight is not None:
            audit["preflight"] = preflight
        if canary is not None:
            audit["canary"] = canary
        return audit

    def _run_order_chance_preflight(
        self, plan: LiveOrderExecutionPlan
    ) -> dict[str, Any] | None:
        if not get_settings().live_order_chance_preflight_enabled:
            return None
        raw_chance = self.upbit_client.get_order_chance(plan.market)
        chance = parse_upbit_order_chance(raw_chance, expected_market=plan.market)
        preflight = UpbitOrderChancePreflightService()
        if plan.action == "BUY":
            if plan.amount_krw is None:
                raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
            return preflight.validate_buy(
                chance,
                market=plan.market,
                approved_amount_krw=plan.amount_krw,
            ).audit
        if plan.quantity is None or plan.amount_krw is None:
            raise UpbitOrderChanceValidationError("INVALID_CHANCE_RESPONSE")
        return preflight.validate_sell(
            chance,
            market=plan.market,
            approved_quantity=plan.quantity,
            current_value_krw=plan.amount_krw,
        ).audit

    def _persist_preflight_failure(
        self,
        *,
        recommendation: TradeRecommendation,
        plan: LiveOrderExecutionPlan,
        approval_request_id: int | None,
        identifier: str,
        reason_code: str,
        commit: bool,
    ) -> LiveOrderExecutionResult:
        audit = UpbitOrderChancePreflightService().failed_audit(
            market=plan.market, reason_code=reason_code
        )
        return self._persist_result(
            recommendation=recommendation,
            plan=plan,
            approval_request_id=approval_request_id,
            status=LIVE_ORDER_FAILED_STATUS,
            exchange_order_id=None,
            audit=self._audit(
                executed=False,
                identifier=identifier,
                preflight=audit,
            ),
            error_message=(
                f"Upbit order chance preflight failed. reason_code={reason_code}"
            ),
            commit=commit,
        )

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
        start_utc, end_utc = self._get_today_range_in_utc(self.now_fn())
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
    def _get_today_range_in_utc(
        now: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        today_kst = (now or datetime.now(UTC)).astimezone(KST).date()
        start_kst = datetime.combine(today_kst, time.min, tzinfo=KST)
        return start_kst.astimezone(UTC), (start_kst + timedelta(days=1)).astimezone(
            UTC
        )

    def _get_today_canary_buy_amount_krw(self, activation_id: int) -> Decimal:
        start_utc, end_utc = self._get_today_range_in_utc(self.now_fn())
        statement = (
            select(func.coalesce(func.sum(OrderLog.amount_krw), 0))
            .join(
                TradeRecommendation,
                TradeRecommendation.id == OrderLog.recommendation_id,
            )
            .join(
                MarketUniverseCandidate,
                MarketUniverseCandidate.id == TradeRecommendation.universe_candidate_id,
            )
            .join(
                AnalysisRun,
                AnalysisRun.id == MarketUniverseCandidate.analysis_run_id,
            )
            .join(
                LivePolicyCanaryRun,
                LivePolicyCanaryRun.analysis_run_id == AnalysisRun.id,
            )
            .where(
                LivePolicyCanaryRun.canary_activation_id == activation_id,
                OrderLog.trading_mode == "LIVE",
                OrderLog.side == "BUY",
                OrderLog.status.in_(COUNTED_DAILY_LIVE_ORDER_STATUSES),
                OrderLog.amount_krw.is_not(None),
                OrderLog.created_at >= start_utc,
                OrderLog.created_at < end_utc,
            )
        )
        return Decimal(str(self.session.scalar(statement) or 0))

    def _validate_canary_daily_buy_limit(
        self,
        provenance: CanaryTradeProvenance,
        plan: LiveOrderExecutionPlan,
        *,
        commit: bool,
    ) -> Decimal:
        if (
            provenance.activation is None
            or provenance.daily_buy_cap is None
            or plan.amount_krw is None
        ):
            raise CanaryOrderSafetyError(
                "Canary order was not executed: invalid Canary provenance"
            )
        used = self._get_today_canary_buy_amount_krw(provenance.activation.id)
        if used + plan.amount_krw > provenance.daily_buy_cap:
            self._block_canary_buy(
                self._get_recommendation(plan.recommendation_id),
                reason_code="DAILY_LIMIT",
                detail=(
                    f"today_canary_buy_amount_krw={used}, "
                    f"new_order_amount_krw={plan.amount_krw}, "
                    f"daily_max_buy_amount_krw={provenance.daily_buy_cap}"
                ),
                commit=commit,
            )
        return used

    def _block_canary_buy(
        self,
        recommendation: TradeRecommendation,
        *,
        reason_code: str,
        detail: str | None,
        commit: bool,
    ) -> None:
        provenance_invalid = reason_code == "INVALID_CANARY_PROVENANCE"
        alert_type = (
            "LIVE_CANARY_PROVENANCE_INVALID"
            if provenance_invalid
            else "LIVE_CANARY_BUY_LIMIT_BLOCKED"
        )
        dedup_reason = "PROVENANCE" if provenance_invalid else reason_code
        OperationalAlertService(self.session).create_canary_alert(
            alert_type=alert_type,
            dedup_key=(
                f"CANARY_PROVENANCE_INVALID:{recommendation.id}"
                if provenance_invalid
                else f"CANARY_BUY_LIMIT_BLOCKED:{recommendation.id}:{dedup_reason}"
            ),
            safe_message=(
                f"Canary BUY was not submitted to Upbit. reason_code={reason_code}"
            ),
            user_id=recommendation.user_id,
            recommendation_id=recommendation.id,
        )
        if commit:
            self.session.commit()
        raise CanaryOrderSafetyError(
            "Canary 주문 제한으로 실제 Upbit 주문은 실행되지 않았습니다. "
            f"reason_code={reason_code}; detail={detail or 'unavailable'}"
        )

    def _resolve_canary_provenance_for_audit(
        self, recommendation: TradeRecommendation
    ) -> CanaryTradeProvenance | None:
        try:
            return CanaryTradeProvenanceService(self.session).resolve(recommendation)
        except Exception:
            return None

    @staticmethod
    def _canary_audit(
        provenance: CanaryTradeProvenance | None,
        daily_used_before_order: Decimal | None = None,
    ) -> dict[str, Any] | None:
        if provenance is None or provenance.mode != CANARY_RECOMMENDATION:
            return None
        return {
            "mode": provenance.mode,
            "canary_activation_id": getattr(provenance.activation, "id", None),
            "canary_run_id": getattr(provenance.canary_run, "id", None),
            "promotion_approval_id": getattr(provenance.promotion_approval, "id", None),
            "activation_signature": provenance.activation_signature,
            "safety_binding_signature": provenance.safety_binding_signature,
            "max_buy_order_amount_krw": str(provenance.per_order_buy_cap),
            "daily_max_buy_amount_krw": str(provenance.daily_buy_cap),
            "daily_used_before_order_krw": (
                str(daily_used_before_order)
                if daily_used_before_order is not None
                else None
            ),
        }

    def _validate_sell_balance(self, plan: LiveOrderExecutionPlan) -> None:
        if plan.action != "SELL" or get_settings().live_order_chance_preflight_enabled:
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
        if (
            not get_settings().live_order_chance_preflight_enabled
            and current_amount < MIN_UPBIT_ORDER_AMOUNT_KRW
        ):
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
        if (
            settings.market_universe_mode == "STATIC"
            and recommendation.market not in settings.allowed_market_list
        ):
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
        LiveOrderExecutionService._validate_universe_candidate(recommendation)
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
    def _validate_universe_candidate(
        recommendation: TradeRecommendation,
    ) -> None:
        settings = get_settings()
        if settings.market_universe_mode == "STATIC":
            return
        if not settings.live_dynamic_market_enabled:
            raise LiveOrderSafetyError("Dynamic live market execution is disabled")
        if recommendation.universe_candidate_id is None:
            raise LiveOrderSafetyError(
                "Dynamic live recommendation has no persisted universe candidate"
            )

    def _revalidate_dynamic_market_at_execution(
        self, plan: LiveOrderExecutionPlan
    ) -> None:
        settings = get_settings()
        if settings.market_universe_mode != "DYNAMIC":
            return
        recommendation = self._get_recommendation(plan.recommendation_id)
        self._validate_persisted_dynamic_candidate(recommendation)
        provider = UpbitMarketDataProvider(self.upbit_client)
        descriptor = next(
            (
                market
                for market in provider.list_markets(quote_asset="KRW")
                if market.market == plan.market
            ),
            None,
        )
        if descriptor is None:
            raise LiveOrderSafetyError(
                f"Market is no longer trading on Upbit. market={plan.market}"
            )
        if plan.action == "BUY" and (descriptor.is_warning or descriptor.is_caution):
            raise LiveOrderSafetyError(
                f"Dynamic BUY market has a current warning or caution. market={plan.market}"
            )
        if plan.action == "BUY":
            tickers = provider.get_tickers(markets=[plan.market])
            if (
                len(tickers) != 1
                or tickers[0].market != plan.market
                or tickers[0].trade_price is None
                or tickers[0].trade_price <= 0
            ):
                raise LiveOrderSafetyError(
                    f"Dynamic BUY ticker is invalid. market={plan.market}"
                )
            if plan.amount_krw is None:
                raise LiveOrderSafetyError("Dynamic BUY amount is missing")
            if settings.live_order_chance_preflight_enabled:
                return
            krw_account = next(
                (
                    account
                    for account in self.upbit_client.get_accounts()
                    if account.get("currency") == "KRW"
                ),
                None,
            )
            try:
                available_krw = Decimal(str(krw_account["balance"]))
            except InvalidOperation, KeyError, TypeError, ValueError:
                raise LiveOrderSafetyError(
                    "Dynamic BUY current KRW balance is invalid"
                ) from None
            if not available_krw.is_finite() or available_krw < plan.amount_krw:
                raise LiveOrderSafetyError(
                    "Dynamic BUY current KRW balance is insufficient"
                )

    def _validate_persisted_dynamic_candidate(
        self, recommendation: TradeRecommendation
    ) -> None:
        settings = get_settings()
        if recommendation.universe_candidate_id is None:
            if settings.market_universe_mode == "STATIC":
                return
            raise LiveOrderSafetyError(
                "Dynamic live recommendation has no persisted universe candidate"
            )
        candidate = self.session.get(
            MarketUniverseCandidate, recommendation.universe_candidate_id
        )
        if candidate is None:
            raise LiveOrderSafetyError("Persisted universe candidate was not found")
        candidate_mode = (
            "STATIC" if candidate.selection_source == "STATIC" else "DYNAMIC"
        )
        if candidate_mode != settings.market_universe_mode:
            raise LiveOrderSafetyError(
                "Recommendation universe mode does not match current runtime mode"
            )
        if settings.market_universe_mode == "STATIC":
            return
        recommendation_run = self.session.get(
            AnalysisRun, recommendation.analysis_run_id
        )
        candidate_run = self.session.get(AnalysisRun, candidate.analysis_run_id)
        if (
            recommendation_run is None
            or candidate_run is None
            or recommendation_run.pipeline_run_id is None
            or recommendation_run.pipeline_run_id != candidate_run.pipeline_run_id
        ):
            raise LiveOrderSafetyError(
                "Recommendation and universe candidate pipeline identity do not match"
            )
        if (
            recommendation_run.user_id != recommendation.user_id
            or recommendation_run.run_type != "AI_RECOMMENDATION"
            or recommendation_run.status != "SUCCESS"
            or candidate_run.user_id != recommendation.user_id
            or candidate_run.run_type != "MARKET_UNIVERSE"
            or candidate_run.status != "SUCCESS"
        ):
            raise LiveOrderSafetyError(
                "Recommendation or universe candidate analysis run is not a valid "
                "successful pipeline stage"
            )
        if (
            candidate.user_id != recommendation.user_id
            or candidate.exchange != recommendation.exchange
            or candidate.market != recommendation.market
            or candidate.quote_asset != "KRW"
        ):
            raise LiveOrderSafetyError(
                "Recommendation does not match its persisted universe candidate"
            )
        if recommendation.action == "BUY" and not candidate.buy_eligible:
            raise LiveOrderSafetyError(
                "Persisted universe candidate is not BUY eligible"
            )
        if recommendation.action == "SELL" and not candidate.sell_eligible:
            raise LiveOrderSafetyError(
                "Persisted universe candidate is not SELL eligible"
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
