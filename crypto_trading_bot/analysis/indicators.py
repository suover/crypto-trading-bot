from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class MarketIndicatorResult:
    market: str
    candle_unit: int
    candle_count: int
    latest_price: Decimal
    sma_5: Decimal | None
    sma_20: Decimal | None
    ema_5: Decimal | None
    ema_20: Decimal | None
    rsi_14: Decimal | None
    recent_10_candle_change_rate: Decimal | None
    volume_ratio_5_to_20: Decimal | None
    trend_label: str


def calculate_sma(values: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0:
        raise ValueError("period must be greater than 0")

    if len(values) < period:
        return None

    target_values = values[-period:]

    return sum(target_values) / Decimal(period)


def calculate_ema(values: Sequence[Decimal], period: int) -> Decimal | None:
    if period <= 0:
        raise ValueError("period must be greater than 0")

    if len(values) < period:
        return None

    multiplier = Decimal(2) / Decimal(period + 1)
    ema = sum(values[:period]) / Decimal(period)

    for value in values[period:]:
        ema = (value - ema) * multiplier + ema

    return ema


def calculate_rsi(values: Sequence[Decimal], period: int = 14) -> Decimal | None:
    if period <= 0:
        raise ValueError("period must be greater than 0")

    if len(values) < period + 1:
        return None

    changes = [
        values[index] - values[index - 1]
        for index in range(1, len(values))
    ]

    initial_changes = changes[:period]

    avg_gain = sum(
        change if change > 0 else Decimal(0)
        for change in initial_changes
    ) / Decimal(period)

    avg_loss = sum(
        abs(change) if change < 0 else Decimal(0)
        for change in initial_changes
    ) / Decimal(period)

    for change in changes[period:]:
        gain = change if change > 0 else Decimal(0)
        loss = abs(change) if change < 0 else Decimal(0)

        avg_gain = ((avg_gain * Decimal(period - 1)) + gain) / Decimal(period)
        avg_loss = ((avg_loss * Decimal(period - 1)) + loss) / Decimal(period)

    if avg_gain == 0 and avg_loss == 0:
        return Decimal("50")

    if avg_loss == 0:
        return Decimal("100")

    relative_strength = avg_gain / avg_loss

    return Decimal("100") - (Decimal("100") / (Decimal("1") + relative_strength))


def calculate_change_rate(
    values: Sequence[Decimal],
    period: int,
) -> Decimal | None:
    if period <= 1:
        raise ValueError("period must be greater than 1")

    if len(values) < 2:
        return None

    target_values = values[-period:] if len(values) >= period else values
    first_value = target_values[0]
    last_value = target_values[-1]

    if first_value == 0:
        return None

    return ((last_value - first_value) / first_value) * Decimal("100")


def calculate_volume_ratio(
    volumes: Sequence[Decimal],
    short_period: int = 5,
    long_period: int = 20,
) -> Decimal | None:
    short_volume_average = calculate_sma(volumes, short_period)
    long_volume_average = calculate_sma(volumes, long_period)

    if short_volume_average is None or long_volume_average is None:
        return None

    if long_volume_average == 0:
        return None

    return (short_volume_average / long_volume_average) * Decimal("100")


def classify_trend(
    latest_price: Decimal,
    sma_5: Decimal | None,
    sma_20: Decimal | None,
    rsi_14: Decimal | None,
) -> str:
    if rsi_14 is not None and rsi_14 >= Decimal("70"):
        return "과열 주의"

    if rsi_14 is not None and rsi_14 <= Decimal("30"):
        return "과매도 주의"

    if sma_5 is None or sma_20 is None:
        return "판단 보류"

    if latest_price > sma_5 > sma_20:
        return "상승 우위"

    if latest_price < sma_5 < sma_20:
        return "하락 우위"

    return "관망"


def calculate_market_indicators(
    market: str,
    candle_unit: int,
    close_prices: Sequence[Decimal],
    volumes: Sequence[Decimal],
) -> MarketIndicatorResult:
    if not close_prices:
        raise ValueError("close_prices must not be empty")

    if len(close_prices) != len(volumes):
        raise ValueError("close_prices and volumes must have the same length")

    latest_price = close_prices[-1]
    sma_5 = calculate_sma(close_prices, 5)
    sma_20 = calculate_sma(close_prices, 20)
    ema_5 = calculate_ema(close_prices, 5)
    ema_20 = calculate_ema(close_prices, 20)
    rsi_14 = calculate_rsi(close_prices, 14)
    recent_10_candle_change_rate = calculate_change_rate(close_prices, 10)
    volume_ratio_5_to_20 = calculate_volume_ratio(volumes, 5, 20)

    return MarketIndicatorResult(
        market=market,
        candle_unit=candle_unit,
        candle_count=len(close_prices),
        latest_price=latest_price,
        sma_5=sma_5,
        sma_20=sma_20,
        ema_5=ema_5,
        ema_20=ema_20,
        rsi_14=rsi_14,
        recent_10_candle_change_rate=recent_10_candle_change_rate,
        volume_ratio_5_to_20=volume_ratio_5_to_20,
        trend_label=classify_trend(
            latest_price=latest_price,
            sma_5=sma_5,
            sma_20=sma_20,
            rsi_14=rsi_14,
        ),
    )