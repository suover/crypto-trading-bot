from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


def _decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return default
    return result if result.is_finite() else default


def _optional_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None
    return result if result.is_finite() else None


class MarketRankingPolicy(Protocol):
    def rank(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HeuristicRankingWeights:
    """Centralized, replaceable weights for candidate reduction, not a profit model."""

    liquidity: Decimal = Decimal("0.35")
    trend_alignment: Decimal = Decimal("0.20")
    momentum: Decimal = Decimal("0.15")
    volume_confirmation: Decimal = Decimal("0.10")
    spread: Decimal = Decimal("0.08")
    volatility: Decimal = Decimal("0.07")
    drawdown: Decimal = Decimal("0.05")
    momentum_reference_percent: Decimal = Decimal("10")
    volatility_reference_percent: Decimal = Decimal("20")
    drawdown_reference_percent: Decimal = Decimal("30")
    spread_reference_rate: Decimal = Decimal("0.01")


class HeuristicMarketRankingPolicy:
    def __init__(self, weights: HeuristicRankingWeights | None = None) -> None:
        self.weights = weights or HeuristicRankingWeights()

    def rank(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        liquidities = sorted(
            {
                _decimal(candidate.get("quote_trade_value_24h"))
                for candidate in candidates
            }
        )
        denominator = Decimal(max(len(liquidities) - 1, 1))
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            liquidity = _decimal(candidate.get("quote_trade_value_24h"))
            liquidity_score = Decimal(liquidities.index(liquidity)) / denominator
            summaries = [
                value
                for value in candidate.get("timeframes", {}).values()
                if isinstance(value, dict) and value.get("data_quality") == "SUFFICIENT"
            ]
            trend_score = self._trend_score(summaries)
            momentum_score = self._average_bounded(
                summaries,
                "recent_change_rate",
                self.weights.momentum_reference_percent,
                centered=True,
            )
            volume_score = self._average_bounded(
                summaries, "volume_ratio", Decimal("200")
            )
            volatility_penalty = self._average_bounded(
                summaries,
                "realized_volatility",
                self.weights.volatility_reference_percent,
                missing_score=Decimal("1"),
            )
            drawdown_penalty = self._average_bounded(
                summaries,
                "max_drawdown",
                self.weights.drawdown_reference_percent,
                missing_score=Decimal("1"),
            )
            spread_rate = _decimal(
                candidate.get("orderbook", {}).get("spread_rate"),
                self.weights.spread_reference_rate,
            )
            spread_score = Decimal("1") - min(
                max(spread_rate / self.weights.spread_reference_rate, Decimal("0")),
                Decimal("1"),
            )
            score = (
                self.weights.liquidity * liquidity_score
                + self.weights.trend_alignment * trend_score
                + self.weights.momentum * momentum_score
                + self.weights.volume_confirmation * volume_score
                + self.weights.spread * spread_score
                + self.weights.volatility * (Decimal("1") - volatility_penalty)
                + self.weights.drawdown * (Decimal("1") - drawdown_penalty)
            )
            ranked.append({**candidate, "score": score})
        return sorted(
            ranked,
            key=lambda candidate: (
                _decimal(candidate["score"]),
                _decimal(candidate.get("quote_trade_value_24h")),
            ),
            reverse=True,
        )

    @staticmethod
    def _trend_score(summaries: list[dict[str, Any]]) -> Decimal:
        if not summaries:
            return Decimal("0")
        mapping = {
            "상승 우위": Decimal("1"),
            "과열 주의": Decimal("0.65"),
            "관망": Decimal("0.5"),
            "과매도 주의": Decimal("0.35"),
            "하락 우위": Decimal("0"),
            "판단 보류": Decimal("0.25"),
        }
        return sum(
            mapping.get(str(summary.get("trend_label")), Decimal("0.25"))
            for summary in summaries
        ) / Decimal(len(summaries))

    @staticmethod
    def _average_bounded(
        summaries: list[dict[str, Any]],
        key: str,
        reference: Decimal,
        *,
        centered: bool = False,
        missing_score: Decimal = Decimal("0"),
    ) -> Decimal:
        values = [
            value
            for summary in summaries
            if (value := _optional_decimal(summary.get(key))) is not None
        ]
        if not values or reference <= 0:
            return missing_score
        average = sum(values) / Decimal(len(values))
        if centered:
            return min(
                max((average / reference + Decimal("1")) / Decimal("2"), Decimal("0")),
                Decimal("1"),
            )
        return min(max(average / reference, Decimal("0")), Decimal("1"))
