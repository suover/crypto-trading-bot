import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from openai import OpenAI

from crypto_trading_bot.config.settings import get_settings


TRADE_ADVICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "recommended_amount_krw": {
            "type": ["number", "null"],
        },
        "recommended_quantity": {
            "type": ["number", "null"],
        },
        "reason": {
            "type": "string",
        },
        "risk_notes": {
            "type": "string",
        },
    },
    "required": [
        "action",
        "confidence",
        "recommended_amount_krw",
        "recommended_quantity",
        "reason",
        "risk_notes",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AiTradeAdvice:
    action: str
    confidence: Decimal
    recommended_amount_krw: Decimal | None
    recommended_quantity: Decimal | None
    reason: str
    risk_notes: str
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
                        "You are a conservative crypto trading assistant. "
                        "You do not guarantee profits. "
                        "You only choose BUY, SELL, or HOLD. "
                        "Prefer HOLD when data is weak, balance is insufficient, "
                        "or the risk is unclear. "
                        "Never recommend borrowing, leverage, or aggressive trading. "
                        "Answer in Korean."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Analyze the market/account context and return one trading recommendation.",
                            "rules": [
                                "Use BUY only when trend and risk conditions are favorable.",
                                "Use SELL only when the account has coin balance and sell risk is justified.",
                                "Use HOLD when there is not enough evidence.",
                                "Do not exceed max_order_amount_krw.",
                                "If krw_balance is below minimum_order_amount_krw, do not BUY.",
                                "This is not financial advice. Be conservative.",
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
            confidence=Decimal(str(parsed_response["confidence"])),
            recommended_amount_krw=to_decimal_or_none(
                parsed_response["recommended_amount_krw"]
            ),
            recommended_quantity=to_decimal_or_none(
                parsed_response["recommended_quantity"]
            ),
            reason=str(parsed_response["reason"]),
            risk_notes=str(parsed_response["risk_notes"]),
            raw_response=parsed_response,
        )
