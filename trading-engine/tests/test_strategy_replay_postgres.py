from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    MarketUniverseCandidate,
    StrategyReplayCandidate,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.ai_trade_recommendation_service import (
    AiTradeRecommendationService,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    StrategyReplayDatasetService,
)


def test_research_candidates_do_not_enter_ai_persisted_candidate_set() -> None:
    with SessionLocal() as session:
        user = User(name=f"strategy-replay-{uuid4()}")
        session.add(user)
        session.flush()
        pipeline_run_id = str(uuid4())
        run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=pipeline_run_id,
            run_type="MARKET_UNIVERSE",
            trading_mode="AI_APPROVAL",
            status="SUCCESS",
        )
        session.add(run)
        session.flush()
        final_candidate = MarketUniverseCandidate(
            analysis_run_id=run.id,
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
            feature_data={"market": "KRW-BTC"},
        )
        session.add(final_candidate)
        snapshot = StrategyReplaySnapshot(
            analysis_run_id=run.id,
            pipeline_run_id=pipeline_run_id,
            user_id=user.id,
            exchange="UPBIT",
            quote_asset="KRW",
            dataset_schema_version="strategy-replay-dataset-v1",
            policy_signature="strategy-replay-v1:test",
            policy_data={},
            research_candidate_count=2,
            prefilter_candidate_count=2,
            ranked_candidate_count=2,
            final_candidate_count=1,
            captured_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        for market, selected in (("KRW-BTC", True), ("KRW-ETH", False)):
            session.add(
                StrategyReplayCandidate(
                    strategy_replay_snapshot_id=snapshot.id,
                    analysis_run_id=run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=market,
                    base_asset=market.split("-")[1],
                    quote_asset="KRW",
                    in_prefilter=True,
                    prefilter_rank=1 if selected else 2,
                    held=False,
                    buy_eligible=True,
                    sell_eligible=False,
                    trading_supported=True,
                    original_rank=1 if selected else 2,
                    original_score=Decimal("0.9" if selected else "0.8"),
                    final_selected=selected,
                    final_rank=1 if selected else None,
                    selection_source="RANKED" if selected else None,
                    quote_trade_value_24h=Decimal("1000"),
                    feature_data={"market": market},
                )
            )
        session.flush()

        service = AiTradeRecommendationService(
            session,
            trade_advisor=MagicMock(),
            registry_service=MagicMock(),
            market_data_context_service=MagicMock(),
        )
        candidates = service._get_persisted_candidates(user.id, pipeline_run_id)
        assert [candidate.market for candidate in candidates] == ["KRW-BTC"]
        session.rollback()


def test_capture_persists_jsonb_lineage_and_full_ranking_on_postgresql() -> None:
    with SessionLocal() as session:
        user = User(name=f"strategy-replay-capture-{uuid4()}")
        session.add(user)
        session.flush()
        run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id=str(uuid4()),
            run_type="MARKET_UNIVERSE",
            trading_mode="AI_APPROVAL",
            status="STARTED",
        )
        session.add(run)
        session.flush()
        btc = {
            "exchange": "UPBIT",
            "market": "KRW-BTC",
            "base_asset": "BTC",
            "quote_asset": "KRW",
            "latest_price": "100",
            "quote_trade_value_24h": "1000",
            "liquidity": {"quote_trade_value_24h": "1000"},
            "market_event": {"warning": False},
            "timeframes": {"15m": {"data_quality": "SUFFICIENT"}},
            "orderbook": {"spread_rate": "0.001"},
            "data_quality": "SUFFICIENT",
            "enough_candles": True,
            "held": False,
            "buy_eligible": True,
            "sell_eligible": False,
            "trading_supported": True,
        }
        eth = {**btc, "market": "KRW-ETH", "base_asset": "ETH"}
        ranked = [
            {**btc, "score": Decimal("0.9")},
            {**eth, "score": Decimal("0.8")},
        ]
        final = [{**ranked[0], "rank": 1, "selection_source": "RANKED"}]
        capture = StrategyReplayDatasetService(session).capture(
            analysis_run=run,
            user=user,
            exchange="UPBIT",
            quote_asset="KRW",
            settings=Settings(
                database_url="postgresql://test:test@localhost/test",
                market_universe_mode="DYNAMIC",
                market_universe_top_n=1,
                market_universe_prefilter_n=2,
            ),
            ranking_policy=HeuristicMarketRankingPolicy(),
            research_candidates=[btc, eth],
            liquidity_prefilter=[btc, eth],
            ranked_candidates=ranked,
            final_candidates=final,
        )
        assert capture.snapshot.pipeline_run_id == run.pipeline_run_id
        assert capture.snapshot.policy_data["ranking"]["weights"]["liquidity"] == "0.35"
        persisted = {
            row.market: row
            for row in session.query(StrategyReplayCandidate).filter_by(
                strategy_replay_snapshot_id=capture.snapshot.id
            )
        }
        assert persisted["KRW-BTC"].original_rank == 1
        assert persisted["KRW-BTC"].final_selected is True
        assert persisted["KRW-ETH"].original_rank == 2
        assert persisted["KRW-ETH"].final_selected is False
        assert (
            persisted["KRW-ETH"].feature_data["timeframes"]["15m"]["data_quality"]
            == "SUFFICIENT"
        )
        session.rollback()
