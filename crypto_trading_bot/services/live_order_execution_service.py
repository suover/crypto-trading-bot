from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import OrderLog, TradeRecommendation
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_safety import (
    validate_live_order_request,
)


LIVE_ORDER_STATUS = "LIVE_PLACED"


class LiveOrderExecutionError(ValueError):
    """실거래 주문 실행 전 검증 또는 실행 중 문제가 있을 때 발생하는 예외."""


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


class LiveOrderExecutionService:
    def __init__(
        self,
        session: Session,
        upbit_client: UpbitClient | None = None,
    ) -> None:
        self.session = session
        self.upbit_client = upbit_client or UpbitClient()

    def build_execution_plan(
        self,
        recommendation_id: int,
    ) -> LiveOrderExecutionPlan:
        recommendation = self._get_recommendation(
            recommendation_id=recommendation_id,
        )

        self._validate_recommendation_for_live_order(
            recommendation=recommendation,
        )

        action = recommendation.action.strip().upper()
        amount_krw = self._to_decimal_or_none(recommendation.recommended_amount_krw)
        quantity = self._to_decimal_or_none(recommendation.recommended_quantity)

        settings = get_settings()

        validate_live_order_request(
            settings=settings,
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
        recommendation = self._get_recommendation_for_update(
            recommendation_id=recommendation_id,
        )

        if recommendation is None:
            raise LiveOrderExecutionError(
                "Trade recommendation was not found. "
                f"recommendation_id={recommendation_id}"
            )

        existing_order_log = self._get_order_log(
            recommendation_id=recommendation.id,
        )

        if existing_order_log is not None:
            return LiveOrderExecutionResult(
                order_log=existing_order_log,
                recommendation=recommendation,
                already_executed=True,
            )

        plan = self.build_execution_plan(
            recommendation_id=recommendation.id,
        )

        exchange_response = self._place_live_order(
            plan=plan,
        )

        exchange_order_id = self._extract_exchange_order_id(
            exchange_response=exchange_response,
        )

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
            status=LIVE_ORDER_STATUS,
            exchange_order_id=exchange_order_id,
            error_message=None,
            raw_response={
                "actual_order_executed": True,
                "recommendation_id": recommendation.id,
                "approval_request_id": approval_request_id,
                "market": recommendation.market,
                "side": plan.action,
                "amount_krw": str(plan.amount_krw)
                if plan.amount_krw is not None
                else None,
                "quantity": str(plan.quantity) if plan.quantity is not None else None,
                "exchange_response": exchange_response,
            },
        )

        recommendation.status = "LIVE_EXECUTED"

        self.session.add(order_log)
        self.session.flush()

        if commit:
            self.session.commit()
            self.session.refresh(order_log)
            self.session.refresh(recommendation)

        return LiveOrderExecutionResult(
            order_log=order_log,
            recommendation=recommendation,
            already_executed=False,
        )

    def _place_live_order(
        self,
        plan: LiveOrderExecutionPlan,
    ) -> dict[str, Any]:
        identifier = f"recommendation-{plan.recommendation_id}"

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

    def _get_recommendation(
        self,
        recommendation_id: int,
    ) -> TradeRecommendation:
        recommendation = self.session.get(
            TradeRecommendation,
            recommendation_id,
        )

        if recommendation is None:
            raise LiveOrderExecutionError(
                "Trade recommendation was not found. "
                f"recommendation_id={recommendation_id}"
            )

        return recommendation

    def _get_recommendation_for_update(
        self,
        recommendation_id: int,
    ) -> TradeRecommendation | None:
        statement = (
            select(TradeRecommendation)
            .where(TradeRecommendation.id == recommendation_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    def _get_order_log(
        self,
        recommendation_id: int,
    ) -> OrderLog | None:
        statement = (
            select(OrderLog)
            .where(OrderLog.recommendation_id == recommendation_id)
            .with_for_update()
        )

        return self.session.scalar(statement)

    @staticmethod
    def _validate_recommendation_for_live_order(
        recommendation: TradeRecommendation,
    ) -> None:
        if recommendation.exchange != "UPBIT":
            raise LiveOrderExecutionError(
                f"Live order only supports UPBIT. exchange={recommendation.exchange}"
            )

        if recommendation.status != "APPROVED":
            raise LiveOrderExecutionError(
                "Trade recommendation must be APPROVED for live order. "
                f"recommendation_id={recommendation.id}, "
                f"status={recommendation.status}"
            )

        if recommendation.action not in {"BUY", "SELL"}:
            raise LiveOrderExecutionError(
                "Trade recommendation action must be BUY or SELL for live order. "
                f"recommendation_id={recommendation.id}, "
                f"action={recommendation.action}"
            )

    @staticmethod
    def _to_decimal_or_none(value: object) -> Decimal | None:
        if value is None:
            return None

        return Decimal(str(value))

    @staticmethod
    def _extract_exchange_order_id(
        exchange_response: dict[str, Any],
    ) -> str | None:
        uuid_value = exchange_response.get("uuid")

        if uuid_value is None:
            return None

        return str(uuid_value)
