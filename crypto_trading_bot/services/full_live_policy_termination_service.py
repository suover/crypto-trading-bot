from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.db.models import (
    FullLivePolicyActivation,
    FullLivePolicyTerminationEvent,
)
from crypto_trading_bot.services.full_live_policy_provenance import (
    MANUAL_CLI,
    TERMINATION_SCHEMA_VERSION,
    FullLivePolicyIntegrityError,
    aware_utc,
    normalize_context,
    termination_signature,
    validate_activation,
    validate_termination,
)


REPORT_TYPE = "FULL_LIVE_POLICY_TERMINATION_V1"
DRY_RUN = "DRY_RUN"
TERMINATED = "TERMINATED"
ALREADY_TERMINATED = "ALREADY_TERMINATED"
NOT_FOUND = "NOT_FOUND"
CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
INVALID_ACTIVATION = "INVALID_ACTIVATION"


class FullLivePolicyTerminationError(ValueError):
    pass


@dataclass(frozen=True)
class FullLivePolicyTerminationResult:
    activation_id: int
    user_id: int
    exchange: str
    quote_asset: str
    activation: FullLivePolicyActivation | None
    termination: FullLivePolicyTerminationEvent | None
    termination_status: str
    safe_reason: str | None
    database_write: bool
    live_policy_change: bool
    live_order_change: bool
    external_calls: bool


class FullLivePolicyTerminationService:
    def __init__(
        self,
        session: Session,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def preview(
        self,
        *,
        activation_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
    ) -> FullLivePolicyTerminationResult:
        return self._execute(
            activation_id=activation_id,
            user_id=user_id,
            exchange=exchange,
            quote_asset=quote_asset,
            expected_signature=None,
            reason=None,
            apply=False,
        )

    def terminate(
        self,
        *,
        activation_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
        expected_activation_signature: str,
        reason: str,
    ) -> FullLivePolicyTerminationResult:
        prefix = "full-live-policy-activation-v1:"
        if (
            not isinstance(expected_activation_signature, str)
            or not expected_activation_signature.startswith(prefix)
            or len(expected_activation_signature) != len(prefix) + 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_activation_signature[len(prefix) :]
            )
        ):
            raise FullLivePolicyTerminationError(
                "expected activation signature is invalid"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise FullLivePolicyTerminationError("termination reason is required")
        if reason != reason.strip():
            raise FullLivePolicyTerminationError(
                "termination reason must not have surrounding whitespace"
            )
        return self._execute(
            activation_id=activation_id,
            user_id=user_id,
            exchange=exchange,
            quote_asset=quote_asset,
            expected_signature=expected_activation_signature,
            reason=reason,
            apply=True,
        )

    def _execute(
        self,
        *,
        activation_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
        expected_signature: str | None,
        reason: str | None,
        apply: bool,
    ) -> FullLivePolicyTerminationResult:
        normalized_exchange, normalized_quote = normalize_context(exchange, quote_asset)
        if (
            isinstance(activation_id, bool)
            or not isinstance(activation_id, int)
            or activation_id < 1
        ):
            raise FullLivePolicyTerminationError(
                "activation ID must be a positive integer"
            )
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise FullLivePolicyTerminationError("user ID must be a positive integer")
        if apply:
            self._lock_context(user_id, normalized_exchange, normalized_quote)
        activation = self.session.get(FullLivePolicyActivation, activation_id)
        if activation is None:
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                None,
                None,
                NOT_FOUND,
                "Full LIVE activation does not exist",
            )
        try:
            validate_activation(activation)
        except FullLivePolicyIntegrityError as error:
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                None,
                INVALID_ACTIVATION,
                str(error),
            )
        if (
            activation.user_id != user_id
            or activation.exchange != normalized_exchange
            or activation.quote_asset != normalized_quote
        ):
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                None,
                CONTEXT_MISMATCH,
                "activation context does not match",
            )
        existing = self._termination(activation_id)
        if existing is not None:
            try:
                validate_termination(existing, activation)
            except FullLivePolicyIntegrityError as error:
                return self._result(
                    activation_id,
                    user_id,
                    normalized_exchange,
                    normalized_quote,
                    activation,
                    existing,
                    INVALID_ACTIVATION,
                    str(error),
                )
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                existing,
                ALREADY_TERMINATED,
            )
        if not apply:
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                None,
                DRY_RUN,
            )
        if expected_signature != activation.activation_signature:
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                None,
                SIGNATURE_MISMATCH,
                "expected activation signature does not match",
            )
        terminated_at = aware_utc(self.now_fn(), "termination clock")
        if terminated_at < aware_utc(activation.activated_at, "activated_at"):
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                None,
                INVALID_ACTIVATION,
                "termination time precedes activation",
            )
        event = FullLivePolicyTerminationEvent(
            termination_schema_version=TERMINATION_SCHEMA_VERSION,
            activation_id=activation.id,
            activation_signature=activation.activation_signature,
            user_id=activation.user_id,
            exchange=activation.exchange,
            quote_asset=activation.quote_asset,
            termination_source=MANUAL_CLI,
            termination_reason=reason,
            terminated_at=terminated_at,
            termination_signature="",
        )
        event.termination_signature = termination_signature(event)
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
        except IntegrityError:
            concurrent = self._termination(activation_id)
            if concurrent is None:
                raise FullLivePolicyTerminationError(
                    "termination uniqueness conflict"
                ) from None
            validate_termination(concurrent, activation)
            return self._result(
                activation_id,
                user_id,
                normalized_exchange,
                normalized_quote,
                activation,
                concurrent,
                ALREADY_TERMINATED,
            )
        return self._result(
            activation_id,
            user_id,
            normalized_exchange,
            normalized_quote,
            activation,
            event,
            TERMINATED,
            created=True,
        )

    def _termination(self, activation_id: int):
        return self.session.scalar(
            select(FullLivePolicyTerminationEvent)
            .where(FullLivePolicyTerminationEvent.activation_id == activation_id)
            .execution_options(autoflush=False)
        )

    def _lock_context(self, user_id: int, exchange: str, quote_asset: str) -> None:
        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        digest = sha256(
            f"full-live-policy:{user_id}:{exchange}:{quote_asset}".encode()
        ).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": key}
        )

    @staticmethod
    def _result(
        activation_id,
        user_id,
        exchange,
        quote_asset,
        activation,
        termination,
        status,
        safe_reason=None,
        *,
        created=False,
    ):
        return FullLivePolicyTerminationResult(
            activation_id=activation_id,
            user_id=user_id,
            exchange=exchange,
            quote_asset=quote_asset,
            activation=activation,
            termination=termination,
            termination_status=status,
            safe_reason=safe_reason,
            database_write=created,
            live_policy_change=created,
            live_order_change=False,
            external_calls=False,
        )


__all__ = [
    "ALREADY_TERMINATED",
    "CONTEXT_MISMATCH",
    "DRY_RUN",
    "FullLivePolicyTerminationError",
    "FullLivePolicyTerminationResult",
    "FullLivePolicyTerminationService",
    "INVALID_ACTIVATION",
    "NOT_FOUND",
    "REPORT_TYPE",
    "SIGNATURE_MISMATCH",
    "TERMINATED",
]
