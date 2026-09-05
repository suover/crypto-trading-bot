from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    MarketUniverseCandidate,
    TradeRecommendation,
    TradeRecommendationCandidateOutcome,
    TradeRecommendationOutcome,
    User,
)
from crypto_trading_bot.services.recommendation_outcome_service import (
    RecommendationOutcomeService,
)


class FakeProvider:
    exchange_code = "UPBIT"

    def __init__(self, target: datetime, *, fail: bool = False) -> None:
        self.target = target
        self.fail = fail
        self.calls = 0

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls += 1
        if self.fail:
            raise TimeoutError
        return [
            {
                "candle_date_time_utc": (self.target - timedelta(minutes=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
                "trade_price": "110",
            }
        ]


def test_postgres_exact_pipeline_dry_run_apply_and_complete_idempotency() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    recommendation_at = now - timedelta(hours=2)
    target = recommendation_at + timedelta(hours=1)
    with SessionLocal() as session:
        user = User(name=f"recommendation-outcome-{uuid4()}")
        session.add(user)
        session.flush()
        pipeline_a = str(uuid4())
        universe_a = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_a,
            run_type="MARKET_UNIVERSE",
            trading_mode="AI_APPROVAL",
            status="SUCCESS",
        )
        recommendation_run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_a,
            run_type="AI_RECOMMENDATION",
            trading_mode="AI_APPROVAL",
            status="SUCCESS",
        )
        universe_b = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=str(uuid4()),
            run_type="MARKET_UNIVERSE",
            trading_mode="AI_APPROVAL",
            status="SUCCESS",
        )
        session.add_all((universe_a, recommendation_run, universe_b))
        session.flush()
        candidate_a = MarketUniverseCandidate(
            analysis_run_id=universe_a.id,
            user_id=user.id,
            exchange="UPBIT",
            market="KRW-BTC",
            base_asset="BTC",
            quote_asset="KRW",
            rank=1,
            score=Decimal("0.8"),
            selection_source="RANKED",
            buy_eligible=True,
            sell_eligible=False,
            feature_data={"latest_price": "100", "held": False},
        )
        candidate_b = MarketUniverseCandidate(
            analysis_run_id=universe_b.id,
            user_id=user.id,
            exchange="UPBIT",
            market="KRW-BTC",
            base_asset="BTC",
            quote_asset="KRW",
            rank=1,
            score=Decimal("0.9"),
            selection_source="RANKED",
            buy_eligible=True,
            sell_eligible=False,
            feature_data={"latest_price": "150", "held": False},
        )
        session.add_all((candidate_a, candidate_b))
        session.flush()
        recommendation = TradeRecommendation(
            analysis_run_id=recommendation_run.id,
            universe_candidate_id=candidate_a.id,
            user_id=user.id,
            exchange="UPBIT",
            market="KRW-BTC",
            action="BUY",
            trade_ratio=Decimal("0.1"),
            confidence=Decimal("0.7"),
            status="CREATED",
            created_at=recommendation_at,
        )
        session.add(recommendation)
        not_due = TradeRecommendation(
            analysis_run_id=recommendation_run.id,
            universe_candidate_id=candidate_a.id,
            user_id=user.id,
            exchange="UPBIT",
            market="KRW-BTC",
            action="HOLD",
            trade_ratio=Decimal("0"),
            confidence=Decimal("0.5"),
            status="CREATED",
            created_at=now - timedelta(minutes=59),
        )
        session.add(not_due)
        session.flush()

        dry_provider = FakeProvider(target)
        dry_result = RecommendationOutcomeService(
            session, market_data_provider=dry_provider, now_fn=lambda: now
        ).evaluate_due(horizons=(60,), batch_size=10, user_id=user.id, apply=False)
        assert dry_result.selected_complete_count == 1
        assert dry_result.candidate_complete_count == 1
        assert dry_provider.calls == 1
        assert (
            session.scalar(select(func.count()).select_from(TradeRecommendationOutcome))
            == 0
        )
        assert dry_result.recommendation_count == 1

        failed_provider = FakeProvider(target, fail=True)
        RecommendationOutcomeService(
            session, market_data_provider=failed_provider, now_fn=lambda: now
        ).evaluate_due(horizons=(60,), batch_size=10, user_id=user.id, apply=True)
        partial = session.scalar(
            select(TradeRecommendationOutcome).where(
                TradeRecommendationOutcome.recommendation_id == recommendation.id
            )
        )
        assert partial.evaluation_status == "PARTIAL"
        assert partial.safe_reason == "HISTORICAL_PRICE_UNAVAILABLE"

        apply_provider = FakeProvider(target)
        RecommendationOutcomeService(
            session, market_data_provider=apply_provider, now_fn=lambda: now
        ).evaluate_due(horizons=(60,), batch_size=10, user_id=user.id, apply=True)
        selected = session.scalar(
            select(TradeRecommendationOutcome).where(
                TradeRecommendationOutcome.recommendation_id == recommendation.id
            )
        )
        candidate_outcomes = tuple(
            session.scalars(
                select(TradeRecommendationCandidateOutcome).where(
                    TradeRecommendationCandidateOutcome.recommendation_id
                    == recommendation.id
                )
            )
        )
        assert selected.market_return_percentage == Decimal("10")
        assert [row.universe_candidate_id for row in candidate_outcomes] == [
            candidate_a.id
        ]

        no_call_provider = FakeProvider(target)
        second = RecommendationOutcomeService(
            session, market_data_provider=no_call_provider, now_fn=lambda: now
        ).evaluate_due(horizons=(60,), batch_size=10, user_id=user.id, apply=True)
        assert second.recommendation_count == 0
        assert no_call_provider.calls == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(TradeRecommendationOutcome)
                .where(TradeRecommendationOutcome.recommendation_id == not_due.id)
            )
            == 0
        )
        session.rollback()
