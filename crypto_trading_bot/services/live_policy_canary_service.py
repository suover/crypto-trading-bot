from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    HeuristicRankingWeights,
)
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    LivePolicyCanaryActivation,
    LivePolicyCanaryRun,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    SCHEMA_VERSION as SCENARIO_SCHEMA_VERSION,
    parse_scenario_document,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ShadowPolicyPromotionApprovalError,
    load_and_validate_shadow_policy_promotion_approval,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)


REPORT_TYPE = "LIMITED_LIVE_CANARY_V1A_ACTIVATION"
STATUS_REPORT_TYPE = "LIMITED_LIVE_CANARY_V1A_STATUS"
ACTIVATION_SOURCE = "MANUAL_CLI"
RUN_SCHEMA_VERSION = "limited-live-canary-run-v1a"

NO_PROMOTION_APPROVAL = "NO_PROMOTION_APPROVAL"
APPROVAL_SIGNATURE_CHANGED = "APPROVAL_SIGNATURE_CHANGED"
ACTIVE_CANARY_EXISTS = "ACTIVE_CANARY_EXISTS"
CANARY_CONTEXT_BUSY = "CANARY_CONTEXT_BUSY"
DRY_RUN = "DRY_RUN"
CREATED = "CREATED"
ALREADY_ACTIVATED = "ALREADY_ACTIVATED"
INVALID_CANARY_ACTIVATION = "INVALID_CANARY_ACTIVATION"

BASELINE_NO_CANARY = "BASELINE_NO_CANARY"
BASELINE_EXPIRED = "BASELINE_EXPIRED"
BASELINE_EXHAUSTED = "BASELINE_EXHAUSTED"
BASELINE_INVALID_CANARY = "BASELINE_INVALID_CANARY"
CANARY = "CANARY"


@dataclass(frozen=True)
class LimitedLiveCanaryPolicy:
    schema_version: str
    max_duration_hours: int
    max_analysis_runs: int


LIMITED_LIVE_CANARY_V1A = LimitedLiveCanaryPolicy(
    schema_version="limited-live-canary-v1a",
    max_duration_hours=48,
    max_analysis_runs=6,
)


class LivePolicyCanaryError(ValueError):
    pass


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise LivePolicyCanaryError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise LivePolicyCanaryError("signature Decimal must be finite")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonicalize(value):
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc(value, "signature timestamp").isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def validate_canary_policy(
    policy: LimitedLiveCanaryPolicy = LIMITED_LIVE_CANARY_V1A,
) -> None:
    if (
        policy.schema_version != "limited-live-canary-v1a"
        or isinstance(policy.max_duration_hours, bool)
        or not isinstance(policy.max_duration_hours, int)
        or policy.max_duration_hours <= 0
        or isinstance(policy.max_analysis_runs, bool)
        or not isinstance(policy.max_analysis_runs, int)
        or policy.max_analysis_runs <= 0
    ):
        raise LivePolicyCanaryError("Canary policy is invalid")


def canary_policy_definition(
    policy: LimitedLiveCanaryPolicy = LIMITED_LIVE_CANARY_V1A,
) -> dict:
    validate_canary_policy(policy)
    return _canonicalize(asdict(policy))


