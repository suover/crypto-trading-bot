from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.db.models import (
    FullLivePolicyActivation,
    FullLivePolicyTerminationEvent,
)
from crypto_trading_bot.services.offline_strategy_replay_service import restore_weights
from crypto_trading_bot.services.strategy_replay_dataset_service import policy_signature


ACTIVATION_SCHEMA_VERSION = "full-live-policy-activation-v1"
TERMINATION_SCHEMA_VERSION = "full-live-policy-termination-v1"
POLICY_RUN_SCHEMA_VERSION = "market-universe-policy-run-v1"
MANUAL_CLI = "MANUAL_CLI"


class FullLivePolicyIntegrityError(ValueError):
    pass


def aware_utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FullLivePolicyIntegrityError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_context(exchange: object, quote_asset: object) -> tuple[str, str]:
    if not isinstance(exchange, str) or not exchange.strip():
        raise FullLivePolicyIntegrityError("exchange is required")
    if not isinstance(quote_asset, str) or not quote_asset.strip():
        raise FullLivePolicyIntegrityError("quote asset is required")
    return exchange.strip().upper(), quote_asset.strip().upper()


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise FullLivePolicyIntegrityError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def canonicalize(value):
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, datetime):
        return aware_utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    return value


def _signed(prefix: str, payload: dict) -> str:
    encoded = json.dumps(
        canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"{prefix}:{sha256(encoded).hexdigest()}"


def activation_payload(value) -> dict:
    return canonicalize(
        {
            "activation_schema_version": value.activation_schema_version,
            "promotion_approval_id": value.promotion_approval_id,
            "promotion_approval_signature": value.promotion_approval_signature,
            "candidate_id": value.candidate_id,
            "shadow_enrollment_id": value.shadow_enrollment_id,
            "user_id": value.user_id,
            "exchange": value.exchange,
            "quote_asset": value.quote_asset,
            "scenario_name": value.scenario_name,
            "scenario_definition_signature": value.scenario_definition_signature,
            "component_weights": value.component_weights,
            "baseline_policy_signature": value.baseline_policy_signature,
            "effective_policy_definition": value.effective_policy_definition,
            "effective_policy_signature": value.effective_policy_signature,
            "effective_top_n": value.effective_top_n,
            "activation_source": value.activation_source,
            "activated_at": value.activated_at,
        }
    )


def activation_signature(value) -> str:
    return _signed(ACTIVATION_SCHEMA_VERSION, activation_payload(value))


def termination_payload(value) -> dict:
    return canonicalize(
        {
            "termination_schema_version": value.termination_schema_version,
            "activation_id": value.activation_id,
            "activation_signature": value.activation_signature,
            "user_id": value.user_id,
            "exchange": value.exchange,
            "quote_asset": value.quote_asset,
            "termination_source": value.termination_source,
            "termination_reason": value.termination_reason,
            "terminated_at": value.terminated_at,
        }
    )


def termination_signature(value) -> str:
    return _signed(TERMINATION_SCHEMA_VERSION, termination_payload(value))


def restore_effective_ranking_policy(
    policy_definition: object,
    expected_signature: str,
    expected_top_n: int,
) -> HeuristicMarketRankingPolicy:
    if not isinstance(policy_definition, dict):
        raise FullLivePolicyIntegrityError("effective policy definition is invalid")
    if policy_signature(policy_definition) != expected_signature:
        raise FullLivePolicyIntegrityError("effective policy signature does not verify")
    try:
        weights, top_n = restore_weights(policy_definition)
    except Exception as error:
        raise FullLivePolicyIntegrityError(
            f"effective ranking policy is invalid: {error}"
        ) from error
    if top_n != expected_top_n:
        raise FullLivePolicyIntegrityError("effective TopN does not verify")
    return HeuristicMarketRankingPolicy(weights)


def validate_activation(
    activation: FullLivePolicyActivation,
) -> HeuristicMarketRankingPolicy:
    exchange, quote_asset = normalize_context(
        activation.exchange, activation.quote_asset
    )
    if (
        activation.activation_schema_version != ACTIVATION_SCHEMA_VERSION
        or activation.activation_source != MANUAL_CLI
        or isinstance(activation.id, bool)
        or not isinstance(activation.id, int)
        or activation.id < 1
        or isinstance(activation.promotion_approval_id, bool)
        or not isinstance(activation.promotion_approval_id, int)
        or activation.promotion_approval_id < 1
        or isinstance(activation.user_id, bool)
        or not isinstance(activation.user_id, int)
        or activation.user_id < 1
        or isinstance(activation.effective_top_n, bool)
        or not isinstance(activation.effective_top_n, int)
        or activation.effective_top_n < 1
        or activation.exchange != exchange
        or activation.quote_asset != quote_asset
    ):
        raise FullLivePolicyIntegrityError("activation metadata is invalid")
    required_strings = (
        activation.promotion_approval_signature,
        activation.scenario_name,
        activation.scenario_definition_signature,
        activation.baseline_policy_signature,
        activation.effective_policy_signature,
    )
    if any(not isinstance(value, str) or not value for value in required_strings):
        raise FullLivePolicyIntegrityError("activation identity is invalid")
    aware_utc(activation.activated_at, "activated_at")
    if activation.activation_signature != activation_signature(activation):
        raise FullLivePolicyIntegrityError("activation signature does not verify")
    return restore_effective_ranking_policy(
        activation.effective_policy_definition,
        activation.effective_policy_signature,
        activation.effective_top_n,
    )


def validate_termination(
    event: FullLivePolicyTerminationEvent,
    activation: FullLivePolicyActivation,
) -> None:
    if (
        event.termination_schema_version != TERMINATION_SCHEMA_VERSION
        or event.termination_source != MANUAL_CLI
        or event.activation_id != activation.id
        or event.activation_signature != activation.activation_signature
        or event.user_id != activation.user_id
        or event.exchange != activation.exchange
        or event.quote_asset != activation.quote_asset
        or not isinstance(event.termination_reason, str)
        or not event.termination_reason.strip()
        or event.termination_reason != event.termination_reason.strip()
    ):
        raise FullLivePolicyIntegrityError("termination provenance is invalid")
    terminated_at = aware_utc(event.terminated_at, "terminated_at")
    if terminated_at < aware_utc(activation.activated_at, "activated_at"):
        raise FullLivePolicyIntegrityError("termination predates activation")
    if event.termination_signature != termination_signature(event):
        raise FullLivePolicyIntegrityError("termination signature does not verify")


def effective_weights_definition(policy: HeuristicMarketRankingPolicy) -> dict:
    return canonicalize(asdict(policy.weights))


__all__ = [
    "ACTIVATION_SCHEMA_VERSION",
    "FullLivePolicyIntegrityError",
    "MANUAL_CLI",
    "POLICY_RUN_SCHEMA_VERSION",
    "TERMINATION_SCHEMA_VERSION",
    "activation_payload",
    "activation_signature",
    "aware_utc",
    "canonicalize",
    "effective_weights_definition",
    "normalize_context",
    "restore_effective_ranking_policy",
    "termination_payload",
    "termination_signature",
    "validate_activation",
    "validate_termination",
]
