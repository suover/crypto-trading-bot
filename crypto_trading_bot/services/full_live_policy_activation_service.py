from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    FullLivePolicyActivation,
    FullLivePolicyTerminationEvent,
    StrategyReplaySnapshot,
)
from crypto_trading_bot.services.full_live_policy_provenance import (
    ACTIVATION_SCHEMA_VERSION,
    MANUAL_CLI,
    FullLivePolicyIntegrityError,
    activation_signature,
    aware_utc,
    canonicalize,
    normalize_context,
    validate_activation,
    validate_termination,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    apply_overrides,
    restore_weights,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ShadowPolicyPromotionApprovalError,
    load_and_validate_shadow_policy_promotion_approval,
    promotion_approval_signature,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)


REPORT_TYPE = "FULL_LIVE_POLICY_ACTIVATION_V1"
DRY_RUN = "DRY_RUN"
CREATED = "CREATED"
ALREADY_ACTIVE = "ALREADY_ACTIVE"
ALREADY_TERMINATED = "ALREADY_TERMINATED"
ACTIVE_FULL_LIVE_EXISTS = "ACTIVE_FULL_LIVE_EXISTS"
BASELINE_MISMATCH = "BASELINE_MISMATCH"
INVALID_APPROVAL = "INVALID_APPROVAL"
INVALID_POLICY = "INVALID_POLICY"
CONTEXT_MISMATCH = "CONTEXT_MISMATCH"


class FullLivePolicyActivationError(ValueError):
    pass


class _Rejected(FullLivePolicyActivationError):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class FullLivePolicyActivationResult:
    promotion_approval_id: int
    candidate_id: int | None
    user_id: int
    exchange: str
    quote_asset: str
    scenario_name: str | None
    baseline_policy_signature: str | None
    current_baseline_policy_signature: str | None
    effective_policy_signature: str | None
    activation: FullLivePolicyActivation | None
    activation_status: str
    safe_reason: str | None
    database_write: bool
    live_policy_change: bool
    live_order_change: bool
    external_calls: bool