def canary_policy_definition_signature(
    policy: LimitedLiveCanaryPolicy = LIMITED_LIVE_CANARY_V1A,
) -> str:
    encoded = json.dumps(
        canary_policy_definition(policy), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{policy.schema_version}:{sha256(encoded).hexdigest()}"


def canary_context_lock_key(user_id: int, exchange: str, quote_asset: str) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise LivePolicyCanaryError("Canary user ID is invalid")
    context = (
        f"{LIMITED_LIVE_CANARY_V1A.schema_version}:"
        f"{user_id}:{exchange.strip().upper()}:{quote_asset.strip().upper()}"
    )
    return int.from_bytes(
        sha256(context.encode("utf-8")).digest()[:8], "big", signed=True
    )


def baseline_ranking_policy(settings: Settings):
    ranking_policy = HeuristicMarketRankingPolicy()
    signature = policy_signature(build_policy_data(settings, ranking_policy))
    return ranking_policy, signature


def canary_ranking_policy(approval, settings: Settings):
    scenario = parse_scenario_document(
        {
            "schema_version": SCENARIO_SCHEMA_VERSION,
            "scenarios": [
                {
                    "name": approval.scenario_name,
                    "component_weights": approval.component_weights,
                }
            ],
        }
    )[0]
    if scenario.definition_signature != approval.scenario_definition_signature:
        raise LivePolicyCanaryError("Candidate scenario definition does not verify")
    ranking_policy = HeuristicMarketRankingPolicy(
        HeuristicRankingWeights(**scenario.component_weights)
    )
    signature = policy_signature(build_policy_data(settings, ranking_policy))
    return ranking_policy, signature


_ACTIVATION_SIGNATURE_FIELDS = (
    "canary_schema_version",
    "canary_policy_definition",
    "canary_policy_definition_signature",
    "promotion_approval_id",
    "promotion_approval_signature",
    "candidate_id",
    "shadow_enrollment_id",
    "user_id",
    "exchange",
    "quote_asset",
    "scenario_name",
    "scenario_definition_signature",
    "component_weights",
    "dataset_schema_version",
    "baseline_policy_signature",
    "canary_policy_signature",
    "effective_top_n",
    "activation_source",
    "started_at",
    "expires_at",
    "max_analysis_runs",
)


def canary_activation_signature(value) -> str:
    payload = _canonicalize(
        {name: getattr(value, name) for name in _ACTIVATION_SIGNATURE_FIELDS}
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{LIMITED_LIVE_CANARY_V1A.schema_version}:{sha256(encoded).hexdigest()}"


_RUN_SIGNATURE_FIELDS = (
    "run_schema_version",
    "canary_activation_id",
    "activation_signature",
    "promotion_approval_id",
    "promotion_approval_signature",
    "candidate_id",
    "user_id",
    "exchange",
    "quote_asset",
    "analysis_run_id",
    "pipeline_run_id",
    "run_ordinal",
    "baseline_policy_signature",
    "canary_policy_signature",
    "used_canary_policy",
    "reserved_at",
)


def canary_run_signature(value) -> str:
    payload = _canonicalize(
        {name: getattr(value, name) for name in _RUN_SIGNATURE_FIELDS}
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{RUN_SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


def canary_run_count(session: Session, activation_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(LivePolicyCanaryRun)
            .where(LivePolicyCanaryRun.canary_activation_id == activation_id)
            .execution_options(autoflush=False)
        )
        or 0
    )


def validate_stored_canary_activation(
    session: Session, activation: LivePolicyCanaryActivation, settings: Settings
):
    validated = load_and_validate_shadow_policy_promotion_approval(
        session, activation.promotion_approval_id
    )
    if validated is None:
        raise LivePolicyCanaryError("Canary Promotion Approval does not exist")
    approval = validated.row
    expected_identity = {
        "promotion_approval_signature": approval.approval_signature,
        "candidate_id": approval.candidate_id,
        "shadow_enrollment_id": approval.shadow_enrollment_id,
        "user_id": approval.user_id,
        "exchange": approval.exchange,
        "quote_asset": approval.quote_asset,
        "scenario_name": approval.scenario_name,
        "scenario_definition_signature": approval.scenario_definition_signature,
        "component_weights": approval.component_weights,
        "dataset_schema_version": approval.dataset_schema_version,
        "baseline_policy_signature": approval.baseline_policy_signature,
        "effective_top_n": approval.effective_top_n,
    }
    baseline_policy, baseline_signature = baseline_ranking_policy(settings)
    candidate_policy, candidate_signature = canary_ranking_policy(approval, settings)
    if (
        activation.canary_schema_version != LIMITED_LIVE_CANARY_V1A.schema_version
        or activation.canary_policy_definition
        != canary_policy_definition(LIMITED_LIVE_CANARY_V1A)
        or activation.canary_policy_definition_signature
        != canary_policy_definition_signature(LIMITED_LIVE_CANARY_V1A)
        or any(
            _canonicalize(getattr(activation, name)) != _canonicalize(value)
            for name, value in expected_identity.items()
        )
        or activation.activation_source != ACTIVATION_SOURCE
        or activation.max_analysis_runs != LIMITED_LIVE_CANARY_V1A.max_analysis_runs
        or _utc(activation.expires_at, "Canary expires_at")
        != _utc(activation.started_at, "Canary started_at")
        + timedelta(hours=LIMITED_LIVE_CANARY_V1A.max_duration_hours)
        or settings.market_universe_mode != "DYNAMIC"
        or settings.market_universe_exchange.strip().upper() != approval.exchange
        or settings.market_universe_quote_asset.strip().upper() != approval.quote_asset
        or settings.market_universe_top_n != approval.effective_top_n
        or baseline_signature != approval.baseline_policy_signature
        or activation.canary_policy_signature != candidate_signature
        or activation.activation_signature != canary_activation_signature(activation)
    ):
        raise LivePolicyCanaryError("stored Canary activation provenance is invalid")
    return validated, baseline_policy, candidate_policy


@dataclass(frozen=True)
class LivePolicyCanaryActivationResult:
    activation_status: str
    safe_reason: str | None
    approval: object | None
    activation: LivePolicyCanaryActivation | None
    expected_approval_signature: str | None
    approval_signature_matched: bool
    canary_policy_schema_version: str
    canary_policy_definition: dict
    canary_policy_definition_signature: str
    proposed_started_at: datetime | None
    proposed_expires_at: datetime | None
    max_analysis_runs: int
    database_write: bool
    external_calls: bool
    ranking_runtime_activation_record_created: bool
    order_behavior_changed: bool
    canary_order_cap_enabled: bool


class LivePolicyCanaryActivationService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        now_fn: Callable[[], datetime] | None = None,
        lock_factory: Callable[[int], PostgresAdvisoryLock] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.lock_factory = lock_factory or PostgresAdvisoryLock
        validate_canary_policy()

    def preview(self, *, promotion_approval_id: int):
        return self._execute(promotion_approval_id, expected=None, apply=False)

    def activate(self, *, promotion_approval_id: int, expected_approval_signature: str):
        self._validate_expected_signature(expected_approval_signature)
        return self._execute(
            promotion_approval_id,
            expected=expected_approval_signature,
            apply=True,
        )

    def _execute(self, approval_id: int, *, expected: str | None, apply: bool):
        try:
            validated = load_and_validate_shadow_policy_promotion_approval(
                self.session, approval_id
            )
            if validated is None:
                return self._result(NO_PROMOTION_APPROVAL, expected=expected)
            approval = validated.row
            if apply and expected != approval.approval_signature:
                return self._result(
                    APPROVAL_SIGNATURE_CHANGED,
                    approval=approval,
                    expected=expected,
                    reason="expected signature differs from stored approval",
                )
            existing = self._existing_activation(approval.id)
            if existing is not None:
                validate_stored_canary_activation(self.session, existing, self.settings)
                return self._result(
                    ALREADY_ACTIVATED,
                    approval=approval,
                    activation=existing,
                    expected=expected,
                )
            started_at = _utc(self.now_fn(), "Canary activation clock")
            expires_at = started_at + timedelta(
                hours=LIMITED_LIVE_CANARY_V1A.max_duration_hours
            )
            activation = self._build_activation(
                approval, started_at=started_at, expires_at=expires_at
            )
            activation.activation_signature = canary_activation_signature(activation)
            if self._active_activations(approval, started_at):
                return self._result(
                    ACTIVE_CANARY_EXISTS,
                    approval=approval,
                    activation=activation,
                    expected=expected,
                    reason="another Canary is active for this context",
                    started_at=started_at,
                    expires_at=expires_at,
                )
            if not apply:
                return self._result(
                    DRY_RUN,
                    approval=approval,
                    activation=activation,
                    expected=expected,
                    started_at=started_at,
                    expires_at=expires_at,
                )
            if self.session.new or self.session.dirty or self.session.deleted:
                raise LivePolicyCanaryError(
                    "Activation requires a clean database session"
                )
            lock = self.lock_factory(
                canary_context_lock_key(
                    approval.user_id, approval.exchange, approval.quote_asset
                )
            )
            if not lock.acquire():
                return self._result(
                    CANARY_CONTEXT_BUSY,
                    approval=approval,
                    expected=expected,
                    reason="Canary context advisory lock is busy",
                )
            try:
                validated = load_and_validate_shadow_policy_promotion_approval(
                    self.session, approval_id
                )
                if validated is None:
                    raise LivePolicyCanaryError("Promotion Approval disappeared")
                approval = validated.row
                if expected != approval.approval_signature:
                    return self._result(
                        APPROVAL_SIGNATURE_CHANGED,
                        approval=approval,
                        expected=expected,
                    )
                existing = self._existing_activation(approval.id)
                if existing is not None:
                    validate_stored_canary_activation(
                        self.session, existing, self.settings
                    )
                    return self._result(
                        ALREADY_ACTIVATED,
                        approval=approval,
                        activation=existing,
                        expected=expected,
                    )
                started_at = _utc(self.now_fn(), "Canary activation clock")
                expires_at = started_at + timedelta(
                    hours=LIMITED_LIVE_CANARY_V1A.max_duration_hours
                )
                if self._active_activations(approval, started_at):
                    return self._result(
                        ACTIVE_CANARY_EXISTS,
                        approval=approval,
                        expected=expected,
                    )
                activation = self._build_activation(
                    approval, started_at=started_at, expires_at=expires_at
                )
                activation.activation_signature = canary_activation_signature(
                    activation
                )
                self.session.add(activation)
                self.session.flush()
                self.session.commit()
                return self._result(
                    CREATED,
                    approval=approval,
                    activation=activation,
                    expected=expected,
                    created=True,
                )
            finally:
                lock.release()
        except (
            LivePolicyCanaryError,
            ShadowPolicyPromotionApprovalError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
        ) as error:
            self.session.rollback()
            return self._result(
                INVALID_CANARY_ACTIVATION, expected=expected, reason=str(error)
            )

    def _build_activation(self, approval, *, started_at, expires_at):
        _, baseline_signature = baseline_ranking_policy(self.settings)
        _, candidate_signature = canary_ranking_policy(approval, self.settings)
        if (
            self.settings.market_universe_mode != "DYNAMIC"
            or self.settings.market_universe_exchange.strip().upper()
            != approval.exchange
            or self.settings.market_universe_quote_asset.strip().upper()
            != approval.quote_asset
            or self.settings.market_universe_top_n != approval.effective_top_n
            or baseline_signature != approval.baseline_policy_signature
        ):
            raise LivePolicyCanaryError(
                "Canary runtime context does not match approval"
            )
        return LivePolicyCanaryActivation(
            canary_schema_version=LIMITED_LIVE_CANARY_V1A.schema_version,
            canary_policy_definition=canary_policy_definition(),
            canary_policy_definition_signature=canary_policy_definition_signature(),
            promotion_approval_id=approval.id,
            promotion_approval_signature=approval.approval_signature,
            candidate_id=approval.candidate_id,
            shadow_enrollment_id=approval.shadow_enrollment_id,
            user_id=approval.user_id,
            exchange=approval.exchange,
            quote_asset=approval.quote_asset,
            scenario_name=approval.scenario_name,
            scenario_definition_signature=approval.scenario_definition_signature,
            component_weights=_canonicalize(approval.component_weights),
            dataset_schema_version=approval.dataset_schema_version,
            baseline_policy_signature=approval.baseline_policy_signature,
            canary_policy_signature=candidate_signature,
            effective_top_n=approval.effective_top_n,
            activation_source=ACTIVATION_SOURCE,
            started_at=started_at,
            expires_at=expires_at,
            max_analysis_runs=LIMITED_LIVE_CANARY_V1A.max_analysis_runs,
            activation_signature="",
        )

    def _existing_activation(self, approval_id):
        return self.session.scalar(
            select(LivePolicyCanaryActivation)
            .where(LivePolicyCanaryActivation.promotion_approval_id == approval_id)
            .execution_options(autoflush=False)
        )

    def _active_activations(self, approval, now):
        rows = self.session.scalars(
            select(LivePolicyCanaryActivation)
            .where(
                LivePolicyCanaryActivation.user_id == approval.user_id,
                LivePolicyCanaryActivation.exchange == approval.exchange,
                LivePolicyCanaryActivation.quote_asset == approval.quote_asset,
                LivePolicyCanaryActivation.started_at <= now,
                LivePolicyCanaryActivation.expires_at > now,
            )
            .execution_options(autoflush=False)
        ).all()
        return [
            row
            for row in rows
            if canary_run_count(self.session, row.id) < row.max_analysis_runs
        ]

    @staticmethod
    def _validate_expected_signature(value):
        prefix = "human-approved-promotion-v1:"
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value.startswith(prefix)
            or len(value) != len(prefix) + 64
            or any(
                character not in "0123456789abcdef"
                for character in value[len(prefix) :]
            )
        ):
            raise ReplayInputError("expected Promotion Approval signature is invalid")

    @staticmethod
    def _result(
        status,
        *,
        approval=None,
        activation=None,
        expected=None,
        reason=None,
        started_at=None,
        expires_at=None,
        created=False,
    ):
        return LivePolicyCanaryActivationResult(
            activation_status=status,
            safe_reason=reason,
            approval=approval,
            activation=activation,
            expected_approval_signature=expected,
            approval_signature_matched=(
                expected is not None
                and approval is not None
                and expected == approval.approval_signature
            ),
            canary_policy_schema_version=LIMITED_LIVE_CANARY_V1A.schema_version,
            canary_policy_definition=canary_policy_definition(),
            canary_policy_definition_signature=canary_policy_definition_signature(),
            proposed_started_at=started_at,
            proposed_expires_at=expires_at,
            max_analysis_runs=LIMITED_LIVE_CANARY_V1A.max_analysis_runs,
            database_write=created,
            external_calls=False,
            ranking_runtime_activation_record_created=created,
            order_behavior_changed=False,
            canary_order_cap_enabled=False,
        )


__all__ = [
    "ACTIVATION_SOURCE",
    "ACTIVE_CANARY_EXISTS",
    "ALREADY_ACTIVATED",
    "APPROVAL_SIGNATURE_CHANGED",
    "BASELINE_EXHAUSTED",
    "BASELINE_EXPIRED",
    "BASELINE_INVALID_CANARY",
    "BASELINE_NO_CANARY",
    "CANARY",
    "CANARY_CONTEXT_BUSY",
    "CREATED",
    "DRY_RUN",
    "INVALID_CANARY_ACTIVATION",
    "LIMITED_LIVE_CANARY_V1A",
    "LivePolicyCanaryActivationResult",
    "LivePolicyCanaryActivationService",
    "LivePolicyCanaryError",
    "NO_PROMOTION_APPROVAL",
    "REPORT_TYPE",
    "RUN_SCHEMA_VERSION",
    "STATUS_REPORT_TYPE",
    "baseline_ranking_policy",
    "canary_activation_signature",
    "canary_context_lock_key",
    "canary_policy_definition",
    "canary_policy_definition_signature",
    "canary_ranking_policy",
    "canary_run_count",
    "canary_run_signature",
    "validate_stored_canary_activation",
]
