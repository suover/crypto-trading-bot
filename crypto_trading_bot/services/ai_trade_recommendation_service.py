from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.ai.trade_advisor import AiTradeAdvice, OpenAITradeAdvisor
from crypto_trading_bot.analysis.indicators import (
    MarketIndicatorResult,
    calculate_market_indicators,
)
from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.db.models import (
    AccountSnapshot,
    AnalysisRun,
    MarketCandle,
    TradeRecommendation,
    User,
)


MIN_RECOMMENDED_ORDER_AMOUNT_KRW = Decimal("5000")


def to_decimal(value: object | None) -> Decimal:
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


def decimal_to_string_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None

    return str(value)


def get_base_currency(market: str) -> str:
    parts = market.split("-")

    if len(parts) != 2:
        raise ValueError(f"Unexpected market format. market={market}")

    return parts[1]


class AiTradeRecommendationService:
    def __init__(
        self,
        session: Session,
        trade_advisor: OpenAITradeAdvisor | None = None,
    ) -> None:
        self.session = session
        self.trade_advisor = trade_advisor or OpenAITradeAdvisor()

    def create_ai_recommendations(
        self,
        user_name: str = "Minsu",
        candle_unit: int = 15,
        candle_count: int = 50,
    ) -> tuple[AnalysisRun, list[TradeRecommendation]]:
        settings = get_settings()
        user = self._get_user(user_name)

        analysis_run = AnalysisRun(
            user_id=user.id,
            run_type="AI_RECOMMENDATION",
            trading_mode=settings.trading_mode,
            status="STARTED",
        )

        self.session.add(analysis_run)
        self.session.flush()

        try:
            recommendations: list[TradeRecommendation] = []

            krw_balance = self._get_latest_balance(
                user_id=user.id,
                exchange="UPBIT",
                currency="KRW",
            )

            for market in settings.allowed_market_list:
                candles = self._get_recent_candles(
                    market=market,
                    candle_unit=candle_unit,
                    count=candle_count,
                )

                if len(candles) < 20:
                    recommendation = self._create_hold_recommendation(
                        analysis_run_id=analysis_run.id,
                        user_id=user.id,
                        market=market,
                        reason="AI 판단에 필요한 캔들 데이터가 부족하여 보류",
                        ai_response={
                            "source": "system_guard",
                            "reason": "not_enough_candles",
                        },
                    )
                    self.session.add(recommendation)
                    recommendations.append(recommendation)
                    continue

                indicators = self._calculate_indicators(
                    market=market,
                    candle_unit=candle_unit,
                    candles=candles,
                )

                base_currency = get_base_currency(market)
                coin_balance = self._get_latest_balance(
                    user_id=user.id,
                    exchange="UPBIT",
                    currency=base_currency,
                )

                context = self._build_ai_context(
                    market=market,
                    indicators=indicators,
                    krw_balance=krw_balance,
                    coin_balance=coin_balance,
                    max_order_amount_krw=Decimal(settings.max_order_amount_krw),
                )

                advice = self.trade_advisor.create_advice(context)

                safe_advice = self._apply_safety_rules(
                    advice=advice,
                    krw_balance=krw_balance,
                    coin_balance=coin_balance,
                    max_order_amount_krw=Decimal(settings.max_order_amount_krw),
                )

                recommendation = TradeRecommendation(
                    analysis_run_id=analysis_run.id,
                    market_snapshot_id=None,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=market,
                    action=safe_advice.action,
                    confidence=safe_advice.confidence,
                    reason=self._build_reason(safe_advice),
                    recommended_amount_krw=safe_advice.recommended_amount_krw,
                    recommended_quantity=safe_advice.recommended_quantity,
                    ai_model=self.trade_advisor.model,
                    ai_response={
                        "context": context,
                        "advice": safe_advice.raw_response,
                    },
                    status="CREATED",
                )

                self.session.add(recommendation)
                recommendations.append(recommendation)

            analysis_run.status = "SUCCESS"
            analysis_run.finished_at = datetime.now(UTC)

            self.session.commit()

            return analysis_run, recommendations

        except Exception as error:
            analysis_run.status = "FAILED"
            analysis_run.error_message = str(error)
            analysis_run.finished_at = datetime.now(UTC)

            self.session.commit()

            raise

    def _get_user(self, user_name: str) -> User:
        user = self.session.scalar(select(User).where(User.name == user_name))

        if user is None:
            raise ValueError(f"User not found. name={user_name}")

        return user

    def _get_recent_candles(
        self,
        market: str,
        candle_unit: int,
        count: int,
    ) -> list[MarketCandle]:
        statement = (
            select(MarketCandle)
            .where(
                MarketCandle.exchange == "UPBIT",
                MarketCandle.market == market,
                MarketCandle.candle_type == "MINUTE",
                MarketCandle.candle_unit == candle_unit,
            )
            .order_by(MarketCandle.candle_at.desc())
            .limit(count)
        )

        candles = list(self.session.scalars(statement))

        return list(reversed(candles))

    def _get_latest_balance(
        self,
        user_id: int,
        exchange: str,
        currency: str,
    ) -> Decimal:
        statement = (
            select(AccountSnapshot)
            .where(
                AccountSnapshot.user_id == user_id,
                AccountSnapshot.exchange == exchange,
                AccountSnapshot.currency == currency,
            )
            .order_by(AccountSnapshot.created_at.desc())
            .limit(1)
        )

        snapshot = self.session.scalar(statement)

        if snapshot is None:
            return Decimal("0")

        return to_decimal(snapshot.balance)

    def _calculate_indicators(
        self,
        market: str,
        candle_unit: int,
        candles: list[MarketCandle],
    ) -> MarketIndicatorResult:
        close_prices = [to_decimal(candle.trade_price) for candle in candles]
        volumes = [to_decimal(candle.candle_acc_trade_volume) for candle in candles]

        return calculate_market_indicators(
            market=market,
            candle_unit=candle_unit,
            close_prices=close_prices,
            volumes=volumes,
        )

    def _build_ai_context(
        self,
        market: str,
        indicators: MarketIndicatorResult,
        krw_balance: Decimal,
        coin_balance: Decimal,
        max_order_amount_krw: Decimal,
    ) -> dict[str, Any]:
        return {
            "exchange": "UPBIT",
            "market": market,
            "candle_unit": indicators.candle_unit,
            "candle_count": indicators.candle_count,
            "latest_price": str(indicators.latest_price),
            "sma_5": decimal_to_string_or_none(indicators.sma_5),
            "sma_20": decimal_to_string_or_none(indicators.sma_20),
            "ema_5": decimal_to_string_or_none(indicators.ema_5),
            "ema_20": decimal_to_string_or_none(indicators.ema_20),
            "rsi_14": decimal_to_string_or_none(indicators.rsi_14),
            "recent_10_candle_change_rate": decimal_to_string_or_none(
                indicators.recent_10_candle_change_rate
            ),
            "volume_ratio_5_to_20": decimal_to_string_or_none(
                indicators.volume_ratio_5_to_20
            ),
            "trend_label": indicators.trend_label,
            "krw_balance": str(krw_balance),
            "coin_balance": str(coin_balance),
            "minimum_order_amount_krw": str(MIN_RECOMMENDED_ORDER_AMOUNT_KRW),
            "max_order_amount_krw": str(max_order_amount_krw),
            "trading_mode": get_settings().trading_mode,
        }

    def _apply_safety_rules(
        self,
        advice: AiTradeAdvice,
        krw_balance: Decimal,
        coin_balance: Decimal,
        max_order_amount_krw: Decimal,
    ) -> AiTradeAdvice:
        if advice.action not in {"BUY", "SELL", "HOLD"}:
            return self._override_to_hold(
                advice=advice,
                reason="AI 응답 action 값이 허용 범위를 벗어나 보류",
            )

        confidence = min(max(advice.confidence, Decimal("0")), Decimal("1"))

        if advice.action == "BUY":
            if krw_balance < MIN_RECOMMENDED_ORDER_AMOUNT_KRW:
                return self._override_to_hold(
                    advice=advice,
                    reason="KRW 잔고가 내부 최소 추천 금액보다 적어 AI 매수 의견을 보류",
                )

            recommended_amount = advice.recommended_amount_krw

            if recommended_amount is None or recommended_amount <= 0:
                recommended_amount = min(max_order_amount_krw, krw_balance)

            recommended_amount = min(
                recommended_amount,
                max_order_amount_krw,
                krw_balance,
            )

            if recommended_amount < MIN_RECOMMENDED_ORDER_AMOUNT_KRW:
                return self._override_to_hold(
                    advice=advice,
                    reason="최종 매수 추천 금액이 최소 주문 기준보다 작아 보류",
                )

            return AiTradeAdvice(
                action="BUY",
                confidence=confidence,
                recommended_amount_krw=recommended_amount,
                recommended_quantity=None,
                reason=advice.reason,
                risk_notes=advice.risk_notes,
                raw_response=advice.raw_response,
            )

        if advice.action == "SELL":
            if coin_balance <= 0:
                return self._override_to_hold(
                    advice=advice,
                    reason="보유 수량이 없어 AI 매도 의견을 보류",
                )

            recommended_quantity = advice.recommended_quantity

            if recommended_quantity is None or recommended_quantity <= 0:
                recommended_quantity = coin_balance

            recommended_quantity = min(recommended_quantity, coin_balance)

            return AiTradeAdvice(
                action="SELL",
                confidence=confidence,
                recommended_amount_krw=None,
                recommended_quantity=recommended_quantity,
                reason=advice.reason,
                risk_notes=advice.risk_notes,
                raw_response=advice.raw_response,
            )

        return AiTradeAdvice(
            action="HOLD",
            confidence=confidence,
            recommended_amount_krw=None,
            recommended_quantity=None,
            reason=advice.reason,
            risk_notes=advice.risk_notes,
            raw_response=advice.raw_response,
        )

    def _override_to_hold(
        self,
        advice: AiTradeAdvice,
        reason: str,
    ) -> AiTradeAdvice:
        raw_response = {
            **advice.raw_response,
            "system_override": reason,
            "original_action": advice.action,
        }

        return AiTradeAdvice(
            action="HOLD",
            confidence=Decimal("0.8000"),
            recommended_amount_krw=None,
            recommended_quantity=None,
            reason=reason,
            risk_notes=advice.risk_notes,
            raw_response=raw_response,
        )

    def _build_reason(self, advice: AiTradeAdvice) -> str:
        return f"{advice.reason} / 리스크 메모: {advice.risk_notes}"

    def _create_hold_recommendation(
        self,
        analysis_run_id: int,
        user_id: int,
        market: str,
        reason: str,
        ai_response: dict[str, Any],
    ) -> TradeRecommendation:
        return TradeRecommendation(
            analysis_run_id=analysis_run_id,
            market_snapshot_id=None,
            user_id=user_id,
            exchange="UPBIT",
            market=market,
            action="HOLD",
            confidence=Decimal("0.8000"),
            reason=reason,
            recommended_amount_krw=None,
            recommended_quantity=None,
            ai_model=self.trade_advisor.model,
            ai_response=ai_response,
            status="CREATED",
        )
