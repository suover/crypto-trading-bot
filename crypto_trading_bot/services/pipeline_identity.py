import os
from uuid import UUID


PIPELINE_RUN_ID_ENV = "CRYPTO_TRADING_PIPELINE_RUN_ID"


def get_pipeline_run_id(value: str | None = None) -> str | None:
    raw_value = value if value is not None else os.environ.get(PIPELINE_RUN_ID_ENV)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return str(UUID(raw_value.strip()))
    except ValueError:
        raise ValueError("pipeline_run_id must be a valid UUID") from None
