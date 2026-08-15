import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from crypto_trading_bot.ai.trade_advisor import (
    TRADE_ADVICE_SCHEMA,
    OpenAITradeAdvisor,
)
from crypto_trading_bot.config.settings import get_settings


@pytest.fixture(autouse=True)
def configure_advisor_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", "")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", "")
    monkeypatch.delenv("OPENAI_TRADE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def build_client() -> MagicMock:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(
            {
                "action": "BUY",
                "exchange": "UPBIT",
                "market": "KRW-BTC",
                "trade_ratio": 0.5,
                "confidence": 0.8,
                "reason": "test reason",
                "risk_notes": "test risk",
                "primary_factors": ["trend"],
                "alternatives_considered": [],
            }
        )
    )
    return client


def test_advisor_uses_default_settings_and_preserves_structured_output() -> None:
    client = build_client()
    advisor = OpenAITradeAdvisor(client=client)

    advice = advisor.create_advice({"candidates": []})

    request = client.responses.create.call_args.kwargs
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning"] == {"effort": "medium"}
    assert request["text"]["format"] == {
        "type": "json_schema",
        "name": "trade_advice",
        "schema": TRADE_ADVICE_SCHEMA,
        "strict": True,
    }
    assert advice.action == "BUY"
    assert str(advice.trade_ratio) == "0.5"
    assert str(advice.confidence) == "0.8"
    assert advice.recommended_amount_krw is None
    assert advice.recommended_quantity is None


def test_advisor_uses_environment_model_and_reasoning_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_TRADE_MODEL", "environment-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    get_settings.cache_clear()
    client = build_client()

    OpenAITradeAdvisor(client=client).create_advice({"candidates": []})

    request = client.responses.create.call_args.kwargs
    assert request["model"] == "environment-model"
    assert request["reasoning"] == {"effort": "low"}


def test_advisor_omits_reasoning_when_effort_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "")
    get_settings.cache_clear()
    client = build_client()

    OpenAITradeAdvisor(client=client).create_advice({"candidates": []})

    request = client.responses.create.call_args.kwargs
    assert "reasoning" not in request


def test_explicit_model_takes_priority_over_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_TRADE_MODEL", "environment-model")
    get_settings.cache_clear()
    client = build_client()

    OpenAITradeAdvisor(model="explicit-model", client=client).create_advice(
        {"candidates": []}
    )

    assert client.responses.create.call_args.kwargs["model"] == "explicit-model"
