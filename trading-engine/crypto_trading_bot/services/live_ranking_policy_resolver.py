from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    MarketRankingPolicy,
)
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    FullLivePolicyActivation,
    FullLivePolicyTerminationEvent,
    ShadowPolicyPromotionApproval,
)
from crypto_trading_bot.services.full_live_policy_provenance import (
    FullLivePolicyIntegrityError,
    canonicalize,
    normalize_context,
    validate_activation,
    validate_termination,
)
from crypto_trading_bot.services.runtime_user_resolver import validate_user_id
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    promotion_approval_signature,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)

BASELINE = "BASELINE"
FULL_LIVE = "FULL_LIVE"


@dataclass(frozen=True)
class LiveRankingPolicyResolution:
    """Read-only runtime ranking policy and immutable provenance."""

    user_id: int
    exchange: str
    quote_asset: str
    mode: str
    ranking_policy: MarketRankingPolicy
    baseline_policy_signature: str
    effective_policy_signature: str
    full_live_activation_id: int | None = None
    promotion_approval_id: int | None = None
    scenario_name: str | None = None
    activated_at: datetime | None = None
    terminated: bool = False
    database_write: bool = False
    external_calls: bool = False
    live_policy_change: bool = False
    ranking_runtime_changed: bool = False


class LiveRankingPolicyLease:
    def __init__(self, resolution: LiveRankingPolicyResolution) -> None:
        self.resolution = resolution

    @property
    def ranking_policy(self) -> MarketRankingPolicy:
        return self.resolution.ranking_policy

    def __enter__(self) -> "LiveRankingPolicyLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class LiveRankingPolicyResolver:
    """Resolve one context to BASELINE or a validated FULL_LIVE policy."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        **_: object,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def resolve(
        self, *, user_id: int, exchange: str, quote_asset: str
    ) -> LiveRankingPolicyLease:
        return LiveRankingPolicyLease(
            self.inspect(user_id=user_id, exchange=exchange, quote_asset=quote_asset)
        )

    def inspect(
        self, *, user_id: int, exchange: str, quote_asset: str
    ) -> LiveRankingPolicyResolution:
        normalized_user_id = validate_user_id(user_id)
        normalized_exchange, normalized_quote = normalize_context(exchange, quote_asset)
        baseline_policy = HeuristicMarketRankingPolicy()
        baseline_signature = policy_signature(
            build_policy_data(self.settings, baseline_policy)
        )
        activations = tuple(
            self.session.scalars(
                select(FullLivePolicyActivation)
                .where(
                    FullLivePolicyActivation.user_id == normalized_user_id,
                    FullLivePolicyActivation.exchange == normalized_exchange,
                    FullLivePolicyActivation.quote_asset == normalized_quote,
                )
                .order_by(
                    FullLivePolicyActivation.activated_at,
                    FullLivePolicyActivation.id,
                )
                .execution_options(autoflush=False)
            )
        )
        active: list[
            tuple[
                FullLivePolicyActivation,
                MarketRankingPolicy,
                ShadowPolicyPromotionApproval,
            ]
        ] = []
        for activation in activations:
            ranking_policy = validate_activation(activation)
            approval = self.session.get(
                ShadowPolicyPromotionApproval, activation.promotion_approval_id
            )
            if (
                approval is None
                or approval.approval_signature != promotion_approval_signature(approval)
                or approval.approval_signature
                != activation.promotion_approval_signature
                or approval.candidate_id != activation.candidate_id
                or approval.shadow_enrollment_id != activation.shadow_enrollment_id
                or approval.user_id != activation.user_id
                or approval.exchange != activation.exchange
                or approval.quote_asset != activation.quote_asset
                or approval.scenario_name != activation.scenario_name
                or approval.scenario_definition_signature
                != activation.scenario_definition_signature
                or canonicalize(approval.component_weights)
                != canonicalize(activation.component_weights)
                or approval.baseline_policy_signature
                != activation.baseline_policy_signature
                or approval.effective_top_n != activation.effective_top_n
            ):
                raise FullLivePolicyIntegrityError(
                    "Full LIVE promotion approval anchor is invalid"
                )
            termination = self.session.scalar(
                select(FullLivePolicyTerminationEvent)
                .where(FullLivePolicyTerminationEvent.activation_id == activation.id)
                .execution_options(autoflush=False)
            )
            if termination is None:
                active.append((activation, ranking_policy, approval))
            else:
                validate_termination(termination, activation)
        if len(active) > 1:
            raise FullLivePolicyIntegrityError(
                "multiple active Full LIVE policies exist for one context"
            )
        if not active:
            return LiveRankingPolicyResolution(
                user_id=normalized_user_id,
                exchange=normalized_exchange,
                quote_asset=normalized_quote,
                mode=BASELINE,
                ranking_policy=baseline_policy,
                baseline_policy_signature=baseline_signature,
                effective_policy_signature=baseline_signature,
            )
        activation, ranking_policy, approval = active[0]
        if activation.baseline_policy_signature != baseline_signature:
            raise FullLivePolicyIntegrityError(
                "active Full LIVE baseline no longer matches runtime settings"
            )
        if activation.effective_top_n != self.settings.market_universe_top_n:
            raise FullLivePolicyIntegrityError(
                "active Full LIVE TopN no longer matches runtime settings"
            )
        return LiveRankingPolicyResolution(
            user_id=normalized_user_id,
            exchange=normalized_exchange,
            quote_asset=normalized_quote,
            mode=FULL_LIVE,
            ranking_policy=ranking_policy,
            baseline_policy_signature=baseline_signature,
            effective_policy_signature=activation.effective_policy_signature,
            full_live_activation_id=activation.id,
            promotion_approval_id=approval.id,
            scenario_name=activation.scenario_name,
            activated_at=activation.activated_at,
            ranking_runtime_changed=True,
        )


__all__ = [
    "BASELINE",
    "FULL_LIVE",
    "LiveRankingPolicyLease",
    "LiveRankingPolicyResolution",
    "LiveRankingPolicyResolver",
]
