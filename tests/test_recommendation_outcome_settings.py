import pytest

from crypto_trading_bot.config.settings import Settings


def settings(**values):
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        **values,
    )


def test_recommendation_outcome_rollout_defaults_and_normalized_horizons():
    value = settings()
    assert value.recommendation_outcome_enabled is False
    assert value.recommendation_outcome_interval_seconds == 300
    assert value.recommendation_outcome_batch_size == 50
    assert value.recommendation_outcome_horizon_list == (60, 240, 1440)
    assert settings(
        recommendation_outcome_horizons_minutes="240, 60,1440,60"
    ).recommendation_outcome_horizon_list == (60, 240, 1440)


@pytest.mark.parametrize("value", ["", "60,", "0,60", "-1", "abc"])
def test_recommendation_outcome_horizons_reject_invalid_values(value):
    with pytest.raises(ValueError, match="positive integers"):
        settings(recommendation_outcome_horizons_minutes=value)
