from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    LivePolicyCanaryActivation,
    LivePolicyCanaryTerminationEvent,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.live_policy_canary_service import (
    CANARY,
    CANARY_CONTEXT_BUSY,
    LivePolicyCanaryError,
    _canonicalize,
    canary_context_lock_key,
    canary_run_count,
    load_and_validate_canary_safety_binding,
    validate_stored_canary_activation,
)
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertService,
)
from crypto_trading_bot.services.offline_strategy_replay_service import ReplayInputError


REPORT_TYPE = "LIMITED_LIVE_CANARY_V1B_TERMINATION"
TERMINATION_SCHEMA_VERSION = "limited-live-canary-termination-v1b"
TERMINATION_SOURCE = "MANUAL_CLI"
TERMINATION_REASON = "MANUAL_STOP"

NO_CANARY_ACTIVATION = "NO_CANARY_ACTIVATION"
ACTIVATION_SIGNATURE_CHANGED = "ACTIVATION_SIGNATURE_CHANGED"
CANARY_NOT_ACTIVE = "CANARY_NOT_ACTIVE"
DRY_RUN = "DRY_RUN"
STOPPED = "STOPPED"
ALREADY_STOPPED = "ALREADY_STOPPED"
INVALID_CANARY_TERMINATION = "INVALID_CANARY_TERMINATION"


class LivePolicyCanaryTerminationError(ValueError):
    pass


_TERMINATION_SIGNATURE_FIELDS = (
    "termination_schema_version",
    "canary_activation_id",
    "activation_signature",
    "safety_binding_id",
    "safety_binding_signature",
    "promotion_approval_id",
    "promotion_approval_signature",
    "candidate_id",
    "user_id",
    "exchange",
    "quote_asset",
    "termination_source",
    "termination_reason",
    "terminated_at",
)


