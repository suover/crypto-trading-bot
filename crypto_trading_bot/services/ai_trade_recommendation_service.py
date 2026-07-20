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
    ExchangeMarket,
    MarketCandle,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)
from crypto_trading_bot.services.market_data_context_service import (
    MarketDataContextService,
)


MIN_RECOMMENDED_ORDER_AMOUNT_KRW = Decimal("5000")
MIN_CANDLES_FOR_ADVICE = 20


def to_decimal(value: object | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def decimal_to_string_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


class AiTradeRecommendationService:
    def __init__(
        self,
        session: Session,
        trade_advisor: OpenAITradeAdvisor | None = None,
        registry_service: ExchangeMarketRegistryService | None = None,
        market_data_context_service: MarketDataContextService | None = None,
    ) -> None:
        self.session = session
        self.trade_advisor = trade_advisor or OpenAITradeAdvisor()
        self.registry_service = registry_service or ExchangeMarketRegistryService(
            session
        )
        self.market_data_context_service = (
            market_data_context_service or MarketDataContextService()
        )

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
            krw_balance = self._get_latest_balance(
                user_id=user.id,
                exchange="UPBIT",
                currency="KRW",
            )
            registry_markets = self.registry_service.load_active_markets_for_exchange(
                "UPBIT"
            )
            if not registry_markets:
                raise ValueError("No active UPBIT markets found in registry")

            base_candidates = [
                self._build_candidate(
                    user_id=user.id,
                    registry_market=registry_market,
                    candle_unit=candle_unit,
                    candle_count=candle_count,
                    krw_balance=krw_balance,
                )
                for registry_market in registry_markets
            ]
            market_data_result = self.market_data_context_service.enrich_candidates(
                base_candidates
            )
            candidates = market_data_result.candidates
            context = {
                "account": {
                    "exchange": "UPBIT",
                    "quote_asset": "KRW",
                    "quote_balance_krw": str(krw_balance),
                },
                "trading_mode": settings.trading_mode,
                "external_data_status": market_data_result.external_data_status,
                "market_sentiment": market_data_result.market_sentiment,
                "candidates": candidates,
                "rules": {
                    "minimum_order_amount_krw": str(MIN_RECOMMENDED_ORDER_AMOUNT_KRW),
                    "allowed_actions": ["BUY", "SELL", "HOLD"],
                    "spot_only": True,
                    "conservative": True,
                },
            }

            if all(not candidate["enough_candles"] for candidate in candidates):
                advice = self._create_system_guard_advice(candidates[0])
                source = "system_guard"
                raw_ai_response: dict[str, Any] | None = None
            else:
                advice = self.trade_advisor.create_advice(context)
                source = "openai"
                raw_ai_response = advice.raw_response

            safe_advice = self._apply_safety_rules(
                advice=advice,
                candidates=candidates,
            )
            safety_override = safe_advice.raw_response.get("system_override")
            recommendation = TradeRecommendation(
                analysis_run_id=analysis_run.id,
                market_snapshot_id=None,
                user_id=user.id,
                exchange=safe_advice.exchange,
                market=safe_advice.market,
                action=safe_advice.action,
                confidence=safe_advice.confidence,
                reason=self._build_reason(safe_advice),
                recommended_amount_krw=safe_advice.recommended_amount_krw,
                recommended_quantity=safe_advice.recommended_quantity,
                ai_model=self.trade_advisor.model,
                ai_response={
                    "source": source,
                    "context": context,
                    "raw_ai_response": raw_ai_response,
                    "safe_advice": self._advice_to_dict(safe_advice),
                    "safety_override": safety_override,
                    "alternatives_considered": safe_advice.alternatives_considered,
                },
                status="CREATED",
            )
            self.session.add(recommendation)
            analysis_run.status = "SUCCESS"
            analysis_run.finished_at = datetime.now(UTC)
            self.session.commit()
            return analysis_run, [recommendation]

        except Exception as error:
            analysis_run.status = "FAILED"
            analysis_run.error_message = str(error)
            analysis_run.finished_at = datetime.now(UTC)
            self.session.commit()
            raise

    def _build_candidate(
        self,
        user_id: int,
        registry_market: ExchangeMarket,
        candle_unit: int,
        candle_count: int,
        krw_balance: Decimal,
    ) -> dict[str, Any]:
        candles = self._get_recent_candles(
            market=registry_market.market,
            candle_unit=candle_unit,
            count=candle_count,
        )
        coin_balance = self._get_latest_balance(
            user_id=user_id,
            exchange=registry_market.exchange_code,
            currency=registry_market.base_asset,
        )
        max_order_amount = self.registry_service.calculate_final_max_order_amount(
            registry_market
        )
        enough_candles = len(candles) >= MIN_CANDLES_FOR_ADVICE
        indicators = (
            self._calculate_indicators(
                market=registry_market.market,
                candle_unit=candle_unit,
                candles=candles,
            )
            if enough_candles
            else None
        )
        return self._candidate_to_dict(
            registry_market=registry_market,
            candle_unit=candle_unit,
            candles=candles,
            indicators=indicators,
            krw_balance=krw_balance,
            coin_balance=coin_balance,
            max_order_amount=max_order_amount,
        )

    def _candidate_to_dict(
        self,
        registry_market: ExchangeMarket,
        candle_unit: int,
        candles: list[MarketCandle],
        indicators: MarketIndicatorResult | None,
        krw_balance: Decimal,
        coin_balance: Decimal,
        max_order_amount: Decimal,
    ) -> dict[str, Any]:
        enough_candles = indicators is not None
        latest_price = to_decimal(candles[-1].trade_price) if candles else None
        return {
            "exchange": registry_market.exchange_code,
            "market": registry_market.market,
            "base_asset": registry_market.base_asset,
            "quote_asset": registry_market.quote_asset,
            "coingecko_id": registry_market.coingecko_id,
            "candle_unit": candle_unit,
            "candle_count": len(candles),
            "data_quality": "SUFFICIENT" if enough_candles else "INSUFFICIENT",
            "enough_candles": enough_candles,
            "latest_price": decimal_to_string_or_none(
                indicators.latest_price if indicators else latest_price
            ),
            "sma_5": decimal_to_string_or_none(
                indicators.sma_5 if indicators else None
            ),
            "sma_20": decimal_to_string_or_none(
                indicators.sma_20 if indicators else None
            ),
            "ema_5": decimal_to_string_or_none(
                indicators.ema_5 if indicators else None
            ),
            "ema_20": decimal_to_string_or_none(
                indicators.ema_20 if indicators else None
            ),
            "rsi_14": decimal_to_string_or_none(
                indicators.rsi_14 if indicators else None
            ),
            "recent_10_candle_change_rate": decimal_to_string_or_none(
                indicators.recent_10_candle_change_rate if indicators else None
            ),
            "volume_ratio_5_to_20": decimal_to_string_or_none(
                indicators.volume_ratio_5_to_20 if indicators else None
            ),
            "trend_label": indicators.trend_label if indicators else "판단 보류",
            "quote_balance_krw": str(krw_balance),
            "coin_balance": str(coin_balance),
            "minimum_order_amount_krw": str(MIN_RECOMMENDED_ORDER_AMOUNT_KRW),
            "max_order_amount_krw": str(max_order_amount),
        }

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
        return list(reversed(list(self.session.scalars(statement))))

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
        return Decimal("0") if snapshot is None else to_decimal(snapshot.balance)

    def _calculate_indicators(
        self,
        market: str,
        candle_unit: int,
        candles: list[MarketCandle],
    ) -> MarketIndicatorResult:
        return calculate_market_indicators(
            market=market,
            candle_unit=candle_unit,
            close_prices=[to_decimal(candle.trade_price) for candle in candles],
            volumes=[to_decimal(candle.candle_acc_trade_volume) for candle in candles],
        )

    def _apply_safety_rules(
        self,
        advice: AiTradeAdvice,
        candidates: list[dict[str, Any]],
    ) -> AiTradeAdvice:
        candidate_lookup = {
            (str(candidate["exchange"]), str(candidate["market"])): candidate
            for candidate in candidates
        }
        selected_candidate = candidate_lookup.get((advice.exchange, advice.market))
        if selected_candidate is None:
            fallback = candidates[0]
            return self._override_to_hold(
                advice,
                exchange=str(fallback["exchange"]),
                market=str(fallback["market"]),
                reason="AI가 활성 후보 목록에 없는 마켓을 선택하여 보류했습니다.",
            )

        confidence = min(max(advice.confidence, Decimal("0")), Decimal("1"))
        if advice.action not in {"BUY", "SELL", "HOLD"}:
            return self._override_to_hold(
                advice,
                exchange=advice.exchange,
                market=advice.market,
                reason="AI 응답의 action 값이 허용 범위를 벗어나 보류했습니다.",
            )
        if advice.action in {"BUY", "SELL"} and not bool(
            selected_candidate["enough_candles"]
        ):
            return self._override_to_hold(
                advice,
                exchange=advice.exchange,
                market=advice.market,
                reason="선택한 마켓의 캔들 데이터가 부족하여 거래를 보류했습니다.",
            )

        if advice.action == "BUY":
            quote_balance = to_decimal(selected_candidate["quote_balance_krw"])
            maximum = to_decimal(selected_candidate["max_order_amount_krw"])
            minimum = to_decimal(selected_candidate["minimum_order_amount_krw"])
            if quote_balance < minimum:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="KRW 잔고가 최소 주문 금액보다 적어 매수를 보류했습니다.",
                )
            amount = advice.recommended_amount_krw
            if amount is None or amount <= 0:
                amount = min(maximum, quote_balance)
            amount = min(amount, maximum, quote_balance)
            if amount < minimum:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="최종 매수 금액이 최소 주문 금액보다 적어 보류했습니다.",
                )
            return self._replace_advice(
                advice,
                action="BUY",
                confidence=confidence,
                recommended_amount_krw=amount,
                recommended_quantity=None,
            )

        if advice.action == "SELL":
            coin_balance = to_decimal(selected_candidate["coin_balance"])
            if coin_balance <= 0:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="선택한 자산의 보유 수량이 없어 매도를 보류했습니다.",
                )
            quantity = advice.recommended_quantity
            if quantity is None or quantity <= 0:
                quantity = coin_balance
            return self._replace_advice(
                advice,
                action="SELL",
                confidence=confidence,
                recommended_amount_krw=None,
                recommended_quantity=min(quantity, coin_balance),
            )

        return self._replace_advice(
            advice,
            action="HOLD",
            confidence=confidence,
            recommended_amount_krw=None,
            recommended_quantity=None,
        )

    def _replace_advice(
        self,
        advice: AiTradeAdvice,
        *,
        action: str,
        confidence: Decimal,
        recommended_amount_krw: Decimal | None,
        recommended_quantity: Decimal | None,
    ) -> AiTradeAdvice:
        return AiTradeAdvice(
            action=action,
            exchange=advice.exchange,
            market=advice.market,
            confidence=confidence,
            recommended_amount_krw=recommended_amount_krw,
            recommended_quantity=recommended_quantity,
            reason=advice.reason,
            risk_notes=advice.risk_notes,
            primary_factors=advice.primary_factors,
            alternatives_considered=advice.alternatives_considered,
            raw_response=advice.raw_response,
        )

    def _override_to_hold(
        self,
        advice: AiTradeAdvice,
        *,
        exchange: str,
        market: str,
        reason: str,
    ) -> AiTradeAdvice:
        return AiTradeAdvice(
            action="HOLD",
            exchange=exchange,
            market=market,
            confidence=Decimal("0.8000"),
            recommended_amount_krw=None,
            recommended_quantity=None,
            reason=reason,
            risk_notes=advice.risk_notes,
            primary_factors=advice.primary_factors,
            alternatives_considered=advice.alternatives_considered,
            raw_response={
                **advice.raw_response,
                "system_override": reason,
                "original_action": advice.action,
                "original_exchange": advice.exchange,
                "original_market": advice.market,
            },
        )

    def _create_system_guard_advice(
        self,
        candidate: dict[str, Any],
    ) -> AiTradeAdvice:
        reason = "모든 활성 후보의 캔들 데이터가 부족하여 AI 호출 없이 보류했습니다."
        raw_response = {
            "source": "system_guard",
            "reason": "all_candidates_not_enough_candles",
        }
        return AiTradeAdvice(
            action="HOLD",
            exchange=str(candidate["exchange"]),
            market=str(candidate["market"]),
            confidence=Decimal("0.8000"),
            recommended_amount_krw=None,
            recommended_quantity=None,
            reason=reason,
            risk_notes="충분한 시계열 데이터가 쌓인 뒤 다시 비교해야 합니다.",
            primary_factors=["모든 후보의 캔들 데이터 부족"],
            alternatives_considered=[],
            raw_response=raw_response,
        )

    def _advice_to_dict(self, advice: AiTradeAdvice) -> dict[str, Any]:
        return {
            "action": advice.action,
            "exchange": advice.exchange,
            "market": advice.market,
            "confidence": str(advice.confidence),
            "recommended_amount_krw": decimal_to_string_or_none(
                advice.recommended_amount_krw
            ),
            "recommended_quantity": decimal_to_string_or_none(
                advice.recommended_quantity
            ),
            "reason": advice.reason,
            "risk_notes": advice.risk_notes,
            "primary_factors": advice.primary_factors,
            "alternatives_considered": advice.alternatives_considered,
        }

    def _build_reason(self, advice: AiTradeAdvice) -> str:
        return f"{advice.reason} / 리스크 메모: {advice.risk_notes}"
