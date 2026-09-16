from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

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
)
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver


MIN_RECOMMENDED_ORDER_AMOUNT_KRW = Decimal("5000")


@dataclass(frozen=True)
class TradeDecision:
    action: str
    confidence: Decimal
    reason: str
    recommended_amount_krw: Decimal | None
    recommended_quantity: Decimal | None


def to_decimal(value: object | None) -> Decimal:
    if value is None:
        return Decimal("0")

    return Decimal(str(value))


def get_base_currency(market: str) -> str:
    parts = market.split("-")

    if len(parts) != 2:
        raise ValueError(f"Unexpected market format. market={market}")

    return parts[1]


class TradeRecommendationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_recommendations(
        self,
        user_id: int,
        candle_unit: int = 15,
        candle_count: int = 50,
    ) -> tuple[AnalysisRun, list[TradeRecommendation]]:
        settings = get_settings()

        user = RuntimeUserResolver(self.session).resolve(user_id)

        analysis_run = AnalysisRun(
            user_id=user.id,
            run_type="RECOMMENDATION",
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

                decision = self._create_decision(
                    market=market,
                    candle_unit=candle_unit,
                    candles=candles,
                    krw_balance=krw_balance,
                    user_id=user.id,
                )

                recommendation = TradeRecommendation(
                    analysis_run_id=analysis_run.id,
                    market_snapshot_id=None,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=market,
                    action=decision.action,
                    confidence=decision.confidence,
                    reason=decision.reason,
                    recommended_amount_krw=decision.recommended_amount_krw,
                    recommended_quantity=decision.recommended_quantity,
                    ai_model=None,
                    ai_response=None,
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

    def _create_decision(
        self,
        market: str,
        candle_unit: int,
        candles: list[MarketCandle],
        krw_balance: Decimal,
        user_id: int,
    ) -> TradeDecision:
        if len(candles) < 20:
            return TradeDecision(
                action="HOLD",
                confidence=Decimal("0.5000"),
                reason="기술지표 계산에 필요한 캔들 데이터가 부족하여 보류",
                recommended_amount_krw=None,
                recommended_quantity=None,
            )

        close_prices = [to_decimal(candle.trade_price) for candle in candles]
        volumes = [to_decimal(candle.candle_acc_trade_volume) for candle in candles]

        indicators = calculate_market_indicators(
            market=market,
            candle_unit=candle_unit,
            close_prices=close_prices,
            volumes=volumes,
        )

        base_currency = get_base_currency(market)
        coin_balance = self._get_latest_balance(
            user_id=user_id,
            exchange="UPBIT",
            currency=base_currency,
        )

        return self._decide_by_rules(
            indicators=indicators,
            krw_balance=krw_balance,
            coin_balance=coin_balance,
        )

    def _decide_by_rules(
        self,
        indicators: MarketIndicatorResult,
        krw_balance: Decimal,
        coin_balance: Decimal,
    ) -> TradeDecision:
        if (
            indicators.sma_5 is None
            or indicators.sma_20 is None
            or indicators.rsi_14 is None
            or indicators.recent_10_candle_change_rate is None
            or indicators.volume_ratio_5_to_20 is None
        ):
            return TradeDecision(
                action="HOLD",
                confidence=Decimal("0.5000"),
                reason="일부 기술지표가 계산되지 않아 판단 보류",
                recommended_amount_krw=None,
                recommended_quantity=None,
            )

        if coin_balance > 0:
            if indicators.rsi_14 >= Decimal("70"):
                return TradeDecision(
                    action="SELL",
                    confidence=Decimal("0.6500"),
                    reason="RSI가 70 이상으로 과열 구간에 진입하여 매도 검토",
                    recommended_amount_krw=None,
                    recommended_quantity=coin_balance,
                )

            if (
                indicators.trend_label == "하락 우위"
                and indicators.recent_10_candle_change_rate < 0
            ):
                return TradeDecision(
                    action="SELL",
                    confidence=Decimal("0.6000"),
                    reason="단기 추세가 하락 우위이고 최근 등락률도 음수라 매도 검토",
                    recommended_amount_krw=None,
                    recommended_quantity=coin_balance,
                )

        if krw_balance < MIN_RECOMMENDED_ORDER_AMOUNT_KRW:
            return TradeDecision(
                action="HOLD",
                confidence=Decimal("0.7500"),
                reason="KRW 잔고가 내부 최소 추천 금액보다 적어 신규 매수 보류",
                recommended_amount_krw=None,
                recommended_quantity=None,
            )

        if (
            indicators.trend_label == "상승 우위"
            and indicators.rsi_14 < Decimal("65")
            and indicators.recent_10_candle_change_rate > 0
            and indicators.volume_ratio_5_to_20 >= Decimal("100")
        ):
            settings = get_settings()
            recommended_amount = min(
                Decimal(settings.max_order_amount_krw),
                krw_balance,
            )

            return TradeDecision(
                action="BUY",
                confidence=Decimal("0.6200"),
                reason="상승 우위 추세, 과열 전 RSI, 최근 상승률, 거래량 조건을 충족하여 매수 검토",
                recommended_amount_krw=recommended_amount,
                recommended_quantity=None,
            )

        if indicators.rsi_14 <= Decimal("30"):
            return TradeDecision(
                action="HOLD",
                confidence=Decimal("0.5800"),
                reason="RSI가 과매도 구간이나 반등 확인 전이라 추격 매수 보류",
                recommended_amount_krw=None,
                recommended_quantity=None,
            )

        return TradeDecision(
            action="HOLD",
            confidence=Decimal("0.5500"),
            reason="강한 매수 또는 매도 조건이 없어 관망",
            recommended_amount_krw=None,
            recommended_quantity=None,
        )
