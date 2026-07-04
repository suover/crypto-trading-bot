from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import TradeRecommendation
from crypto_trading_bot.exchange.upbit_client import UpbitClient
from crypto_trading_bot.services.live_order_safety import (
    validate_live_order_request,
)


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
    ) -> LiveOrderExecutionPlan:
        plan = self.build_execution_plan(
            recommendation_id=recommendation_id,
        )

        raise NotImplementedError(
            "Live Upbit order execution is intentionally not implemented yet. "
            f"recommendation_id={plan.recommendation_id}"
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
