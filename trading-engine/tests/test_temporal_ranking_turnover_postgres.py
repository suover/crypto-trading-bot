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
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
)
from crypto_trading_bot.services.temporal_ranking_turnover_service import (
    SUCCESS,
    TemporalRankingTurnoverService,
)


def _definitions():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "baseline_clone",
                    "component_weights": {
                        "liquidity": "0.35",
                        "trend_alignment": "0.20",
                        "momentum": "0.15",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
                {
                    "name": "momentum_heavy",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                },
            ],
        }
    )


def _feature(market: str, liquidity: str, momentum: str) -> dict:
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


def _create_snapshot(session, user, *, hour: int, invalid: bool = False):
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
        dataset_schema_version=("unsupported" if invalid else DATASET_SCHEMA_VERSION),
        policy_signature=policy_signature(policy_data),
        policy_data=policy_data,
        research_candidate_count=3,
        prefilter_candidate_count=3,
        ranked_candidate_count=3,
        final_candidate_count=2,
        captured_at=datetime(2040, 1, 1, hour, tzinfo=UTC),
    )
    session.add(snapshot)
    session.flush()
    features = (
        _feature("KRW-A", str(1000 - hour * 80), str(-10 + hour * 4)),
        _feature("KRW-B", "800", "0"),
        _feature("KRW-C", str(600 + hour * 80), str(10 - hour * 4)),
    )
    ranked = policy.rank(list(features))
    ranks = {
        item["market"]: (rank, item["score"].quantize(Decimal("0.000000001")))
        for rank, item in enumerate(ranked, start=1)
    }
    for prefilter_rank, item in enumerate(features, start=1):
        original_rank, original_score = ranks[item["market"]]
        session.add(
            StrategyReplayCandidate(
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
        )
    session.flush()
    return snapshot


def test_postgresql_no_outcomes_end_to_end_is_read_only() -> None:
    with SessionLocal() as session:
        user = User(name=f"temporal-turnover-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = [_create_snapshot(session, user, hour=hour) for hour in range(5)]
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = TemporalRankingTurnoverService(session).evaluate(
            scenarios=_definitions(), latest=5
        )

        assert result.status == SUCCESS
        assert result.replayed_snapshot_count == 5
        assert result.cohorts[0].transition_count == 4
        assert result.cohorts[0].baseline_summary.transition_count == 4
        assert result.cohorts[0].scenario_summaries[0].summary.transition_count == 4
        assert (
            session.scalar(
                select(func.count())
                .select_from(StrategyReplayCandidateOutcome)
                .where(
                    StrategyReplayCandidateOutcome.strategy_replay_snapshot_id.in_(
                        [snapshot.id for snapshot in snapshots]
                    )
                )
            )
            == 0
        )
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in before
        }
        assert after == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()


def test_postgresql_incompatible_middle_snapshot_is_not_bridged() -> None:
    with SessionLocal() as session:
        user = User(name=f"temporal-break-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = [
            _create_snapshot(session, user, hour=hour, invalid=hour == 2)
            for hour in range(5)
        ]

        result = TemporalRankingTurnoverService(session).evaluate(
            scenarios=_definitions(), latest=5
        )

        assert result.status == SUCCESS
        transition_pairs = {
            (
                transition.baseline.previous_snapshot_id,
                transition.baseline.current_snapshot_id,
            )
            for transition in result.cohorts[0].transitions
        }
        assert (snapshots[1].id, snapshots[3].id) not in transition_pairs
        assert transition_pairs == {
            (snapshots[0].id, snapshots[1].id),
            (snapshots[3].id, snapshots[4].id),
        }
        assert result.cohorts[0].continuity_break_count == 2
        session.rollback()
