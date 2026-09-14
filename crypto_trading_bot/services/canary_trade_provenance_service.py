from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from crypto_trading_bot.config.settings import Settings, get_settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    LivePolicyCanaryRun,
    MarketUniverseCandidate,
    TradeRecommendation,
)
from crypto_trading_bot.services.live_policy_canary_service import (
    LivePolicyCanaryError,
    load_and_validate_live_policy_canary_run,
)


BASELINE_RECOMMENDATION = "BASELINE_RECOMMENDATION"
CANARY_RECOMMENDATION = "CANARY_RECOMMENDATION"
INVALID_CANARY_PROVENANCE = "INVALID_CANARY_PROVENANCE"


@dataclass(frozen=True)
class CanaryTradeProvenance:
    mode: str
    recommendation_id: int
    universe_candidate: MarketUniverseCandidate | None
    market_universe_analysis_run: AnalysisRun | None
    canary_run: LivePolicyCanaryRun | None
    activation: object | None
    safety_binding: object | None
    promotion_approval: object | None
    activation_signature: str | None
    safety_binding_signature: str | None
    per_order_buy_cap: Decimal | None
    daily_buy_cap: Decimal | None
    valid: bool
    safe_reason: str | None


class CanaryTradeProvenanceService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def resolve(self, recommendation: TradeRecommendation) -> CanaryTradeProvenance:
        candidate = (
            self.session.get(
                MarketUniverseCandidate, recommendation.universe_candidate_id
            )
            if recommendation.universe_candidate_id is not None
            else None
        )
        if candidate is None:
            return self._baseline(recommendation, candidate=None, market_run=None)
        market_run = self.session.get(AnalysisRun, candidate.analysis_run_id)
        canary_run = self.session.scalar(
            select(LivePolicyCanaryRun)
            .where(LivePolicyCanaryRun.analysis_run_id == candidate.analysis_run_id)
            .execution_options(autoflush=False)
        )
        if canary_run is None:
            return self._baseline(
                recommendation, candidate=candidate, market_run=market_run
            )
        try:
            validated = load_and_validate_live_policy_canary_run(
                self.session, candidate.analysis_run_id, self.settings
            )
            if validated is None:
                raise LivePolicyCanaryError("Canary Run disappeared")
            run, activation, binding, approval = validated
            recommendation_run = self.session.get(
                AnalysisRun, recommendation.analysis_run_id
            )
            if (
                market_run is None
                or recommendation_run is None
                or candidate.user_id != recommendation.user_id
                or run.user_id != recommendation.user_id
                or activation.user_id != recommendation.user_id
                or candidate.exchange != recommendation.exchange
                or run.exchange != recommendation.exchange
                or activation.exchange != recommendation.exchange
                or candidate.market != recommendation.market
                or candidate.analysis_run_id != run.analysis_run_id
                or market_run.pipeline_run_id is None
                or market_run.pipeline_run_id != run.pipeline_run_id
                or recommendation_run.pipeline_run_id != market_run.pipeline_run_id
                or recommendation_run.user_id != recommendation.user_id
                or recommendation_run.run_type != "AI_RECOMMENDATION"
                or market_run.user_id != recommendation.user_id
                or market_run.run_type != "MARKET_UNIVERSE"
            ):
                raise LivePolicyCanaryError(
                    "Recommendation Canary lineage identity is invalid"
                )
            return CanaryTradeProvenance(
                mode=CANARY_RECOMMENDATION,
                recommendation_id=recommendation.id,
                universe_candidate=candidate,
                market_universe_analysis_run=market_run,
                canary_run=run,
                activation=activation,
                safety_binding=binding,
                promotion_approval=approval,
                activation_signature=activation.activation_signature,
                safety_binding_signature=binding.binding_signature,
                per_order_buy_cap=Decimal(binding.max_buy_order_amount_krw),
                daily_buy_cap=Decimal(binding.daily_max_buy_amount_krw),
                valid=True,
                safe_reason=None,
            )
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            return CanaryTradeProvenance(
                mode=INVALID_CANARY_PROVENANCE,
                recommendation_id=recommendation.id,
                universe_candidate=candidate,
                market_universe_analysis_run=market_run,
                canary_run=canary_run,
                activation=None,
                safety_binding=None,
                promotion_approval=None,
                activation_signature=None,
                safety_binding_signature=None,
                per_order_buy_cap=None,
                daily_buy_cap=None,
                valid=False,
                safe_reason=str(error),
            )

    @staticmethod
    def _baseline(recommendation, *, candidate, market_run):
        return CanaryTradeProvenance(
            mode=BASELINE_RECOMMENDATION,
            recommendation_id=recommendation.id,
            universe_candidate=candidate,
            market_universe_analysis_run=market_run,
            canary_run=None,
            activation=None,
            safety_binding=None,
            promotion_approval=None,
            activation_signature=None,
            safety_binding_signature=None,
            per_order_buy_cap=None,
            daily_buy_cap=None,
            valid=True,
            safe_reason=None,
        )