def canary_termination_signature(value) -> str:
    payload = _canonicalize(
        {name: getattr(value, name) for name in _TERMINATION_SIGNATURE_FIELDS}
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{TERMINATION_SCHEMA_VERSION}:{sha256(encoded).hexdigest()}"


def load_and_validate_canary_termination(session: Session, activation, binding):
    event = session.scalar(
        select(LivePolicyCanaryTerminationEvent)
        .where(LivePolicyCanaryTerminationEvent.canary_activation_id == activation.id)
        .execution_options(autoflush=False)
    )
    if event is None:
        return None
    expected = {
        "termination_schema_version": TERMINATION_SCHEMA_VERSION,
        "canary_activation_id": activation.id,
        "activation_signature": activation.activation_signature,
        "safety_binding_id": binding.id,
        "safety_binding_signature": binding.binding_signature,
        "promotion_approval_id": activation.promotion_approval_id,
        "promotion_approval_signature": activation.promotion_approval_signature,
        "candidate_id": activation.candidate_id,
        "user_id": activation.user_id,
        "exchange": activation.exchange,
        "quote_asset": activation.quote_asset,
        "termination_source": TERMINATION_SOURCE,
        "termination_reason": TERMINATION_REASON,
    }
    if any(
        getattr(event, name) != value for name, value in expected.items()
    ) or event.termination_signature != canary_termination_signature(event):
        raise LivePolicyCanaryTerminationError(
            "stored Canary termination provenance is invalid"
        )
    return event


@dataclass(frozen=True)
class LivePolicyCanaryTerminationResult:
    termination_status: str
    safe_reason: str | None
    activation: LivePolicyCanaryActivation | None
    safety_binding: object | None
    termination_event: LivePolicyCanaryTerminationEvent | None
    current_mode: str | None
    expected_activation_signature: str | None
    activation_signature_matched: bool
    proposed_terminated_at: datetime | None
    database_write: bool
    external_calls: bool
    ranking_runtime_changed: bool
    order_cancelled: bool


class LivePolicyCanaryTerminationService:
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

    def preview(self, *, canary_activation_id: int):
        return self._execute(canary_activation_id, expected=None, apply=False)

    def stop(self, *, canary_activation_id: int, expected_activation_signature: str):
        self._validate_expected_signature(expected_activation_signature)
        return self._execute(
            canary_activation_id,
            expected=expected_activation_signature,
            apply=True,
        )

    def _execute(self, activation_id: int, *, expected: str | None, apply: bool):
        try:
            activation = self.session.get(LivePolicyCanaryActivation, activation_id)
            if activation is None:
                return self._result(NO_CANARY_ACTIVATION, expected=expected)
            binding = self._validate(activation)
            existing = load_and_validate_canary_termination(
                self.session, activation, binding
            )
            if existing is not None:
                return self._result(
                    ALREADY_STOPPED,
                    activation=activation,
                    binding=binding,
                    event=existing,
                    expected=expected,
                    current_mode="BASELINE_STOPPED",
                )
            if apply and expected != activation.activation_signature:
                return self._result(
                    ACTIVATION_SIGNATURE_CHANGED,
                    activation=activation,
                    binding=binding,
                    expected=expected,
                    reason="expected signature differs from stored Activation",
                )
            now = self._now()
            if not self._is_active(activation, now):
                return self._result(
                    CANARY_NOT_ACTIVE,
                    activation=activation,
                    binding=binding,
                    expected=expected,
                    reason="Canary is expired, exhausted, or not started",
                )
            if not apply:
                return self._result(
                    DRY_RUN,
                    activation=activation,
                    binding=binding,
                    expected=expected,
                    current_mode=CANARY,
                    proposed_at=now,
                )
            if self.session.new or self.session.dirty or self.session.deleted:
                raise LivePolicyCanaryTerminationError(
                    "Termination requires a clean database session"
                )
            lock = self.lock_factory(
                canary_context_lock_key(
                    activation.user_id, activation.exchange, activation.quote_asset
                )
            )
            if not lock.acquire():
                return self._result(
                    CANARY_CONTEXT_BUSY,
                    activation=activation,
                    binding=binding,
                    expected=expected,
                    reason="Canary context advisory lock is busy",
                )
            try:
                self.session.expire_all()
                activation = self.session.get(LivePolicyCanaryActivation, activation_id)
                if activation is None:
                    raise LivePolicyCanaryTerminationError(
                        "Canary Activation disappeared"
                    )
                binding = self._validate(activation)
                existing = load_and_validate_canary_termination(
                    self.session, activation, binding
                )
                if existing is not None:
                    return self._result(
                        ALREADY_STOPPED,
                        activation=activation,
                        binding=binding,
                        event=existing,
                        expected=expected,
                        current_mode="BASELINE_STOPPED",
                    )
                if expected != activation.activation_signature:
                    return self._result(
                        ACTIVATION_SIGNATURE_CHANGED,
                        activation=activation,
                        binding=binding,
                        expected=expected,
                    )
                now = self._now()
                if not self._is_active(activation, now):
                    return self._result(
                        CANARY_NOT_ACTIVE,
                        activation=activation,
                        binding=binding,
                        expected=expected,
                    )
                event = self._build_event(activation, binding, now)
                self.session.add(event)
                self.session.flush()
                OperationalAlertService(self.session).create_canary_alert(
                    alert_type="LIVE_CANARY_STOPPED",
                    dedup_key=f"CANARY_STOPPED:{activation.id}",
                    safe_message=(
                        "Limited LIVE Canary가 수동으로 중지되었습니다. "
                        f"activation_id={activation.id}"
                    ),
                    user_id=activation.user_id,
                )
                self.session.commit()
                return self._result(
                    STOPPED,
                    activation=activation,
                    binding=binding,
                    event=event,
                    expected=expected,
                    current_mode="BASELINE_STOPPED",
                    proposed_at=now,
                    created=True,
                )
            finally:
                lock.release()
        except (
            LivePolicyCanaryError,
            LivePolicyCanaryTerminationError,
            ValueError,
        ) as error:
            self.session.rollback()
            return self._result(
                INVALID_CANARY_TERMINATION, expected=expected, reason=str(error)
            )

    def _validate(self, activation):
        validate_stored_canary_activation(self.session, activation, self.settings)
        return load_and_validate_canary_safety_binding(self.session, activation)

    def _is_active(self, activation, now):
        return (
            activation.started_at.astimezone(UTC) <= now
            and now < activation.expires_at.astimezone(UTC)
            and canary_run_count(self.session, activation.id)
            < activation.max_analysis_runs
        )

    @staticmethod
    def _build_event(activation, binding, now):
        event = LivePolicyCanaryTerminationEvent(
            termination_schema_version=TERMINATION_SCHEMA_VERSION,
            canary_activation_id=activation.id,
            activation_signature=activation.activation_signature,
            safety_binding_id=binding.id,
            safety_binding_signature=binding.binding_signature,
            promotion_approval_id=activation.promotion_approval_id,
            promotion_approval_signature=activation.promotion_approval_signature,
            candidate_id=activation.candidate_id,
            user_id=activation.user_id,
            exchange=activation.exchange,
            quote_asset=activation.quote_asset,
            termination_source=TERMINATION_SOURCE,
            termination_reason=TERMINATION_REASON,
            terminated_at=now,
            termination_signature="",
        )
        event.termination_signature = canary_termination_signature(event)
        return event

    def _now(self):
        value = self.now_fn()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LivePolicyCanaryTerminationError(
                "Canary termination clock must be timezone-aware"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _validate_expected_signature(value):
        prefix = "limited-live-canary-v1a:"
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
            raise ReplayInputError("expected Canary Activation signature is invalid")

    @staticmethod
    def _result(
        status,
        *,
        activation=None,
        binding=None,
        event=None,
        expected=None,
        reason=None,
        current_mode=None,
        proposed_at=None,
        created=False,
    ):
        return LivePolicyCanaryTerminationResult(
            termination_status=status,
            safe_reason=reason,
            activation=activation,
            safety_binding=binding,
            termination_event=event,
            current_mode=current_mode,
            expected_activation_signature=expected,
            activation_signature_matched=(
                expected is not None
                and activation is not None
                and expected == activation.activation_signature
            ),
            proposed_terminated_at=proposed_at,
            database_write=created,
            external_calls=False,
            ranking_runtime_changed=created,
            order_cancelled=False,
        )
