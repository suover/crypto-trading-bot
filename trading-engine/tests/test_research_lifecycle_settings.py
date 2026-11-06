import pytest
from pydantic import ValidationError

from test_research_lifecycle_service import settings


def test_research_lifecycle_safe_rollout_defaults():
    value = settings()
    assert not value.research_lifecycle_enabled
    assert not value.research_auto_shadow_enrollment_enabled
    assert value.research_lifecycle_interval_seconds == 21600
    assert value.research_lifecycle_batch_size == 10


@pytest.mark.parametrize(
    "name,value",
    [
        ("research_lifecycle_interval_seconds", 0),
        ("research_lifecycle_interval_seconds", 59),
        ("research_lifecycle_interval_seconds", 604801),
        ("research_lifecycle_interval_seconds", "invalid"),
        ("research_lifecycle_batch_size", 0),
        ("research_lifecycle_batch_size", 501),
        ("research_lifecycle_batch_size", 1.5),
        ("research_lifecycle_enabled", "invalid"),
        ("research_auto_shadow_enrollment_enabled", "invalid"),
    ],
)
def test_research_lifecycle_invalid_settings_are_rejected(name, value):
    with pytest.raises(ValidationError):
        settings(**{name: value})
