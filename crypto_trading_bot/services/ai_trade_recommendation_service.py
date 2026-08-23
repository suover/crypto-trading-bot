from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
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
    MarketUniverseCandidate,
    OrderLog,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.services.exchange_market_registry_service import (
    ExchangeMarketRegistryService,
)
from crypto_trading_bot.services.market_data_context_service import (
    MarketDataContextService,
)
from crypto_trading_bot.services.pipeline_identity import get_pipeline_run_id


MIN_RECOMMENDED_ORDER_AMOUNT_KRW = Decimal("5000")
MIN_CANDLES_FOR_ADVICE = 20
KST = ZoneInfo("Asia/Seoul")


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
        pipeline_run_id: str | None = None,
    ) -> tuple[AnalysisRun, list[TradeRecommendation]]:
        settings = get_settings()
        pipeline_id = get_pipeline_run_id(pipeline_run_id)
        user = self._get_user(user_name)
        analysis_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_id,
            run_type="AI_RECOMMENDATION",
            trading_mode=settings.trading_mode,
            status="STARTED",
        )
        self.session.add(analysis_run)
        self.session.flush()

        try:
            persisted_candidates = self._get_persisted_candidates(user.id, pipeline_id)
            if pipeline_id is not None:
                if not persisted_candidates:
                    raise ValueError(
                        "No persisted universe candidates found for pipeline. "
                        f"pipeline_run_id={pipeline_id}"
                    )
                base_candidates = [
                    dict(row.feature_data) for row in persisted_candidates
                ]
                krw_balance = to_decimal(base_candidates[0].get("quote_balance_krw"))
            else:
                krw_balance = self._get_latest_balance(
                    user_id=user.id,
                    exchange="UPBIT",
                    currency="KRW",
                )
                registry_markets = (
                    self.registry_service.load_allowed_active_markets_for_exchange(
                        "UPBIT"
                    )
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
                    "holdings": [
                        candidate.get("position")
                        for candidate in candidates
                        if candidate.get("held")
                    ],
                },
                "pipeline_run_id": pipeline_id,
                "market_universe_mode": settings.market_universe_mode,
                "trading_mode": settings.trading_mode,
                "external_data_status": market_data_result.external_data_status,
                "market_sentiment": market_data_result.market_sentiment,
                "candidates": candidates,
                "rules": {
                    "minimum_order_amount_krw": str(MIN_RECOMMENDED_ORDER_AMOUNT_KRW),
                    "allowed_actions": ["BUY", "SELL", "HOLD"],
                    "spot_only": True,
                    "objective": "maximize_expected_long_term_net_account_value",
                    "buy_limits_are_exposure_limits": True,
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
                user_id=user.id,
                execution_mode=settings.order_execution_mode,
            )
            safety_override = safe_advice.raw_response.get("system_override")
            candidate_row_by_market = {
                (row.exchange, row.market): row for row in persisted_candidates
            }
            selected_universe_candidate = candidate_row_by_market.get(
                (safe_advice.exchange, safe_advice.market)
            )
            recommendation = TradeRecommendation(
                analysis_run_id=analysis_run.id,
                market_snapshot_id=None,
                universe_candidate_id=(
                    selected_universe_candidate.id
                    if selected_universe_candidate is not None
                    else None
                ),
                user_id=user.id,
                exchange=safe_advice.exchange,
                market=safe_advice.market,
                action=safe_advice.action,
                trade_ratio=safe_advice.trade_ratio,
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
        avg_buy_price = self._get_latest_avg_buy_price(
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
            avg_buy_price=avg_buy_price,
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
        avg_buy_price: Decimal,
        max_order_amount: Decimal,
    ) -> dict[str, Any]:
        enough_candles = indicators is not None
        latest_price = to_decimal(candles[-1].trade_price) if candles else None
        current_position_value = (
            coin_balance * latest_price
            if latest_price is not None
            and latest_price.is_finite()
            and latest_price > 0
            else None
        )
        estimated_cost_basis = (
            coin_balance * avg_buy_price if avg_buy_price > 0 else None
        )
        unrealized_pnl = (
            current_position_value - estimated_cost_basis
            if current_position_value is not None and estimated_cost_basis is not None
            else None
        )
        unrealized_pnl_percentage = (
            unrealized_pnl / estimated_cost_basis * Decimal("100")
            if unrealized_pnl is not None and estimated_cost_basis
            else None
        )
        return {
            "exchange": registry_market.exchange_code,
            "market": registry_market.market,
            "base_asset": registry_market.base_asset,
            "quote_asset": registry_market.quote_asset,
            "coingecko_id": registry_market.coingecko_id,
            "buy_eligible": True,
            "sell_eligible": coin_balance > 0,
            "selection_source": "STATIC",
            "held": coin_balance > 0,
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
            "timeframes": {
                f"{candle_unit}m": {
                    "data_quality": "SUFFICIENT" if enough_candles else "INSUFFICIENT",
                    "candle_count": len(candles),
                    "trend_label": (
                        indicators.trend_label if indicators else "판단 보류"
                    ),
                }
            },
            "quote_balance_krw": str(krw_balance),
            "coin_balance": str(coin_balance),
            "avg_buy_price": decimal_to_string_or_none(
                avg_buy_price if avg_buy_price > 0 else None
            ),
            "current_position_value_krw": decimal_to_string_or_none(
                current_position_value
            ),
            "estimated_cost_basis_krw": decimal_to_string_or_none(estimated_cost_basis),
            "unrealized_pnl_krw": decimal_to_string_or_none(unrealized_pnl),
            "unrealized_pnl_percentage": decimal_to_string_or_none(
                unrealized_pnl_percentage
            ),
            "minimum_order_amount_krw": str(MIN_RECOMMENDED_ORDER_AMOUNT_KRW),
            "max_order_amount_krw": str(max_order_amount),
        }

    def _get_user(self, user_name: str) -> User:
        user = self.session.scalar(select(User).where(User.name == user_name))
        if user is None:
            raise ValueError(f"User not found. name={user_name}")
        return user

    def _get_persisted_candidates(
        self, user_id: int, pipeline_run_id: str | None
    ) -> list[MarketUniverseCandidate]:
        if pipeline_run_id is None:
            return []
        universe_run_id = self.session.scalar(
            select(AnalysisRun.id)
            .where(
                AnalysisRun.user_id == user_id,
                AnalysisRun.pipeline_run_id == pipeline_run_id,
                AnalysisRun.run_type == "MARKET_UNIVERSE",
                AnalysisRun.status == "SUCCESS",
            )
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )
        if universe_run_id is None:
            return []
        return list(
            self.session.scalars(
                select(MarketUniverseCandidate)
                .where(
                    MarketUniverseCandidate.analysis_run_id == universe_run_id,
                    MarketUniverseCandidate.user_id == user_id,
                    MarketUniverseCandidate.exchange == "UPBIT",
                )
                .order_by(
                    MarketUniverseCandidate.rank.asc().nulls_last(),
                    MarketUniverseCandidate.id,
                )
            )
        )

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

    def _get_latest_avg_buy_price(
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
        return Decimal("0") if snapshot is None else to_decimal(snapshot.avg_buy_price)

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
            high_prices=[to_decimal(candle.high_price) for candle in candles],
            low_prices=[to_decimal(candle.low_price) for candle in candles],
        )

    def _apply_safety_rules(
        self,
        advice: AiTradeAdvice,
        candidates: list[dict[str, Any]],
        user_id: int | None = None,
        execution_mode: str | None = None,
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

        confidence = advice.confidence
        if not isinstance(confidence, Decimal) or not confidence.is_finite():
            confidence = Decimal("0")
        confidence = min(max(confidence, Decimal("0")), Decimal("1"))
        ratio = advice.trade_ratio
        if ratio is None or not ratio.is_finite() or ratio < 0 or ratio > 1:
            return self._override_to_hold(
                advice,
                exchange=advice.exchange,
                market=advice.market,
                reason="trade_ratio is missing, non-finite, or outside 0..1.",
            )
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
            if not bool(selected_candidate.get("buy_eligible", True)):
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="선택한 후보는 현재 신규 BUY 대상이 아닙니다.",
                )
            if ratio == 0:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="BUY requires trade_ratio greater than zero.",
                )
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
            remaining_daily_capacity = Decimal("Infinity")
            if user_id is not None and execution_mode is not None:
                daily_limit = Decimal(str(get_settings().daily_max_order_amount_krw))
                used_today = self._get_today_buy_amount_krw(
                    user_id=user_id, execution_mode=execution_mode
                )
                remaining_daily_capacity = daily_limit - used_today
            amount = min(
                quote_balance * ratio,
                maximum,
                quote_balance,
                remaining_daily_capacity,
            )
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
                trade_ratio=ratio,
                confidence=confidence,
                recommended_amount_krw=amount,
                recommended_quantity=None,
            )

        if advice.action == "SELL":
            if not bool(selected_candidate.get("sell_eligible", True)):
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="선택한 후보는 현재 SELL 검토 대상이 아닙니다.",
                )
            if ratio == 0:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="SELL requires trade_ratio greater than zero.",
                )
            coin_balance = to_decimal(selected_candidate["coin_balance"])
            if coin_balance <= 0:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="선택한 자산의 보유 수량이 없어 매도를 보류했습니다.",
                )
            try:
                latest_price = to_decimal(selected_candidate.get("latest_price"))
            except ArithmeticError, TypeError, ValueError:
                latest_price = Decimal("NaN")
            if not latest_price.is_finite() or latest_price <= 0:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="Latest price is invalid, so SELL sizing cannot be validated.",
                )
            quantity = min(coin_balance * ratio, coin_balance)
            estimated_amount = quantity * latest_price
            minimum = to_decimal(selected_candidate["minimum_order_amount_krw"])
            if estimated_amount < minimum:
                return self._override_to_hold(
                    advice,
                    exchange=advice.exchange,
                    market=advice.market,
                    reason="Estimated SELL value is below the minimum order amount.",
                )
            return self._replace_advice(
                advice,
                action="SELL",
                trade_ratio=ratio,
                confidence=confidence,
                recommended_amount_krw=estimated_amount,
                recommended_quantity=quantity,
            )

        if ratio != 0:
            return self._override_to_hold(
                advice,
                exchange=advice.exchange,
                market=advice.market,
                reason="HOLD requires trade_ratio equal to zero.",
            )
        return self._replace_advice(
            advice,
            action="HOLD",
            trade_ratio=Decimal("0"),
            confidence=confidence,
            recommended_amount_krw=None,
            recommended_quantity=None,
        )

    def _get_today_buy_amount_krw(self, user_id: int, execution_mode: str) -> Decimal:
        today_kst = datetime.now(KST).date()
        start_kst = datetime.combine(today_kst, time.min, tzinfo=KST)
        start_utc = start_kst.astimezone(UTC)
        end_utc = (start_kst + timedelta(days=1)).astimezone(UTC)
        mode = execution_mode.strip().upper()
        statuses = (
            ("MOCK_FILLED",)
            if mode == "MOCK"
            else (
                "LIVE_PLACED",
                "LIVE_WAIT",
                "LIVE_DONE",
                "LIVE_CANCELLED",
                "LIVE_EXECUTED_CANCELLED",
                "LIVE_UNKNOWN",
            )
        )
        statement = select(func.coalesce(func.sum(OrderLog.amount_krw), 0)).where(
            OrderLog.user_id == user_id,
            OrderLog.trading_mode == mode,
            OrderLog.side == "BUY",
            OrderLog.status.in_(statuses),
            OrderLog.amount_krw.is_not(None),
            OrderLog.created_at >= start_utc,
            OrderLog.created_at < end_utc,
        )
        return Decimal(str(self.session.scalar(statement) or 0))

    def _replace_advice(
        self,
        advice: AiTradeAdvice,
        *,
        action: str,
        trade_ratio: Decimal,
        confidence: Decimal,
        recommended_amount_krw: Decimal | None,
        recommended_quantity: Decimal | None,
    ) -> AiTradeAdvice:
        return AiTradeAdvice(
            action=action,
            exchange=advice.exchange,
            market=advice.market,
            trade_ratio=trade_ratio,
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
            trade_ratio=Decimal("0"),
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
            trade_ratio=Decimal("0"),
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
            "trade_ratio": decimal_to_string_or_none(advice.trade_ratio),
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
