import json
from dataclasses import dataclass
from decimal import Decimal
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
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "recommended_amount_krw": {"type": ["number", "null"]},
        "recommended_quantity": {"type": ["number", "null"]},
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
        "confidence",
        "recommended_amount_krw",
        "recommended_quantity",
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
    return Decimal(str(value))


class OpenAITradeAdvisor:
    def __init__(
        self,
        model: str = "gpt-5.5",
        client: OpenAI | None = None,
    ) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        self.model = model
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    def create_advice(self, context: dict[str, Any]) -> AiTradeAdvice:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a conservative crypto trading assistant comparing "
                        "multiple spot-market candidates. Choose exactly one final "
                        "BUY, SELL, or HOLD action and one candidate exchange/market. "
                        "BUY only with sufficient positive evidence and acceptable risk. "
                        "SELL only when the selected base asset has a positive balance "
                        "and selling is justified. HOLD when evidence is weak or mixed, "
                        "data is insufficient, or risk is unclear. For HOLD, select the "
                        "most relevant candidate that is being held or the strongest "
                        "candidate that still does not justify a trade. Never recommend "
                        "borrowing, leverage, futures, or aggressive trading. Write "
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
                                "Do not exceed the selected candidate max_order_amount_krw.",
                                "If quote_balance_krw is below minimum_order_amount_krw, do not BUY.",
                                "Treat orderbook as a short-lived supporting signal, never a guaranteed direction.",
                                "Compare global_market USD context with local UPBIT technical data.",
                                "market_sentiment is broad, not coin-specific; never trade from Fear & Greed alone.",
                                "Extreme fear can mean opportunity and elevated risk; greed can mean momentum and correction risk.",
                                "Ignore UNAVAILABLE or DISABLED external sources and continue with candle and account data.",
                                "Prefer HOLD when technical and external signals conflict.",
                                "Never bypass candidate membership, candle, balance, minimum, or maximum order restrictions.",
                                "For HOLD, recommended_amount_krw and recommended_quantity must be null.",
                                "Be conservative.",
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
            confidence=Decimal(str(parsed_response["confidence"])),
            recommended_amount_krw=to_decimal_or_none(
                parsed_response["recommended_amount_krw"]
            ),
            recommended_quantity=to_decimal_or_none(
                parsed_response["recommended_quantity"]
            ),
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
