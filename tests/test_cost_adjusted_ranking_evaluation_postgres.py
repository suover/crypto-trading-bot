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
from crypto_trading_bot.services.cost_adjusted_ranking_evaluation_service import (
    SUCCESS,
    CostAdjustedRankingEvaluationService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    parse_scenario_document,
)
from crypto_trading_bot.services.strategy_replay_dataset_service import (
    DATASET_SCHEMA_VERSION,
    build_policy_data,
    policy_signature,
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


def _create_snapshot(
    session,
    user,
    *,
    captured_at: datetime,
    sequence: int,
    invalid: bool = False,
    top_n: int = 2,
    features: tuple[dict, ...] | None = None,
):
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
        market_universe_top_n=top_n,
        market_universe_prefilter_n=len(features) if features is not None else 3,
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
        research_candidate_count=len(features) if features is not None else 3,
        prefilter_candidate_count=len(features) if features is not None else 3,
        ranked_candidate_count=len(features) if features is not None else 3,
        final_candidate_count=top_n,
        captured_at=captured_at,
    )
    session.add(snapshot)
    session.flush()
    if features is None:
        selected_features = (
            _feature("KRW-A", str(1100 - sequence * 150), str(-12 + sequence * 5)),
            _feature("KRW-B", "800", "0"),
            _feature("KRW-C", str(500 + sequence * 150), str(12 - sequence * 5)),
        )
        returns = {
            "KRW-A": Decimal("1.5") + Decimal(sequence) / Decimal("10"),
            "KRW-B": Decimal("0.5"),
            "KRW-C": Decimal("-0.5") + Decimal(sequence) / Decimal("10"),
        }
    else:
        selected_features = features
        returns = {
            item["market"]: Decimal(index + 1) / Decimal("10")
            + Decimal(sequence) / Decimal("100")
            for index, item in enumerate(selected_features)
        }
    ranked = policy.rank(list(selected_features))
    ranks = {
        item["market"]: (rank, item["score"].quantize(Decimal("0.000000001")))
        for rank, item in enumerate(ranked, start=1)
    }
    for prefilter_rank, item in enumerate(selected_features, start=1):
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
            final_selected=original_rank <= top_n,
            final_rank=original_rank if original_rank <= top_n else None,
            selection_source="RANKED" if original_rank <= top_n else None,
            quote_trade_value_24h=Decimal(item["quote_trade_value_24h"]),
            feature_data=item,
        )
        session.add(candidate)
        session.flush()
        for horizon in (60, 240):
            market_return = returns[candidate.market] + Decimal(horizon) / Decimal(
                "1000"
            )
            session.add(
                StrategyReplayCandidateOutcome(
                    strategy_replay_candidate_id=candidate.id,
                    strategy_replay_snapshot_id=snapshot.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market=candidate.market,
                    horizon_minutes=horizon,
                    snapshot_at=captured_at,
                    reference_at=captured_at,
                    target_at=captured_at + timedelta(minutes=horizon),
                    evaluated_at=captured_at + timedelta(minutes=horizon + 1),
                    reference_price=Decimal("100"),
                    reference_price_source=(
                        "STRATEGY_REPLAY_CANDIDATE_FEATURE_LATEST_PRICE"
                    ),
                    end_price=Decimal("100") + market_return,
                    end_price_at=captured_at + timedelta(minutes=horizon - 1),
                    end_price_source="UPBIT_MINUTE_CANDLE_1M_CLOSE",
                    market_return_percentage=market_return,
                    evaluation_status="COMPLETE",
                    safe_reason=None,
                )
            )
    session.flush()
    return snapshot


