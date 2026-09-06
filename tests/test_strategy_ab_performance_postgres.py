from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    OUTCOME_INCOMPLETE,
    SUCCESS,
    StrategyABPerformanceService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


def feature(market: str, liquidity: str, momentum: str) -> dict:
    return {
        "market": market,
        "quote_trade_value_24h": liquidity,
        "timeframes": {
            "15m": {
                "data_quality": "SUFFICIENT",
                "trend_label": "관망",
                "recent_change_rate": momentum,
                "volume_ratio": "100",
                "realized_volatility": "10",
                "max_drawdown": "15",
            }
        },
        "orderbook": {"spread_rate": "0.005"},
        "enough_candles": True,
    }


def create_snapshot(session, user, *, captured_at, with_outcomes):
    run = AnalysisRun(
        user_id=user.id,
        pipeline_run_id=str(uuid4()),
        run_type="MARKET_UNIVERSE",
        trading_mode="AI_APPROVAL",
        status="SUCCESS",
    )
    session.add(run)
    session.flush()
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=2,
        market_universe_prefilter_n=3,
    )
    policy = HeuristicMarketRankingPolicy()
    policy_data = build_policy_data(settings, policy)
    snapshot = StrategyReplaySnapshot(
        analysis_run_id=run.id,
        pipeline_run_id=run.pipeline_run_id,
        user_id=user.id,
        exchange="UPBIT",
        quote_asset="KRW",
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        policy_signature=policy_signature(policy_data),
        policy_data=policy_data,
        research_candidate_count=3,
        prefilter_candidate_count=3,
        ranked_candidate_count=3,
        final_candidate_count=2,
        captured_at=captured_at,
    )
    session.add(snapshot)
    session.flush()
    features = [
        feature("KRW-A", "1000", "-10"),
        feature("KRW-B", "800", "0"),
        feature("KRW-C", "600", "10"),
    ]
    ranked = policy.rank(features)
    ranks = {
        item["market"]: (
            rank,
            item["score"].quantize(Decimal("0.000000001")),
        )
        for rank, item in enumerate(ranked, start=1)
    }
    returns = {"KRW-A": Decimal("10"), "KRW-B": Decimal("-5"), "KRW-C": Decimal("0")}
    for prefilter_rank, item in enumerate(features, start=1):
        original_rank, original_score = ranks[item["market"]]
        candidate = StrategyReplayCandidate(
            strategy_replay_snapshot_id=snapshot.id,
            analysis_run_id=run.id,
            user_id=user.id,
            exchange="UPBIT",
            market=item["market"],
            base_asset=item["market"].split("-")[1],
            quote_asset="KRW",
            in_prefilter=True,
            prefilter_rank=prefilter_rank,
            held=False,
            buy_eligible=True,
            sell_eligible=False,
            trading_supported=True,
            original_rank=original_rank,
            original_score=original_score,
            final_selected=original_rank <= 2,
            final_rank=original_rank if original_rank <= 2 else None,
            selection_source="RANKED" if original_rank <= 2 else None,
            quote_trade_value_24h=Decimal(item["quote_trade_value_24h"]),
            feature_data=item,
        )
        session.add(candidate)
        session.flush()
        if with_outcomes:
            session.add(
                StrategyReplayCandidateOutcome(
                    strategy_replay_candidate_id=candidate.id,
                    strategy_replay_snapshot_id=snapshot.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=candidate.market,
                    horizon_minutes=60,
                    snapshot_at=captured_at,
                    reference_at=captured_at,
                    target_at=captured_at + timedelta(minutes=60),
                    evaluated_at=captured_at + timedelta(minutes=61),
                    reference_price=Decimal("100"),
                    reference_price_source=(
                        "STRATEGY_REPLAY_CANDIDATE_FEATURE_LATEST_PRICE"
                    ),
                    end_price=Decimal("100") + returns[candidate.market],
                    end_price_at=captured_at + timedelta(minutes=59),
                    end_price_source="UPBIT_MINUTE_CANDLE_1M_CLOSE",
                    market_return_percentage=returns[candidate.market],
                    evaluation_status="COMPLETE",
                    safe_reason=None,
                )
            )
    session.flush()
    return snapshot


def test_postgresql_single_and_batch_are_read_only_and_use_stored_outcomes() -> None:
    with SessionLocal() as session:
        user = User(name=f"strategy-ab-{uuid4()}")
        session.add(user)
        session.flush()
        older = create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 1, 10, 13, tzinfo=UTC),
            with_outcomes=True,
        )
        newer = create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 1, 14, 47, tzinfo=UTC),
            with_outcomes=False,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        service = StrategyABPerformanceService(session)
        single = service.evaluate_snapshot(older.id, horizon_minutes=60)
        assert single.status == SUCCESS
        assert single.performance_evaluated is True
        assert single.baseline_top_markets == single.scenario_top_markets
        assert single.mean_return_delta == 0
        assert single.scenario_result == "TIE"

        arbitrary_missing = service.evaluate_snapshot(older.id, horizon_minutes=30)
        assert arbitrary_missing.status == OUTCOME_INCOMPLETE
        assert arbitrary_missing.performance_evaluated is False

        batch = service.evaluate_latest(2, horizon_minutes=60)
        assert [result.snapshot_id for result in batch.results] == [newer.id, older.id]
        assert batch.evaluated_snapshot_count == 2
        assert batch.successful_snapshot_count == 1
        assert batch.outcome_incomplete_count == 1
        assert batch.tie_count == 1
        assert batch.scenario_win_rate == 0

        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in before
        }
        assert after == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()
