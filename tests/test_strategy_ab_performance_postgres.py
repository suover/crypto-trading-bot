from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchPolicyCandidate,
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    FORWARD_OUTCOMES_PENDING,
    SUCCESS as FORWARD_SUCCESS,
    ForwardCandidateGrossEvidenceService,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.strategy_ab_performance_service import (
    OUTCOME_INCOMPLETE,
    SUCCESS,
    StrategyABPerformanceService,
)
from crypto_trading_bot.services.ranking_holdout_validation_service import (
    RankingHoldoutValidationService,
)
from crypto_trading_bot.services.ranking_scenario_sweep_service import (
    RankingScenarioSweepService,
    parse_scenario_document,
)
from crypto_trading_bot.services.ranking_validation_robustness_service import (
    RankingValidationRobustnessService,
)
from crypto_trading_bot.services.ranking_walk_forward_validation_service import (
    RankingWalkForwardValidationService,
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


def create_snapshot(
    session,
    user,
    *,
    captured_at,
    with_outcomes,
    top_n=2,
    exclude_cautions=True,
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
        market_universe_prefilter_n=3,
        market_universe_exclude_cautions=exclude_cautions,
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
        final_candidate_count=top_n,
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
            final_selected=original_rank <= top_n,
            final_rank=original_rank if original_rank <= top_n else None,
            selection_source="RANKED" if original_rank <= top_n else None,
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


def test_postgresql_sweep_aligns_common_sets_splits_cohorts_and_stays_read_only() -> (
    None
):
    definitions = parse_scenario_document(
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
                    "name": "research_scenario",
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
    with SessionLocal() as session:
        user = User(name=f"ranking-sweep-{uuid4()}")
        session.add(user)
        session.flush()
        create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
            with_outcomes=True,
        )
        create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 2, 11, 0, tzinfo=UTC),
            with_outcomes=False,
        )
        create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
            with_outcomes=True,
            top_n=1,
        )
        create_snapshot(
            session,
            user,
            captured_at=datetime(2026, 9, 2, 13, 0, tzinfo=UTC),
            with_outcomes=True,
            exclude_cautions=False,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = RankingScenarioSweepService(session).evaluate(
            scenarios=definitions,
            horizons=(60,),
            latest=4,
        )

        assert result.evaluated_snapshot_count == 4
        assert result.cohort_count == 3
        top_two = [cohort for cohort in result.cohorts if cohort.effective_top_n == 2]
        top_one = [cohort for cohort in result.cohorts if cohort.effective_top_n == 1]
        assert sorted(cohort.candidate_snapshot_count for cohort in top_two) == [1, 2]
        partial_cohort = next(
            cohort for cohort in top_two if cohort.candidate_snapshot_count == 2
        )
        assert partial_cohort.common_comparable_snapshot_count == 1
        assert partial_cohort.common_coverage_rate == Decimal("0.5")
        assert partial_cohort.scenario_results[0].tie_count == 1
        assert partial_cohort.scenario_results[0].mean_return_delta == 0
        assert len({cohort.baseline_policy_signature for cohort in top_two}) == 2
        assert top_one[0].candidate_snapshot_count == 1
        assert top_one[0].common_comparable_snapshot_count == 1
        assert all(
            comparison.raw_outcome_incomplete_count == 1
            for comparison in partial_cohort.scenario_results
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


def test_postgresql_holdout_runs_replay_common_split_and_summaries_read_only() -> None:
    definitions = parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "research_holdout_scenario",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                }
            ],
        }
    )
    with SessionLocal() as session:
        user = User(name=f"ranking-holdout-{uuid4()}")
        session.add(user)
        session.flush()
        for hour in range(4):
            create_snapshot(
                session,
                user,
                captured_at=datetime(2030, 1, 1, hour, tzinfo=UTC),
                with_outcomes=True,
            )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = RankingHoldoutValidationService(session).evaluate(
            scenarios=definitions,
            horizons=(60,),
            latest=4,
            holdout_ratio=Decimal("0.5"),
        )

        assert result.evaluated_snapshot_count == 4
        assert result.cohort_count == 1
        cohort = result.cohorts[0]
        assert cohort.status == SUCCESS
        assert cohort.common_comparable_snapshot_count == 4
        assert (cohort.research_snapshot_count, cohort.holdout_snapshot_count) == (2, 2)
        assert cohort.research_end_at < cohort.holdout_start_at
        comparison = cohort.scenario_results[0]
        assert comparison.research.snapshot_count == 2
        assert comparison.holdout.snapshot_count == 2

        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in before
        }
        assert after == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()


