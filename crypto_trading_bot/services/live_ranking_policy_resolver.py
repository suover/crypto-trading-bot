from dataclasses import dataclass

from sqlalchemy.orm import Session

from crypto_trading_bot.analysis.market_ranking import (
    HeuristicMarketRankingPolicy,
    MarketRankingPolicy,
)
from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    build_policy_data,
    policy_signature,
)

from crypto_trading_bot.services.runtime_user_resolver import validate_user_id

BASELINE = "BASELINE"


@dataclass(frozen=True)
class LiveRankingPolicyResolution:
    """Baseline-only runtime policy selection result."""

    user_id: int
    mode: str
    ranking_policy: MarketRankingPolicy
    baseline_policy_signature: str
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
    """Stable LIVE seam that always resolves to the baseline ranking policy."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        **_: object,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def resolve(self, *, user_id: int) -> LiveRankingPolicyLease:
        return LiveRankingPolicyLease(self.inspect(user_id=user_id))

    def inspect(self, *, user_id: int) -> LiveRankingPolicyResolution:
        normalized_user_id = validate_user_id(user_id)
        ranking_policy = HeuristicMarketRankingPolicy()
        signature = policy_signature(build_policy_data(self.settings, ranking_policy))
        return LiveRankingPolicyResolution(
            user_id=normalized_user_id,
            mode=BASELINE,
            ranking_policy=ranking_policy,
            baseline_policy_signature=signature,
        )


__all__ = [
    "BASELINE",
    "LiveRankingPolicyLease",
    "LiveRankingPolicyResolution",
    "LiveRankingPolicyResolver",
]