def _counts(session):
    return {
        model: session.scalar(select(func.count()).select_from(model))
        for model in (
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
    }


def test_postgresql_cost_adjusted_pipeline_excludes_first_snapshot_and_is_read_only():
    with SessionLocal() as session:
        user = User(name=f"cost-adjusted-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = [
            _create_snapshot(
                session,
                user,
                captured_at=datetime(2050, 1, 1, hour, tzinfo=UTC),
                sequence=hour,
            )
            for hour in range(5)
        ]
        before = _counts(session)

        result = CostAdjustedRankingEvaluationService(session).evaluate(
            scenarios=_definitions(),
            horizons=(60, 240),
            latest=5,
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert result.status == SUCCESS
        assert result.horizon_count == 2
        assert all(cohort.status == SUCCESS for cohort in result.cohorts)
        assert all(cohort.turnover_transition_count == 4 for cohort in result.cohorts)
        assert all(
            cohort.cost_adjustable_snapshot_count == 4 for cohort in result.cohorts
        )
        assert all(
            snapshots[0].id
            not in {
                item.snapshot_id
                for scenario in cohort.scenario_results
                for item in scenario.snapshots
            }
            for cohort in result.cohorts
        )
        assert _counts(session) == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()


def test_postgresql_cost_adjustment_does_not_bridge_incompatible_middle_snapshot():
    with SessionLocal() as session:
        user = User(name=f"cost-adjusted-break-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = [
            _create_snapshot(
                session,
                user,
                captured_at=datetime(2051, 1, 1, hour, tzinfo=UTC),
                sequence=hour,
                invalid=hour == 2,
            )
            for hour in range(5)
        ]
        before = _counts(session)

        result = CostAdjustedRankingEvaluationService(session).evaluate(
            scenarios=_definitions(),
            horizons=(60,),
            latest=5,
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert result.status == SUCCESS
        cohort = next(cohort for cohort in result.cohorts if cohort.status == SUCCESS)
        evaluated_ids = {
            item.snapshot_id
            for scenario in cohort.scenario_results
            for item in scenario.snapshots
        }
        assert evaluated_ids == {snapshots[1].id, snapshots[4].id}
        assert snapshots[3].id not in evaluated_ids
        assert cohort.turnover_transition_count == 2
        assert cohort.cost_adjustable_snapshot_count == 2
        assert _counts(session) == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()


def test_postgresql_top_seven_two_replacements_succeeds_end_to_end():
    with SessionLocal() as session:
        user = User(name=f"cost-adjusted-top-seven-{uuid4()}")
        session.add(user)
        session.flush()
        markets = tuple(f"KRW-{symbol}" for symbol in "ABCDEFGHI")
        first_features = tuple(
            _feature(market, str(900 - index * 50), "0")
            for index, market in enumerate(markets)
        )
        second_order = (*markets[2:], *markets[:2])
        second_liquidity = {
            market: str(900 - index * 50) for index, market in enumerate(second_order)
        }
        second_features = tuple(
            _feature(market, second_liquidity[market], "0") for market in markets
        )
        _create_snapshot(
            session,
            user,
            captured_at=datetime(2052, 1, 1, 0, tzinfo=UTC),
            sequence=0,
            top_n=7,
            features=first_features,
        )
        _create_snapshot(
            session,
            user,
            captured_at=datetime(2052, 1, 1, 1, tzinfo=UTC),
            sequence=1,
            top_n=7,
            features=second_features,
        )
        before = _counts(session)

        result = CostAdjustedRankingEvaluationService(session).evaluate(
            scenarios=_definitions(),
            horizons=(60,),
            latest=2,
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert result.status == SUCCESS
        cohort = next(cohort for cohort in result.cohorts if cohort.status == SUCCESS)
        assert cohort.effective_top_n == 7
        assert cohort.cost_adjustable_snapshot_count == 1
        for scenario in cohort.scenario_results:
            snapshot = scenario.snapshots[0]
            assert snapshot.baseline_replacement_rate == Decimal("2") / Decimal("7")
            assert (
                snapshot.baseline_sell_notional_ratio
                == snapshot.baseline_replacement_rate
            )
            assert (
                snapshot.baseline_buy_notional_ratio
                == snapshot.baseline_replacement_rate
            )
            assert snapshot.baseline_gross_traded_notional_ratio == (
                Decimal("2") * snapshot.baseline_replacement_rate
            )
        assert _counts(session) == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()