def test_postgresql_walk_forward_builds_expanding_folds_and_stays_read_only() -> None:
    definitions = parse_scenario_document(
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
                    "name": "walk_forward_research",
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
    with SessionLocal() as session:
        user = User(name=f"ranking-walk-forward-{uuid4()}")
        session.add(user)
        session.flush()
        for hour in range(6):
            create_snapshot(
                session,
                user,
                captured_at=datetime(2031, 1, 1, hour, tzinfo=UTC),
                with_outcomes=True,
            )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = RankingWalkForwardValidationService(session).evaluate(
            scenarios=definitions,
            horizons=(60,),
            latest=6,
            initial_research_size=2,
            validation_size=2,
        )

        assert result.evaluated_snapshot_count == 6
        assert result.cohort_count == 1
        cohort = result.cohorts[0]
        assert cohort.status == SUCCESS
        assert cohort.common_comparable_snapshot_count == 6
        assert cohort.fold_count == 2
        first, second = cohort.folds
        assert first.research_snapshot_count == 2
        assert first.validation_snapshot_count == 2
        assert second.research_snapshot_count == 4
        assert second.validation_snapshot_count == 2
        assert set(first.validation_snapshot_ids).isdisjoint(
            second.validation_snapshot_ids
        )
        assert set(first.validation_snapshot_ids) <= set(second.research_snapshot_ids)
        assert all(
            item.research.snapshot_count == fold.research_snapshot_count
            and item.validation.snapshot_count == fold.validation_snapshot_count
            for fold in cohort.folds
            for item in fold.scenario_results
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


def test_postgresql_robustness_uses_walk_forward_validation_and_stays_read_only() -> (
    None
):
    definitions = parse_scenario_document(
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
                    "name": "robustness_research",
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
    with SessionLocal() as session:
        user = User(name=f"ranking-robustness-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = tuple(
            create_snapshot(
                session,
                user,
                captured_at=datetime(2032, 1, 1, hour, tzinfo=UTC),
                with_outcomes=True,
            )
            for hour in range(6)
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = RankingValidationRobustnessService(session).evaluate(
            scenarios=definitions,
            horizons=(60,),
            latest=6,
            initial_research_size=2,
            validation_size=2,
        )

        assert result.evaluated_snapshot_count == 6
        assert result.cohort_count == 1
        cohort = result.cohorts[0]
        assert cohort.status == SUCCESS
        assert cohort.robustness_computed is True
        assert cohort.fold_count == 2
        assert cohort.validation_snapshot_count == 4
        assert len(cohort.scenario_results) == 2
        for scenario in cohort.scenario_results:
            fold = scenario.fold_statistics
            snapshot = scenario.snapshot_statistics
            assert fold.statistics.count == 2
            assert snapshot.statistics.count == 4
            assert fold.statistics.delta_stddev == 0
            assert snapshot.statistics.delta_stddev == 0
            assert fold.statistics.min_delta == fold.statistics.max_delta
            assert snapshot.statistics.min_delta == snapshot.statistics.max_delta
            assert fold.worst_fold_index == fold.best_fold_index == 1
            assert (
                snapshot.worst_snapshot_id
                == snapshot.best_snapshot_id
                == snapshots[2].id
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


def test_postgresql_forward_candidate_gross_evidence_enforces_anchor_and_is_read_only():
    scenario = parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "registered-forward-candidate",
                    "component_weights": {
                        "liquidity": "0.20",
                        "trend_alignment": "0.20",
                        "momentum": "0.30",
                        "volume_confirmation": "0.10",
                        "spread": "0.08",
                        "volatility": "0.07",
                        "drawdown": "0.05",
                    },
                }
            ],
        }
    )[0]
    with SessionLocal() as session:
        user = User(name=f"forward-candidate-{uuid4()}")
        session.add(user)
        session.flush()
        reference = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 1, tzinfo=UTC),
            with_outcomes=True,
        )
        pre_registration_future_timestamp = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 4, tzinfo=UTC),
            with_outcomes=True,
        )
        registered_at = datetime(2060, 1, 2, tzinfo=UTC)
        registration = ResearchPolicyCandidateRegistryService(
            session, now_fn=lambda: registered_at
        ).register(reference_snapshot_id=reference.id, scenario=scenario)
        candidate = registration.candidate
        assert candidate.registration_snapshot_id_watermark == (
            pre_registration_future_timestamp.id
        )
        assert candidate.registration_captured_at_watermark == (
            pre_registration_future_timestamp.captured_at
        )

        historical_backfill = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 1, 12, tzinfo=UTC),
            with_outcomes=True,
        )
        captured_watermark_backfill = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 3, tzinfo=UTC),
            with_outcomes=True,
        )
        first_forward = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 5, tzinfo=UTC),
            with_outcomes=True,
        )
        second_forward = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 6, tzinfo=UTC),
            with_outcomes=True,
        )
        changed_baseline = create_snapshot(
            session,
            user,
            captured_at=datetime(2060, 1, 7, tzinfo=UTC),
            with_outcomes=True,
            exclude_cautions=False,
        )

        for snapshot in (first_forward, second_forward):
            for replay_candidate in session.scalars(
                select(StrategyReplayCandidate).where(
                    StrategyReplayCandidate.strategy_replay_snapshot_id == snapshot.id
                )
            ):
                source = session.scalar(
                    select(StrategyReplayCandidateOutcome).where(
                        StrategyReplayCandidateOutcome.strategy_replay_candidate_id
                        == replay_candidate.id,
                        StrategyReplayCandidateOutcome.horizon_minutes == 60,
                    )
                )
                session.add(
                    StrategyReplayCandidateOutcome(
                        strategy_replay_candidate_id=replay_candidate.id,
                        strategy_replay_snapshot_id=snapshot.id,
                        user_id=user.id,
                        exchange="UPBIT",
                        market=replay_candidate.market,
                        horizon_minutes=240,
                        snapshot_at=snapshot.captured_at,
                        reference_at=snapshot.captured_at,
                        target_at=snapshot.captured_at + timedelta(minutes=240),
                        evaluated_at=snapshot.captured_at + timedelta(minutes=241),
                        reference_price=source.reference_price,
                        reference_price_source=source.reference_price_source,
                        end_price=source.end_price,
                        end_price_at=snapshot.captured_at + timedelta(minutes=239),
                        end_price_source=source.end_price_source,
                        market_return_percentage=source.market_return_percentage,
                        evaluation_status="COMPLETE",
                        safe_reason=None,
                    )
                )
        session.flush()
        tracked_models = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked_models
        }

        result = ForwardCandidateGrossEvidenceService(session).evaluate(
            candidate_id=candidate.id,
            horizons=(60, 240, 1440),
        )

        assert result.status == FORWARD_SUCCESS
        assert result.eligible_forward_snapshot_ids == (
            first_forward.id,
            second_forward.id,
        )
        assert reference.id not in result.eligible_forward_snapshot_ids
        assert historical_backfill.id not in result.eligible_forward_snapshot_ids
        assert (
            captured_watermark_backfill.id not in result.eligible_forward_snapshot_ids
        )
        assert changed_baseline.id not in result.eligible_forward_snapshot_ids
        sixty, two_forty, daily = result.horizons
        assert sixty.status == two_forty.status == FORWARD_SUCCESS
        assert sixty.successful_comparable_snapshot_count == 2
        assert two_forty.successful_comparable_snapshot_count == 2
        assert daily.status == FORWARD_OUTCOMES_PENDING
        assert daily.outcome_incomplete_count == 2
        assert all(
            item.performance_evaluated
            for horizon in (sixty, two_forty)
            for item in horizon.snapshots
        )

        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked_models
        }
        assert after == before
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        session.rollback()