class FullLivePolicyActivationService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        now_fn: Callable[[], datetime] | None = None,
        before_insert_fn: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.before_insert_fn = before_insert_fn

    def preview(
        self,
        *,
        promotion_approval_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
    ) -> FullLivePolicyActivationResult:
        return self._execute(
            promotion_approval_id=promotion_approval_id,
            user_id=user_id,
            exchange=exchange,
            quote_asset=quote_asset,
            expected_signature=None,
            apply=False,
        )

    def activate(
        self,
        *,
        promotion_approval_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
        expected_approval_signature: str,
    ) -> FullLivePolicyActivationResult:
        if (
            not isinstance(expected_approval_signature, str)
            or not expected_approval_signature.startswith(
                "human-approved-promotion-v1:"
            )
            or len(expected_approval_signature)
            != len("human-approved-promotion-v1:") + 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_approval_signature.rsplit(":", 1)[1]
            )
        ):
            raise FullLivePolicyActivationError(
                "expected promotion approval signature is invalid"
            )
        return self._execute(
            promotion_approval_id=promotion_approval_id,
            user_id=user_id,
            exchange=exchange,
            quote_asset=quote_asset,
            expected_signature=expected_approval_signature,
            apply=True,
        )

    def _execute(
        self,
        *,
        promotion_approval_id: int,
        user_id: int,
        exchange: str,
        quote_asset: str,
        expected_signature: str | None,
        apply: bool,
    ) -> FullLivePolicyActivationResult:
        normalized_exchange, normalized_quote = normalize_context(exchange, quote_asset)
        context = {
            "promotion_approval_id": promotion_approval_id,
            "candidate_id": None,
            "user_id": user_id,
            "exchange": normalized_exchange,
            "quote_asset": normalized_quote,
            "scenario_name": None,
            "baseline_policy_signature": None,
            "current_baseline_policy_signature": None,
            "effective_policy_signature": None,
        }
        try:
            if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
                raise _Rejected(CONTEXT_MISMATCH, "user ID must be a positive integer")
            if apply:
                self._lock_context(user_id, normalized_exchange, normalized_quote)
            validated = load_and_validate_shadow_policy_promotion_approval(
                self.session, promotion_approval_id
            )
            approval = validated.approval
            context.update(
                candidate_id=approval.candidate_id,
                scenario_name=approval.scenario_name,
                baseline_policy_signature=approval.baseline_policy_signature,
            )
            if (
                approval.user_id != user_id
                or approval.exchange != normalized_exchange
                or approval.quote_asset != normalized_quote
            ):
                raise _Rejected(
                    CONTEXT_MISMATCH, "promotion approval context does not match"
                )
            if apply and approval.approval_signature != expected_signature:
                raise _Rejected(
                    INVALID_APPROVAL, "promotion approval signature changed"
                )
            if approval.approval_signature != promotion_approval_signature(approval):
                raise _Rejected(
                    INVALID_APPROVAL, "promotion approval signature does not verify"
                )
            current_exchange = self.settings.market_universe_exchange.strip().upper()
            current_quote = self.settings.market_universe_quote_asset.strip().upper()
            if (
                normalized_exchange != current_exchange
                or normalized_quote != current_quote
            ):
                raise _Rejected(
                    CONTEXT_MISMATCH,
                    "activation context is not the configured runtime context",
                )
            baseline_policy = HeuristicMarketRankingPolicy()
            current_baseline = policy_signature(
                build_policy_data(self.settings, baseline_policy)
            )
            context["current_baseline_policy_signature"] = current_baseline
            if current_baseline != approval.baseline_policy_signature:
                raise _Rejected(
                    BASELINE_MISMATCH,
                    "current baseline policy differs from the approved baseline",
                )
            if approval.effective_top_n != self.settings.market_universe_top_n:
                raise _Rejected(
                    BASELINE_MISMATCH,
                    "current TopN differs from the approved effective TopN",
                )
            existing = self._find_by_approval(promotion_approval_id)
            if existing is not None:
                self._validate_existing_anchor(existing, approval)
                termination = self._termination(existing)
                if termination is not None:
                    validate_termination(termination, existing)
                    return self._result(
                        context,
                        existing,
                        ALREADY_TERMINATED,
                        "promotion approval was already activated and terminated",
                    )
                return self._result(context, existing, ALREADY_ACTIVE)
            active = self._active_for_context(
                user_id, normalized_exchange, normalized_quote
            )
            if active is not None:
                return self._result(
                    context,
                    active,
                    ACTIVE_FULL_LIVE_EXISTS,
                    "another Full LIVE policy is already active for this context",
                )
            effective_policy, definition, effective_signature = (
                self._reconstruct_effective_policy(validated)
            )
            del effective_policy
            context["effective_policy_signature"] = effective_signature
            if not apply:
                return self._result(context, None, DRY_RUN)
            activated_at = aware_utc(self.now_fn(), "activation clock")
            activation = FullLivePolicyActivation(
                activation_schema_version=ACTIVATION_SCHEMA_VERSION,
                promotion_approval_id=approval.id,
                promotion_approval_signature=approval.approval_signature,
                candidate_id=approval.candidate_id,
                shadow_enrollment_id=approval.shadow_enrollment_id,
                user_id=approval.user_id,
                exchange=approval.exchange,
                quote_asset=approval.quote_asset,
                scenario_name=approval.scenario_name,
                scenario_definition_signature=(approval.scenario_definition_signature),
                component_weights=canonicalize(approval.component_weights),
                baseline_policy_signature=approval.baseline_policy_signature,
                effective_policy_definition=definition,
                effective_policy_signature=effective_signature,
                effective_top_n=approval.effective_top_n,
                activation_source=MANUAL_CLI,
                activated_at=activated_at,
                activation_signature="",
            )
            activation.activation_signature = activation_signature(activation)
            if self.before_insert_fn is not None:
                self.before_insert_fn()
            try:
                with self.session.begin_nested():
                    self.session.add(activation)
                    self.session.flush()
            except IntegrityError:
                concurrent = self._find_by_approval(promotion_approval_id)
                if concurrent is None:
                    concurrent = self._active_for_context(
                        user_id, normalized_exchange, normalized_quote
                    )
                    return self._result(
                        context,
                        concurrent,
                        ACTIVE_FULL_LIVE_EXISTS,
                        "concurrent Full LIVE activation occupied the context",
                    )
                self._validate_existing_anchor(concurrent, approval)
                return self._result(context, concurrent, ALREADY_ACTIVE)
            return self._result(context, activation, CREATED, created=True)
        except _Rejected as error:
            return self._result(context, None, error.status, str(error))
        except ShadowPolicyPromotionApprovalError as error:
            return self._result(context, None, INVALID_APPROVAL, str(error))
        except (FullLivePolicyIntegrityError, TypeError, ValueError) as error:
            return self._result(context, None, INVALID_POLICY, str(error))

    def _reconstruct_effective_policy(self, validated):
        candidate = validated.candidate
        reference = self.session.get(
            StrategyReplaySnapshot, candidate.reference_snapshot_id
        )
        if reference is None:
            raise _Rejected(INVALID_POLICY, "reference policy snapshot is missing")
        if (
            reference.policy_signature != candidate.baseline_policy_signature
            or policy_signature(reference.policy_data) != reference.policy_signature
        ):
            raise _Rejected(INVALID_POLICY, "reference baseline policy is invalid")
        try:
            baseline_weights, top_n = restore_weights(reference.policy_data)
            effective_weights = apply_overrides(
                baseline_weights, candidate.component_weights
            )
        except Exception as error:
            raise _Rejected(
                INVALID_POLICY, f"effective component weights are invalid: {error}"
            ) from error
        if top_n != candidate.effective_top_n:
            raise _Rejected(INVALID_POLICY, "reference effective TopN mismatch")
        policy = HeuristicMarketRankingPolicy(effective_weights)
        definition = build_policy_data(self.settings, policy)
        return policy, definition, policy_signature(definition)

    def _find_by_approval(self, approval_id: int):
        return self.session.scalar(
            select(FullLivePolicyActivation)
            .where(FullLivePolicyActivation.promotion_approval_id == approval_id)
            .execution_options(autoflush=False)
        )

    def _termination(self, activation):
        return self.session.scalar(
            select(FullLivePolicyTerminationEvent)
            .where(FullLivePolicyTerminationEvent.activation_id == activation.id)
            .execution_options(autoflush=False)
        )

    def _active_for_context(self, user_id: int, exchange: str, quote_asset: str):
        rows = tuple(
            self.session.scalars(
                select(FullLivePolicyActivation)
                .where(
                    FullLivePolicyActivation.user_id == user_id,
                    FullLivePolicyActivation.exchange == exchange,
                    FullLivePolicyActivation.quote_asset == quote_asset,
                )
                .order_by(
                    FullLivePolicyActivation.activated_at, FullLivePolicyActivation.id
                )
                .execution_options(autoflush=False)
            )
        )
        active = []
        for row in rows:
            validate_activation(row)
            termination = self._termination(row)
            if termination is None:
                active.append(row)
            else:
                validate_termination(termination, row)
        if len(active) > 1:
            raise FullLivePolicyIntegrityError(
                "multiple active Full LIVE policies exist for one context"
            )
        return active[0] if active else None

    @staticmethod
    def _validate_existing_anchor(activation, approval) -> None:
        validate_activation(activation)
        if (
            activation.promotion_approval_id != approval.id
            or activation.promotion_approval_signature != approval.approval_signature
            or activation.candidate_id != approval.candidate_id
            or activation.shadow_enrollment_id != approval.shadow_enrollment_id
            or activation.user_id != approval.user_id
            or activation.exchange != approval.exchange
            or activation.quote_asset != approval.quote_asset
            or activation.scenario_name != approval.scenario_name
            or activation.scenario_definition_signature
            != approval.scenario_definition_signature
            or canonicalize(activation.component_weights)
            != canonicalize(approval.component_weights)
            or activation.baseline_policy_signature
            != approval.baseline_policy_signature
            or activation.effective_top_n != approval.effective_top_n
        ):
            raise FullLivePolicyIntegrityError(
                "existing activation approval anchor is invalid"
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
        context: dict,
        activation,
        status: str,
        safe_reason: str | None = None,
        *,
        created: bool = False,
    ) -> FullLivePolicyActivationResult:
        return FullLivePolicyActivationResult(
            **context,
            activation=activation,
            activation_status=status,
            safe_reason=safe_reason,
            database_write=created,
            live_policy_change=created,
            live_order_change=False,
            external_calls=False,
        )


__all__ = [
    "ACTIVE_FULL_LIVE_EXISTS",
    "ALREADY_ACTIVE",
    "ALREADY_TERMINATED",
    "BASELINE_MISMATCH",
    "CONTEXT_MISMATCH",
    "CREATED",
    "DRY_RUN",
    "FullLivePolicyActivationError",
    "FullLivePolicyActivationResult",
    "FullLivePolicyActivationService",
    "INVALID_APPROVAL",
    "INVALID_POLICY",
    "REPORT_TYPE",
]
