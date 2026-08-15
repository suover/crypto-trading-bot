import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from openai import OpenAI

from crypto_trading_bot.config.settings import get_settings


ALTERNATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exchange": {"type": "string"},
        "market": {"type": "string"},
        "decision": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["exchange", "market", "decision", "reason"],
    "additionalProperties": False,
}

TRADE_ADVICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "exchange": {"type": "string"},
        "market": {"type": "string"},
        "trade_ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "risk_notes": {"type": "string"},
        "primary_factors": {"type": "array", "items": {"type": "string"}},
        "alternatives_considered": {
            "type": "array",
            "items": ALTERNATIVE_SCHEMA,
        },
    },
    "required": [
        "action",
        "exchange",
        "market",
        "trade_ratio",
        "confidence",
        "reason",
        "risk_notes",
        "primary_factors",
        "alternatives_considered",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AiTradeAdvice:
    action: str
    exchange: str
    market: str
    trade_ratio: Decimal | None
    confidence: Decimal
    recommended_amount_krw: Decimal | None
    recommended_quantity: Decimal | None
    reason: str
    risk_notes: str
    primary_factors: list[str]
    alternatives_considered: list[dict[str, str]]
    raw_response: dict[str, Any]


def to_decimal_or_none(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        value_decimal = Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None
    return value_decimal if value_decimal.is_finite() else None


class OpenAITradeAdvisor:
    def __init__(
        self,
        model: str | None = None,
        client: OpenAI | None = None,
    ) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        self.model = model if model is not None else settings.openai_trade_model
        self.reasoning_effort = settings.openai_reasoning_effort
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    def create_advice(self, context: dict[str, Any]) -> AiTradeAdvice:
        reasoning_options = (
            {"reasoning": {"effort": self.reasoning_effort}}
            if self.reasoning_effort
            else {}
        )
        response = self.client.responses.create(
            model=self.model,
            **reasoning_options,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a cost-aware spot crypto trading assistant. Your primary "
                        "objective is to maximize expected long-term net account value "
                        "after trading fees, spread, slippage, and execution risk. Compare "
                        "every supplied candidate and the current account and position state. "
                        "Choose exactly one final "
                        "BUY, SELL, or HOLD action and one candidate exchange/market. "
                        "Do not trade merely to be active and do not HOLD merely to appear "
                        "conservative. HOLD when expected advantage is insufficient after "
                        "costs, uncertainty, and downside risk. Full BUY or full SELL is "
                        "allowed when strongly justified. For HOLD, select the "
                        "most relevant candidate that is being held or the strongest "
                        "candidate that still does not justify a trade. Never recommend "
                        "borrowing, leverage, futures, or markets outside the supplied list. "
                        "Never claim or guarantee profitability. Write "
                        "reason, risk_notes, primary_factors, and alternative reasons "
                        "in Korean."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": (
                                "Compare every candidate and return one final trading "
                                "recommendation."
                            ),
                            "rules": [
                                "Choose only an exchange and market present in context.candidates.",
                                "Do not BUY or SELL a candidate with enough_candles=false.",
                                "Return trade_ratio only; never return an exact KRW amount or coin quantity.",
                                "trade_ratio must be finite and between 0 and 1 inclusive.",
                                "HOLD requires trade_ratio=0; BUY and SELL require trade_ratio>0.",
                                "Confidence and trade_ratio are separate concepts; never derive one from the other.",
                                "If quote_balance_krw is below minimum_order_amount_krw, do not BUY.",
                                "Treat orderbook as a short-lived supporting signal, never a guaranteed direction.",
                                "Compare global_market USD context with local UPBIT technical data.",
                                "market_sentiment is broad, not coin-specific; never trade from Fear & Greed alone.",
                                "Extreme fear can mean opportunity and elevated risk; greed can mean momentum and correction risk.",
                                "Ignore UNAVAILABLE or DISABLED external sources and continue with candle and account data.",
                                "Never bypass candidate membership, candle, balance, minimum, or maximum order restrictions.",
                                "Remain spot-only. Never use leverage, borrowing, or futures.",
                            ],
                            "context": context,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "trade_advice",
                    "schema": TRADE_ADVICE_SCHEMA,
                    "strict": True,
                }
            },
        )

        parsed_response = json.loads(response.output_text)
        return AiTradeAdvice(
            action=str(parsed_response["action"]),
            exchange=str(parsed_response["exchange"]),
            market=str(parsed_response["market"]),
            trade_ratio=to_decimal_or_none(parsed_response.get("trade_ratio")),
            confidence=to_decimal_or_none(parsed_response.get("confidence"))
            or Decimal("0"),
            recommended_amount_krw=None,
            recommended_quantity=None,
            reason=str(parsed_response["reason"]),
            risk_notes=str(parsed_response["risk_notes"]),
            primary_factors=[
                str(factor) for factor in parsed_response["primary_factors"]
            ],
            alternatives_considered=[
                {
                    "exchange": str(alternative["exchange"]),
                    "market": str(alternative["market"]),
                    "decision": str(alternative["decision"]),
                    "reason": str(alternative["reason"]),
                }
                for alternative in parsed_response["alternatives_considered"]
            ],
            raw_response=parsed_response,
        )
