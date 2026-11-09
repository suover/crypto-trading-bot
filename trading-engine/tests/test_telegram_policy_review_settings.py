import pytest
from pydantic import ValidationError

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.services.telegram_policy_review_tokens import (
    confirmation_token,
    parse_policy_callback,
    token_hash,
)


def settings(**kwargs):
    return Settings(
        _env_file=None, database_url="postgresql://test:test@localhost/test", **kwargs
    )


def test_default_settings_and_token_format():
    config = settings()
    assert not config.policy_telegram_review_enabled
    assert not config.policy_telegram_approval_enabled
    assert config.policy_telegram_admin_ids == frozenset()
    assert config.policy_telegram_review_token_ttl_seconds == 604800
    assert config.policy_telegram_confirmation_ttl_seconds == 900
    token, digest = confirmation_token()
    assert len(digest) == 64
    assert digest != token_hash("review", token)
    for command in ("policy_review", "policy_confirm", "policy_reject"):
        data = f"{command}:{token}"
        assert len(data.encode()) <= 64 and parse_policy_callback(data) == (
            command,
            token,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"policy_telegram_approval_enabled": True},
        {"policy_telegram_review_enabled": True},
        {
            "policy_telegram_review_enabled": True,
            "telegram_chat_id": "-100123",
            "telegram_bot_token": "123:" + "x" * 30,
        },
        {"policy_telegram_allowed_user_ids": "alice"},
        {"policy_telegram_allowed_user_ids": "123,,456"},
        {"policy_telegram_allowed_user_ids": "0"},
        {"policy_telegram_allowed_user_ids": "-123"},
        {"policy_telegram_allowed_user_ids": str(2**52)},
        {"policy_telegram_review_token_ttl_seconds": 0},
        {"policy_telegram_confirmation_ttl_seconds": 3601},
    ],
)
def test_invalid_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)
