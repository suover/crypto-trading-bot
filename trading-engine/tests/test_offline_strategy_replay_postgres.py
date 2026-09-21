from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.offline_strategy_replay_service import (
    OfflineStrategyReplayService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)


def test_postgresql_replay_decodes_jsonb_numeric_and_leaves_rows_unchanged() -> None:
    with SessionLocal() as session:
        user = User(name=f"offline-replay-{uuid4()}")
        session.add(user)
        session.flush()
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
            database_url="postgresql://test:test@localhost/test",
            market_universe_mode="DYNAMIC",
            market_universe_top_n=1,
            market_universe_prefilter_n=2,
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
            research_candidate_count=2,
            prefilter_candidate_count=2,
            ranked_candidate_count=2,
            final_candidate_count=1,
            captured_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        features = [
            {
                "market": "KRW-BTC",
                "quote_trade_value_24h": "1000.1234",
                "timeframes": {
                    "15m": {
                        "data_quality": "SUFFICIENT",
                        "trend_label": "관망",
                        "recent_change_rate": "-10",
                        "volume_ratio": "100",
                        "realized_volatility": "10",
                        "max_drawdown": "15",
                    }
                },
                "orderbook": {"spread_rate": "0.005"},
                "enough_candles": True,
            },
            {
                "market": "KRW-XRP",
                "quote_trade_value_24h": "500.5678",
                "timeframes": {
                    "15m": {
                        "data_quality": "SUFFICIENT",
                        "trend_label": "관망",
                        "recent_change_rate": "10",
                        "volume_ratio": "100",
                        "realized_volatility": "10",
                        "max_drawdown": "15",
                    }
                },
                "orderbook": {"spread_rate": "0.005"},
                "enough_candles": True,
            },
        ]
        ranked = policy.rank(features)
        ranking = {
            candidate["market"]: (rank, candidate["score"])
            for rank, candidate in enumerate(ranked, start=1)
        }
        for prefilter_rank, feature in enumerate(features, start=1):
            original_rank, original_score = ranking[feature["market"]]
            session.add(
                StrategyReplayCandidate(
                    strategy_replay_snapshot_id=snapshot.id,
                    analysis_run_id=run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=feature["market"],
                    base_asset=feature["market"].split("-")[1],
                    quote_asset="KRW",
                    in_prefilter=True,
                    prefilter_rank=prefilter_rank,
                    held=False,
                    buy_eligible=True,
                    sell_eligible=False,
                    trading_supported=True,
                    original_rank=original_rank,
                    original_score=original_score,
                    final_selected=original_rank == 1,
                    final_rank=1 if original_rank == 1 else None,
                    selection_source="RANKED" if original_rank == 1 else None,
                    quote_trade_value_24h=Decimal(feature["quote_trade_value_24h"]),
                    feature_data=feature,
                )
            )
        session.flush()
        before_snapshot_count = session.scalar(
            select(func.count()).select_from(StrategyReplaySnapshot)
        )
        before_candidate_count = session.scalar(
            select(func.count()).select_from(StrategyReplayCandidate)
        )

        result = OfflineStrategyReplayService(session).replay_snapshot(snapshot.id)

        assert result.status == "SUCCESS"
        assert result.baseline_matches_stored is True
        assert all(
            isinstance(candidate.baseline_replay_score, Decimal)
            for candidate in result.candidate_results
        )
        assert (
            session.scalar(select(func.count()).select_from(StrategyReplaySnapshot))
            == before_snapshot_count
        )
        assert (
            session.scalar(select(func.count()).select_from(StrategyReplayCandidate))
            == before_candidate_count
        )
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()
