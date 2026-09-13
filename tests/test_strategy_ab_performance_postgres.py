from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from uuid import uuid4
from unittest.mock import MagicMock

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.analysis.market_ranking import HeuristicMarketRankingPolicy
from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal, engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    OrderLog,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    ShadowPolicyEvaluation,
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    TradeRecommendation,
    User,
)
from crypto_trading_bot.services.forward_candidate_gross_evidence_service import (
    FORWARD_OUTCOMES_PENDING,
    SUCCESS as FORWARD_SUCCESS,
    ForwardCandidateGrossEvidenceService,
)
from crypto_trading_bot.services.candidate_registration_bounded_historical_evidence_service import (
    SUCCESS as BOUNDED_HISTORICAL_SUCCESS,
    CandidateRegistrationBoundedHistoricalEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_cost_adjusted_evidence_service import (
    FORWARD_OUTCOMES_PENDING as FORWARD_COST_OUTCOMES_PENDING,
    INSUFFICIENT_FORWARD_TRANSITIONS as FORWARD_COST_INSUFFICIENT,
    SUCCESS as FORWARD_COST_SUCCESS,
    ForwardCandidateCostAdjustedEvidenceService,
)
from crypto_trading_bot.services.forward_candidate_turnover_evidence_service import (
    INSUFFICIENT_FORWARD_TRANSITIONS,
    SUCCESS as FORWARD_TURNOVER_SUCCESS,
    ForwardCandidateTurnoverEvidenceService,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.policy_promotion_gate_service import (
    INSUFFICIENT_DATA as PROMOTION_INSUFFICIENT,
    PolicyPromotionGateService,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ALREADY_ENROLLED as SHADOW_ALREADY_ENROLLED,
    CREATED as SHADOW_CREATED,
    GATE_NOT_ELIGIBLE as SHADOW_GATE_NOT_ELIGIBLE,
    ShadowPolicyEnrollmentService,
)
from crypto_trading_bot.services.shadow_policy_evaluation_service import (
    BASELINE_INTEGRITY_FAILED as SHADOW_BASELINE_INTEGRITY_FAILED,
    CONTEXT_MISMATCH as SHADOW_CONTEXT_MISMATCH,
    NO_NEW_SHADOW_EVALUATIONS as SHADOW_NO_NEW_EVALUATIONS,
    NO_POST_ENROLLMENT_SNAPSHOTS as SHADOW_NO_POST_SNAPSHOTS,
    NO_SHADOW_ENROLLMENT as SHADOW_NO_ENROLLMENT,
    REPLAY_INCOMPATIBLE as SHADOW_REPLAY_INCOMPATIBLE,
    SUCCESS as SHADOW_EVALUATION_SUCCESS,
    ShadowPolicyEvaluationService,
)
from crypto_trading_bot.services.shadow_policy_performance_service import (
    NO_SHADOW_ENROLLMENT as SHADOW_PERFORMANCE_NO_ENROLLMENT,
    NO_SHADOW_EVALUATIONS as SHADOW_PERFORMANCE_NO_EVALUATIONS,
    SUCCESS as SHADOW_PERFORMANCE_SUCCESS,
    ShadowPolicyPerformanceService,
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
from scripts.run_recommendation_outcome_worker import run_shadow_selection_cycle


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


def _forward_turnover_scenario():
    return parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "registered-forward-turnover-candidate",
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


def _register_forward_turnover_candidate(session, user):
    reference = create_snapshot(
        session,
        user,
        captured_at=datetime(2070, 1, 1, tzinfo=UTC),
        with_outcomes=False,
    )
    pre_registration = create_snapshot(
        session,
        user,
        captured_at=datetime(2070, 1, 4, tzinfo=UTC),
        with_outcomes=False,
    )
    candidate = (
        ResearchPolicyCandidateRegistryService(
            session, now_fn=lambda: datetime(2070, 1, 2, tzinfo=UTC)
        )
        .register(
            reference_snapshot_id=reference.id,
            scenario=_forward_turnover_scenario(),
        )
        .candidate
    )
    assert candidate.registration_snapshot_id_watermark == pre_registration.id
    return candidate, reference, pre_registration


def test_postgresql_forward_turnover_first_valid_pair_excludes_pre_registration():
    with SessionLocal() as session:
        user = User(name=f"forward-turnover-pair-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, reference, pre_registration = _register_forward_turnover_candidate(
            session, user
        )
        backfill = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 1, 12, tzinfo=UTC),
            with_outcomes=False,
        )
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=False,
        )
        second = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=False,
        )
        tracked = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }

        result = ForwardCandidateTurnoverEvidenceService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.status == FORWARD_TURNOVER_SUCCESS
        assert result.forward_timeline_snapshot_ids == (first.id, second.id)
        assert result.transition_count == 1
        transition = result.transitions[0].baseline
        assert (transition.previous_snapshot_id, transition.current_snapshot_id) == (
            first.id,
            second.id,
        )
        assert reference.id not in result.forward_timeline_snapshot_ids
        assert pre_registration.id not in result.forward_timeline_snapshot_ids
        assert backfill.id not in result.forward_timeline_snapshot_ids
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        assert after == before
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_forward_turnover_single_snapshot_is_insufficient():
    with SessionLocal() as session:
        user = User(name=f"forward-turnover-single-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=False,
        )

        result = ForwardCandidateTurnoverEvidenceService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
        assert result.forward_timeline_snapshot_ids == (first.id,)
        assert result.transition_count == 0
        assert result.transitions == ()
        session.rollback()


def test_postgresql_forward_turnover_middle_policy_change_is_not_bridged():
    with SessionLocal() as session:
        user = User(name=f"forward-turnover-policy-break-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=False,
        )
        middle = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=False,
            exclude_cautions=False,
        )
        last = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 7, tzinfo=UTC),
            with_outcomes=False,
        )

        result = ForwardCandidateTurnoverEvidenceService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.forward_timeline_snapshot_ids == (first.id, middle.id, last.id)
        assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
        assert result.transition_count == 0
        assert result.continuity_break_count == 2
        session.rollback()


def test_postgresql_forward_turnover_middle_replay_failure_is_not_bridged():
    with SessionLocal() as session:
        user = User(name=f"forward-turnover-replay-break-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=False,
        )
        middle = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=False,
        )
        last = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 7, tzinfo=UTC),
            with_outcomes=False,
        )
        middle.policy_data = {"dataset_schema_version": DATASET_SCHEMA_VERSION}
        session.flush()

        result = ForwardCandidateTurnoverEvidenceService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.forward_timeline_snapshot_ids == (first.id, middle.id, last.id)
        assert result.status == INSUFFICIENT_FORWARD_TRANSITIONS
        assert result.transition_count == 0
        assert result.continuity_break_count == 2
        session.rollback()


def _copy_forward_outcomes(
    session,
    snapshot,
    *,
    horizon_minutes,
    persisted_at=None,
    target_at=None,
    evaluated_at=None,
    updated_at=None,
):
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
        copied = StrategyReplayCandidateOutcome(
            strategy_replay_candidate_id=replay_candidate.id,
            strategy_replay_snapshot_id=snapshot.id,
            user_id=snapshot.user_id,
            exchange=snapshot.exchange,
            market=replay_candidate.market,
            horizon_minutes=horizon_minutes,
            snapshot_at=snapshot.captured_at,
            reference_at=snapshot.captured_at,
            target_at=target_at
            or snapshot.captured_at + timedelta(minutes=horizon_minutes),
            evaluated_at=evaluated_at
            or snapshot.captured_at + timedelta(minutes=horizon_minutes + 1),
            reference_price=source.reference_price,
            reference_price_source=source.reference_price_source,
            end_price=source.end_price,
            end_price_at=snapshot.captured_at + timedelta(minutes=horizon_minutes - 1),
            end_price_source=source.end_price_source,
            market_return_percentage=source.market_return_percentage,
            evaluation_status="COMPLETE",
            safe_reason=None,
        )
        if persisted_at is not None:
            copied.created_at = persisted_at
            copied.updated_at = persisted_at
        if updated_at is not None:
            copied.updated_at = updated_at
        session.add(copied)
    session.flush()


def test_postgresql_registration_bounded_historical_is_stable_and_disjoint():
    with SessionLocal() as session:
        user = User(name=f"bounded-historical-{uuid4()}")
        session.add(user)
        session.flush()
        scenario = _forward_turnover_scenario()
        reference = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 1, tzinfo=UTC),
            with_outcomes=True,
        )
        second = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 1, 6, tzinfo=UTC),
            with_outcomes=True,
        )
        policy_break = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 1, 12, tzinfo=UTC),
            with_outcomes=True,
            exclude_cautions=False,
        )
        third = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 1, 18, tzinfo=UTC),
            with_outcomes=True,
        )
        registered_at = datetime(2075, 1, 2, tzinfo=UTC)
        for snapshot in (reference, second, third):
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=240,
                persisted_at=registered_at - timedelta(minutes=1),
                target_at=registered_at - timedelta(minutes=1),
                evaluated_at=registered_at - timedelta(minutes=1),
            )
        partial_outcomes = tuple(
            session.scalars(
                select(StrategyReplayCandidateOutcome).where(
                    StrategyReplayCandidateOutcome.strategy_replay_snapshot_id.in_(
                        (reference.id, second.id, third.id)
                    ),
                    StrategyReplayCandidateOutcome.horizon_minutes == 240,
                )
            )
        )
        for outcome in partial_outcomes:
            outcome.evaluation_status = "PARTIAL"
            outcome.market_return_percentage = None
        session.flush()
        candidate = (
            ResearchPolicyCandidateRegistryService(
                session, now_fn=lambda: registered_at
            )
            .register(reference_snapshot_id=reference.id, scenario=scenario)
            .candidate
        )
        forward_one = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 3, tzinfo=UTC),
            with_outcomes=True,
        )
        forward_two = create_snapshot(
            session,
            user,
            captured_at=datetime(2075, 1, 4, tzinfo=UTC),
            with_outcomes=True,
        )
        for snapshot in (reference, second, third):
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=120,
                persisted_at=registered_at,
                target_at=registered_at,
                evaluated_at=registered_at,
            )
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=180,
                persisted_at=registered_at - timedelta(minutes=1),
                target_at=registered_at - timedelta(minutes=1),
                evaluated_at=registered_at + timedelta(minutes=1),
            )
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=300,
                persisted_at=registered_at - timedelta(minutes=1),
                target_at=registered_at - timedelta(minutes=1),
                evaluated_at=registered_at - timedelta(minutes=1),
                updated_at=registered_at + timedelta(minutes=1),
            )
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=480,
                persisted_at=registered_at - timedelta(minutes=1),
                target_at=registered_at + timedelta(minutes=1),
                evaluated_at=registered_at - timedelta(minutes=1),
            )
        tracked = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }

        service = CandidateRegistrationBoundedHistoricalEvidenceService(session)
        observed_methods = (
            (service.sweep_service, "evaluate_matrix_snapshots"),
            (service.gross_robustness_service, "evaluate_from_matrix"),
            (service.turnover_service, "evaluate_snapshots"),
            (service.cost_service, "evaluate_from_results"),
            (service.cost_walk_forward_service, "evaluate_from_result"),
            (service.cost_robustness_service, "evaluate_from_results"),
        )
        for owner, name in observed_methods:
            setattr(owner, name, MagicMock(wraps=getattr(owner, name)))
        initial = service.evaluate(
            candidate_id=candidate.id,
            horizons=(60, 120, 180, 240, 300, 480, 600),
            initial_research_size=1,
            validation_size=1,
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert initial.status == BOUNDED_HISTORICAL_SUCCESS
        assert all(
            getattr(owner, name).call_count == 1 for owner, name in observed_methods
        )
        assert initial.historical_timeline_snapshot_ids == (
            reference.id,
            second.id,
            policy_break.id,
            third.id,
        )
        assert initial.historical_candidate_snapshot_ids == (
            reference.id,
            second.id,
            third.id,
        )
        assert initial.target_turnover_cohort.transition_count == 1
        assert initial.target_turnover_cohort.continuity_break_count == 2
        gross_by_horizon = {
            cohort.horizon_minutes: cohort for cohort in initial.gross_matrix.cohorts
        }
        assert len(gross_by_horizon[60].common_snapshot_ids) == 3
        assert len(gross_by_horizon[120].common_snapshot_ids) == 3
        assert len(gross_by_horizon[180].common_snapshot_ids) == 0
        assert len(gross_by_horizon[240].common_snapshot_ids) == 0
        assert len(gross_by_horizon[300].common_snapshot_ids) == 0
        assert len(gross_by_horizon[480].common_snapshot_ids) == 0
        assert len(gross_by_horizon[600].common_snapshot_ids) == 0
        assert initial.cost_adjusted.cohorts[0].scenario_results[0].snapshots
        assert {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        } == before
        assert not session.new and not session.dirty and not session.deleted

        for outcome in partial_outcomes:
            source = session.scalar(
                select(StrategyReplayCandidateOutcome)
                .join(
                    StrategyReplayCandidate,
                    StrategyReplayCandidate.id
                    == StrategyReplayCandidateOutcome.strategy_replay_candidate_id,
                )
                .where(
                    StrategyReplayCandidate.strategy_replay_snapshot_id
                    == outcome.strategy_replay_snapshot_id,
                    StrategyReplayCandidate.market == outcome.market,
                    StrategyReplayCandidateOutcome.horizon_minutes == 60,
                )
            )
            outcome.evaluation_status = "COMPLETE"
            outcome.market_return_percentage = source.market_return_percentage
            outcome.evaluated_at = datetime(2075, 1, 3, tzinfo=UTC)
            outcome.updated_at = datetime(2075, 1, 3, tzinfo=UTC)
        for snapshot in (reference, second, third):
            _copy_forward_outcomes(
                session,
                snapshot,
                horizon_minutes=600,
                persisted_at=datetime(2075, 1, 3, tzinfo=UTC),
            )
        before_rerun = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        after_post_registration_writes = service.evaluate(
            candidate_id=candidate.id,
            horizons=(60, 120, 180, 240, 300, 480, 600),
            initial_research_size=1,
            validation_size=1,
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )
        assert after_post_registration_writes.historical_candidate_snapshot_ids == (
            initial.historical_candidate_snapshot_ids
        )
        rerun_by_horizon = {
            cohort.horizon_minutes: cohort
            for cohort in after_post_registration_writes.gross_matrix.cohorts
        }
        assert len(rerun_by_horizon[240].common_snapshot_ids) == 0
        assert len(rerun_by_horizon[600].common_snapshot_ids) == 0

        forward = ForwardCandidateGrossEvidenceService(session).evaluate(
            candidate_id=candidate.id, horizons=(60,)
        )
        assert forward.eligible_forward_snapshot_ids == (forward_one.id, forward_two.id)
        assert not (
            set(initial.historical_candidate_snapshot_ids)
            & set(forward.eligible_forward_snapshot_ids)
        )
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        assert after == before_rerun
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_forward_cost_adjusted_full_pipeline_is_read_only():
    with SessionLocal() as session:
        user = User(name=f"forward-cost-adjusted-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, reference, pre_registration = _register_forward_turnover_candidate(
            session, user
        )
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=True,
        )
        second = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=True,
        )
        for snapshot in (first, second):
            _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
        tracked = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }

        result = ForwardCandidateCostAdjustedEvidenceService(session).evaluate(
            candidate_id=candidate.id,
            horizons=(60, 240, 1440),
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert result.status == FORWARD_COST_SUCCESS
        assert result.gross_forward_snapshot_ids == (first.id, second.id)
        assert result.turnover_current_snapshot_ids == (second.id,)
        assert reference.id not in result.gross_forward_snapshot_ids
        assert pre_registration.id not in result.gross_forward_snapshot_ids
        sixty, two_forty, daily = result.horizons
        assert sixty.status == two_forty.status == FORWARD_COST_SUCCESS
        assert sixty.cost_adjustable_forward_snapshot_ids == (second.id,)
        assert two_forty.cost_adjustable_forward_snapshot_ids == (second.id,)
        assert daily.status == FORWARD_COST_OUTCOMES_PENDING
        assert daily.cost_adjustable_forward_snapshot_count == 0
        assert all(
            snapshot.snapshot_id != first.id
            for horizon in result.horizons
            for snapshot in horizon.snapshots
        )
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        assert after == before
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_forward_cost_adjusted_does_not_bridge_policy_break():
    with SessionLocal() as session:
        user = User(name=f"forward-cost-policy-break-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=True,
        )
        middle = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=True,
            exclude_cautions=False,
        )
        last = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 7, tzinfo=UTC),
            with_outcomes=True,
        )

        result = ForwardCandidateCostAdjustedEvidenceService(session).evaluate(
            candidate_id=candidate.id,
            horizons=(60,),
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert result.gross_forward_snapshot_ids == (first.id, last.id)
        assert middle.id not in result.gross_forward_snapshot_ids
        assert result.status == FORWARD_COST_INSUFFICIENT
        assert result.turnover_current_snapshot_ids == ()
        assert result.horizons[0].cost_adjustable_forward_snapshot_count == 0
        assert result.horizons[0].snapshots == ()
        session.rollback()


def test_postgresql_policy_promotion_gate_is_insufficient_and_read_only():
    with SessionLocal() as session:
        user = User(name=f"promotion-gate-{uuid4()}")
        session.add(user)
        session.flush()
        historical = []
        for day in range(7):
            snapshot = create_snapshot(
                session,
                user,
                captured_at=datetime(2080, 1, 1, tzinfo=UTC) + timedelta(days=day),
                with_outcomes=True,
            )
            _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
            _copy_forward_outcomes(session, snapshot, horizon_minutes=1440)
            historical.append(snapshot)
        candidate = (
            ResearchPolicyCandidateRegistryService(
                session, now_fn=lambda: datetime(2080, 1, 8, tzinfo=UTC)
            )
            .register(
                reference_snapshot_id=historical[-1].id,
                scenario=_forward_turnover_scenario(),
            )
            .candidate
        )
        for day in range(2):
            snapshot = create_snapshot(
                session,
                user,
                captured_at=datetime(2080, 1, 9, tzinfo=UTC) + timedelta(days=day),
                with_outcomes=True,
            )
            _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
            _copy_forward_outcomes(session, snapshot, horizon_minutes=1440)
        session.flush()
        tracked = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }

        result = PolicyPromotionGateService(session).evaluate(candidate_id=candidate.id)

        assert result.status == PROMOTION_INSUFFICIENT
        assert (
            result.forward_snapshot_id_ceiling
            > candidate.registration_snapshot_id_watermark
        )
        assert len(result.forward_gross.eligible_forward_snapshot_ids) == 2
        enrollment_count = session.scalar(
            select(func.count()).select_from(ShadowPolicyEnrollment)
        )
        enrollment_result = ShadowPolicyEnrollmentService(session).enroll(
            candidate_id=candidate.id
        )
        assert enrollment_result.enrollment_status == SHADOW_GATE_NOT_ELIGIBLE
        assert enrollment_result.gate.status == PROMOTION_INSUFFICIENT
        assert (
            session.scalar(select(func.count()).select_from(ShadowPolicyEnrollment))
            == enrollment_count
        )
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        assert after == before
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_forward_ceiling_excludes_snapshot_inserted_between_evaluations():
    with SessionLocal() as session:
        user = User(name=f"promotion-ceiling-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 5, tzinfo=UTC),
            with_outcomes=True,
        )
        second = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 6, tzinfo=UTC),
            with_outcomes=True,
        )
        ceiling = second.id
        gross = ForwardCandidateGrossEvidenceService(session).evaluate(
            candidate_id=candidate.id,
            horizons=(60,),
            snapshot_id_ceiling=ceiling,
        )
        inserted_after_ceiling = create_snapshot(
            session,
            user,
            captured_at=datetime(2070, 1, 7, tzinfo=UTC),
            with_outcomes=True,
        )
        turnover = ForwardCandidateTurnoverEvidenceService(session).evaluate(
            candidate_id=candidate.id,
            snapshot_id_ceiling=ceiling,
        )

        assert gross.eligible_forward_snapshot_ids == (first.id, second.id)
        assert turnover.forward_timeline_snapshot_ids == (first.id, second.id)
        assert inserted_after_ceiling.id not in gross.eligible_forward_snapshot_ids
        assert inserted_after_ceiling.id not in turnover.forward_timeline_snapshot_ids
        session.rollback()


def _create_complete_gate_candidate(session, user, *, year, failing_horizon=None):
    promotion_scenario = parse_scenario_document(
        {
            "schema_version": "ranking-scenario-sweep-v1",
            "scenarios": [
                {
                    "name": "promotion-gate-candidate",
                    "component_weights": {
                        "liquidity": "0",
                        "trend_alignment": "0",
                        "momentum": "1",
                        "volume_confirmation": "0",
                        "spread": "0",
                        "volatility": "0",
                        "drawdown": "0",
                    },
                }
            ],
        }
    )[0]

    def make_candidate_outcomes_supportive(snapshot):
        favorable_returns = {
            "KRW-A": Decimal("10"),
            "KRW-B": Decimal("-5"),
            "KRW-C": Decimal("20"),
        }
        for outcome in session.scalars(
            select(StrategyReplayCandidateOutcome).where(
                StrategyReplayCandidateOutcome.strategy_replay_snapshot_id
                == snapshot.id
            )
        ):
            market_return = favorable_returns[outcome.market]
            outcome.market_return_percentage = market_return
            outcome.end_price = Decimal("100") + market_return

    historical = []
    base = datetime(year, 1, 1, tzinfo=UTC)
    for day in range(9):
        snapshot = create_snapshot(
            session,
            user,
            captured_at=base + timedelta(days=day),
            with_outcomes=True,
        )
        _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
        _copy_forward_outcomes(session, snapshot, horizon_minutes=1440)
        make_candidate_outcomes_supportive(snapshot)
        historical.append(snapshot)
    registered_at = base + timedelta(days=9)
    candidate = (
        ResearchPolicyCandidateRegistryService(session, now_fn=lambda: registered_at)
        .register(
            reference_snapshot_id=historical[-1].id,
            scenario=promotion_scenario,
        )
        .candidate
    )
    for index in range(21):
        snapshot = create_snapshot(
            session,
            user,
            captured_at=registered_at + timedelta(hours=1 + (index * 8.4)),
            with_outcomes=True,
        )
        _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
        _copy_forward_outcomes(session, snapshot, horizon_minutes=1440)
        make_candidate_outcomes_supportive(snapshot)
        if failing_horizon is not None:
            adverse_returns = {
                "KRW-A": Decimal("0"),
                "KRW-B": Decimal("10"),
                "KRW-C": Decimal("-10"),
            }
            outcomes = session.scalars(
                select(StrategyReplayCandidateOutcome).where(
                    StrategyReplayCandidateOutcome.strategy_replay_snapshot_id
                    == snapshot.id,
                    StrategyReplayCandidateOutcome.horizon_minutes == failing_horizon,
                )
            )
            for outcome in outcomes:
                market_return = adverse_returns[outcome.market]
                outcome.market_return_percentage = market_return
                outcome.end_price = Decimal("100") + market_return
    session.flush()
    return candidate


def test_postgresql_policy_promotion_gate_all_pass_fixture_is_eligible():
    with SessionLocal() as session:
        user = User(name=f"promotion-eligible-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2081)

        result = PolicyPromotionGateService(session).evaluate(candidate_id=candidate.id)

        assert result.status == "ELIGIBLE_FOR_REVIEW", result.failed_checks
        assert not result.insufficient_checks
        assert not result.failed_checks
        assert not result.invalid_checks
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_policy_promotion_gate_sufficient_failure_is_not_eligible():
    with SessionLocal() as session:
        user = User(name=f"promotion-not-eligible-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(
            session, user, year=2082, failing_horizon=1440
        )

        result = PolicyPromotionGateService(session).evaluate(candidate_id=candidate.id)

        assert result.status == "NOT_ELIGIBLE", result.insufficient_checks
        assert any(
            check.horizon_minutes == 1440 and check.status == "FAIL"
            for check in result.failed_checks
        )
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_policy_promotion_gate_corrupt_registry_is_invalid():
    with SessionLocal() as session:
        user = User(name=f"promotion-invalid-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2083)
        candidate.candidate_schema_version = "corrupt"
        session.flush()

        result = PolicyPromotionGateService(session).evaluate(candidate_id=candidate.id)

        assert result.status == "INVALID_PROMOTION_DATA"
        assert result.invalid_checks
        session.rollback()


def test_postgresql_shadow_enrollment_actual_gate_eligible_is_idempotent_and_scoped():
    with SessionLocal() as session:
        user = User(name=f"shadow-enrollment-eligible-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2084)
        tracked = (
            ResearchPolicyCandidate,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        before_enrollments = session.scalar(
            select(func.count()).select_from(ShadowPolicyEnrollment)
        )
        service = ShadowPolicyEnrollmentService(
            session, now_fn=lambda: datetime(2084, 1, 20, tzinfo=UTC)
        )

        created = service.enroll(candidate_id=candidate.id)
        retry = service.enroll(candidate_id=candidate.id)

        assert created.enrollment_status == SHADOW_CREATED
        assert retry.enrollment_status == SHADOW_ALREADY_ENROLLED
        assert retry.enrollment.id == created.enrollment.id
        assert (
            session.scalar(select(func.count()).select_from(ShadowPolicyEnrollment))
            == before_enrollments + 1
        )
        assert {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        } == before
        assert created.enrollment.shadow_snapshot_id_watermark >= (
            created.enrollment.gate_forward_snapshot_id_ceiling
        )
        session.rollback()


def test_postgresql_shadow_enrollment_actual_gate_not_eligible_does_not_write():
    with SessionLocal() as session:
        user = User(name=f"shadow-enrollment-not-eligible-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(
            session, user, year=2085, failing_horizon=1440
        )
        before = session.scalar(
            select(func.count()).select_from(ShadowPolicyEnrollment)
        )

        result = ShadowPolicyEnrollmentService(session).enroll(
            candidate_id=candidate.id
        )

        assert result.enrollment_status == SHADOW_GATE_NOT_ELIGIBLE
        assert result.gate.status == "NOT_ELIGIBLE"
        assert (
            session.scalar(select(func.count()).select_from(ShadowPolicyEnrollment))
            == before
        )
        session.rollback()


def test_postgresql_shadow_enrollment_concurrent_insert_has_one_winner():
    with SessionLocal() as setup_session:
        user = User(name=f"shadow-enrollment-race-{uuid4()}")
        setup_session.add(user)
        setup_session.flush()
        candidate = _create_complete_gate_candidate(setup_session, user, year=2086)
        gate = PolicyPromotionGateService(setup_session).evaluate(
            candidate_id=candidate.id
        )
        candidate_id = candidate.id
        user_id = user.id
        setup_session.commit()

    barrier = Barrier(2)
    results = []
    errors = []

    def enroll_once():
        try:
            with SessionLocal() as session:
                gate_service = MagicMock()
                gate_service.evaluate.return_value = gate
                result = ShadowPolicyEnrollmentService(
                    session,
                    gate_service=gate_service,
                    now_fn=lambda: datetime(2086, 1, 20, tzinfo=UTC),
                    before_watermark_fn=barrier.wait,
                ).enroll(candidate_id=candidate_id)
                session.commit()
                results.append(result.enrollment_status)
        except Exception as error:
            errors.append(error)

    threads = (Thread(target=enroll_once), Thread(target=enroll_once))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert sorted(results) == sorted([SHADOW_CREATED, SHADOW_ALREADY_ENROLLED])
    with SessionLocal() as verify_session:
        assert (
            verify_session.scalar(
                select(func.count())
                .select_from(ShadowPolicyEnrollment)
                .where(ShadowPolicyEnrollment.candidate_id == candidate_id)
            )
            == 1
        )
        verify_session.execute(
            delete(ShadowPolicyEnrollment).where(
                ShadowPolicyEnrollment.candidate_id == candidate_id
            )
        )
        verify_session.execute(
            delete(ResearchPolicyCandidate).where(
                ResearchPolicyCandidate.id == candidate_id
            )
        )
        verify_session.execute(
            delete(StrategyReplaySnapshot).where(
                StrategyReplaySnapshot.user_id == user_id
            )
        )
        verify_session.commit()


def test_postgresql_shadow_selection_requires_existing_enrollment():
    with SessionLocal() as session:
        user = User(name=f"shadow-selection-no-enrollment-{uuid4()}")
        session.add(user)
        session.flush()
        candidate, _, _ = _register_forward_turnover_candidate(session, user)
        before = session.scalar(
            select(func.count()).select_from(ShadowPolicyEvaluation)
        )

        result = ShadowPolicyEvaluationService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.status == SHADOW_NO_ENROLLMENT
        assert not result.database_write
        assert (
            session.scalar(select(func.count()).select_from(ShadowPolicyEvaluation))
            == before
        )
        session.rollback()


def test_postgresql_shadow_selection_no_post_snapshot_is_safe():
    with SessionLocal() as session:
        user = User(name=f"shadow-selection-no-post-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2087)
        enrollment = ShadowPolicyEnrollmentService(
            session, now_fn=lambda: datetime(2087, 1, 20, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        assert enrollment.enrollment_status == SHADOW_CREATED

        result = ShadowPolicyEvaluationService(session).evaluate(
            candidate_id=candidate.id
        )

        assert result.status == SHADOW_NO_POST_SNAPSHOTS
        assert not result.database_write
        assert not session.scalars(select(ShadowPolicyEvaluation)).all()
        session.rollback()


def test_postgresql_shadow_selection_persists_selection_evidence_idempotently():
    with SessionLocal() as session:
        user = User(name=f"shadow-selection-evidence-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2088)
        enrollment = ShadowPolicyEnrollmentService(
            session, now_fn=lambda: datetime(2088, 1, 20, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        assert enrollment.enrollment_status == SHADOW_CREATED
        success_snapshot = create_snapshot(
            session,
            user,
            captured_at=datetime(2088, 1, 21, tzinfo=UTC),
            with_outcomes=False,
        )
        context_break = create_snapshot(
            session,
            user,
            captured_at=datetime(2088, 1, 22, tzinfo=UTC),
            with_outcomes=False,
            top_n=1,
        )
        baseline_mismatch = create_snapshot(
            session,
            user,
            captured_at=datetime(2088, 1, 23, tzinfo=UTC),
            with_outcomes=False,
        )
        mismatch_candidate = session.scalar(
            select(StrategyReplayCandidate).where(
                StrategyReplayCandidate.strategy_replay_snapshot_id
                == baseline_mismatch.id,
                StrategyReplayCandidate.original_rank == 1,
            )
        )
        mismatch_candidate.original_score += Decimal("0.1")
        incompatible = create_snapshot(
            session,
            user,
            captured_at=datetime(2088, 1, 24, tzinfo=UTC),
            with_outcomes=False,
        )
        incompatible_candidate = session.scalar(
            select(StrategyReplayCandidate).where(
                StrategyReplayCandidate.strategy_replay_snapshot_id == incompatible.id,
                StrategyReplayCandidate.original_rank == 1,
            )
        )
        incompatible_candidate.feature_data = {}
        session.flush()
        tracked = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (
                ResearchPolicyCandidate,
                ShadowPolicyEnrollment,
                StrategyReplaySnapshot,
                StrategyReplayCandidate,
                StrategyReplayCandidateOutcome,
            )
        }

        result = ShadowPolicyEvaluationService(
            session, now_fn=lambda: datetime(2088, 1, 25, tzinfo=UTC)
        ).evaluate(candidate_id=candidate.id)

        assert result.status == SHADOW_EVALUATION_SUCCESS
        assert result.created_evaluation_count == 4
        statuses = {
            row.strategy_replay_snapshot_id: row.evaluation_status
            for row in result.evaluations
        }
        assert statuses == {
            success_snapshot.id: SHADOW_EVALUATION_SUCCESS,
            context_break.id: SHADOW_CONTEXT_MISMATCH,
            baseline_mismatch.id: SHADOW_BASELINE_INTEGRITY_FAILED,
            incompatible.id: SHADOW_REPLAY_INCOMPATIBLE,
        }
        signatures = tuple(row.evaluation_signature for row in result.evaluations)
        rerun = ShadowPolicyEvaluationService(session).evaluate(
            candidate_id=candidate.id
        )
        assert rerun.status == SHADOW_NO_NEW_EVALUATIONS
        assert rerun.created_evaluation_count == 0
        assert (
            tuple(row.evaluation_signature for row in rerun.evaluations) == signatures
        )
        assert {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        } == tracked
        session.rollback()


def test_postgresql_shadow_performance_uses_stored_evidence_read_only_without_replay():
    with SessionLocal() as session:
        user = User(name=f"shadow-performance-{uuid4()}")
        session.add(user)
        session.flush()
        candidate = _create_complete_gate_candidate(session, user, year=2094)
        no_enrollment = ShadowPolicyPerformanceService(
            session, now_fn=lambda: datetime(2094, 2, 1, tzinfo=UTC)
        ).evaluate(
            candidate_id=candidate.id,
            horizons=(60,),
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )
        assert no_enrollment.status == SHADOW_PERFORMANCE_NO_ENROLLMENT

        enrollment = ShadowPolicyEnrollmentService(
            session, now_fn=lambda: datetime(2094, 2, 1, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        assert enrollment.enrollment_status == SHADOW_CREATED
        no_evaluations = ShadowPolicyPerformanceService(
            session, now_fn=lambda: datetime(2094, 2, 2, tzinfo=UTC)
        ).evaluate(
            candidate_id=candidate.id,
            horizons=(60,),
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )
        assert no_evaluations.status == SHADOW_PERFORMANCE_NO_EVALUATIONS

        first = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 3, tzinfo=UTC),
            with_outcomes=True,
        )
        context_break = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 4, tzinfo=UTC),
            with_outcomes=False,
            top_n=1,
        )
        baseline_break = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 5, tzinfo=UTC),
            with_outcomes=False,
        )
        baseline_break_candidate = session.scalar(
            select(StrategyReplayCandidate).where(
                StrategyReplayCandidate.strategy_replay_snapshot_id
                == baseline_break.id,
                StrategyReplayCandidate.original_rank == 1,
            )
        )
        baseline_break_candidate.original_score += Decimal("0.1")
        replay_break = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 6, tzinfo=UTC),
            with_outcomes=False,
        )
        replay_break_candidate = session.scalar(
            select(StrategyReplayCandidate).where(
                StrategyReplayCandidate.strategy_replay_snapshot_id == replay_break.id,
                StrategyReplayCandidate.original_rank == 1,
            )
        )
        replay_break_candidate.feature_data = {}
        third = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 7, tzinfo=UTC),
            with_outcomes=True,
        )
        fourth = create_snapshot(
            session,
            user,
            captured_at=datetime(2094, 2, 8, tzinfo=UTC),
            with_outcomes=True,
        )
        for snapshot in (first, third, fourth):
            _copy_forward_outcomes(session, snapshot, horizon_minutes=240)
            _copy_forward_outcomes(session, snapshot, horizon_minutes=1440)
        selection = ShadowPolicyEvaluationService(
            session, now_fn=lambda: datetime(2094, 2, 9, tzinfo=UTC)
        ).evaluate(candidate_id=candidate.id)
        assert selection.status == SHADOW_EVALUATION_SUCCESS
        tracked = (
            ResearchPolicyCandidate,
            ShadowPolicyEnrollment,
            ShadowPolicyEvaluation,
            StrategyReplaySnapshot,
            StrategyReplayCandidate,
            StrategyReplayCandidateOutcome,
            TradeRecommendation,
            OrderLog,
        )
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        replay_service = MagicMock()
        performance = ShadowPolicyPerformanceService(
            session,
            performance_service=StrategyABPerformanceService(
                session, replay_service=replay_service
            ),
            now_fn=lambda: datetime(2094, 2, 10, tzinfo=UTC),
        ).evaluate(
            candidate_id=candidate.id,
            horizons=(60, 240, 1440),
            fee_rate="0.0005",
            spread_cost_rate="0.0005",
            slippage_rate="0.001",
        )

        assert performance.status == SHADOW_PERFORMANCE_SUCCESS, performance.safe_reason
        assert performance.timeline_snapshot_ids == (
            first.id,
            context_break.id,
            baseline_break.id,
            replay_break.id,
            third.id,
            fourth.id,
        )
        assert performance.successful_selection_snapshot_ids == (
            first.id,
            third.id,
            fourth.id,
        )
        assert all(
            horizon.successful_comparable_snapshot_count == 3
            for horizon in performance.gross
        )
        assert performance.context_mismatch_count == 1
        assert performance.baseline_integrity_failed_count == 1
        assert performance.replay_incompatible_count == 1
        assert performance.turnover.transition_count == 1
        assert performance.turnover.transitions[0].previous_snapshot_id == third.id
        assert performance.turnover.transitions[0].current_snapshot_id == fourth.id
        assert performance.turnover.continuity_break_count == 4
        assert all(
            horizon.cost_adjustable_shadow_snapshot_ids == (fourth.id,)
            for horizon in performance.cost_adjusted
        )
        assert performance.offline_replay_performed is False
        assert performance.database_write is False
        replay_service.replay_snapshot.assert_not_called()
        replay_service.replay_snapshots.assert_not_called()
        replay_service.replay_latest.assert_not_called()
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in tracked
        }
        assert after == before
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()


def test_postgresql_shadow_performance_ceiling_defers_new_evaluation():
    with SessionLocal() as setup_session:
        user = User(name=f"shadow-performance-ceiling-{uuid4()}")
        setup_session.add(user)
        setup_session.flush()
        candidate = _create_complete_gate_candidate(setup_session, user, year=2095)
        enrollment = ShadowPolicyEnrollmentService(
            setup_session, now_fn=lambda: datetime(2095, 2, 1, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        assert enrollment.enrollment_status == SHADOW_CREATED
        first = create_snapshot(
            setup_session,
            user,
            captured_at=datetime(2095, 2, 2, tzinfo=UTC),
            with_outcomes=True,
        )
        second = create_snapshot(
            setup_session,
            user,
            captured_at=datetime(2095, 2, 3, tzinfo=UTC),
            with_outcomes=True,
        )
        selection = ShadowPolicyEvaluationService(
            setup_session, now_fn=lambda: datetime(2095, 2, 4, tzinfo=UTC)
        ).evaluate(candidate_id=candidate.id)
        assert selection.created_evaluation_count == 2
        candidate_id = candidate.id
        enrollment_id = enrollment.enrollment.id
        user_id = user.id
        original_ceiling = second.id
        first_id = first.id
        setup_session.commit()

    inserted_id = None

    def insert_after_ceiling():
        nonlocal inserted_id
        with SessionLocal() as insert_session:
            insert_user = insert_session.get(User, user_id)
            inserted = create_snapshot(
                insert_session,
                insert_user,
                captured_at=datetime(2095, 2, 5, tzinfo=UTC),
                with_outcomes=True,
            )
            evaluation = ShadowPolicyEvaluationService(
                insert_session, now_fn=lambda: datetime(2095, 2, 6, tzinfo=UTC)
            ).evaluate(candidate_id=candidate_id)
            assert evaluation.created_evaluation_count == 1
            inserted_id = inserted.id
            insert_session.commit()

    try:
        with SessionLocal() as performance_session:
            current = ShadowPolicyPerformanceService(
                performance_session,
                now_fn=lambda: datetime(2095, 2, 7, tzinfo=UTC),
                after_ceiling_fn=insert_after_ceiling,
            ).evaluate(
                candidate_id=candidate_id,
                horizons=(60,),
                fee_rate="0",
                spread_cost_rate="0",
                slippage_rate="0",
            )
            assert current.status == SHADOW_PERFORMANCE_SUCCESS
            assert current.shadow_evaluation_snapshot_id_ceiling == original_ceiling
            assert current.timeline_snapshot_ids == (first_id, original_ceiling)
            assert inserted_id not in current.timeline_snapshot_ids

            following = ShadowPolicyPerformanceService(
                performance_session,
                now_fn=lambda: datetime(2095, 2, 7, tzinfo=UTC),
            ).evaluate(
                candidate_id=candidate_id,
                horizons=(60,),
                fee_rate="0",
                spread_cost_rate="0",
                slippage_rate="0",
            )
            assert following.status == SHADOW_PERFORMANCE_SUCCESS
            assert following.shadow_evaluation_snapshot_id_ceiling == inserted_id
            assert following.timeline_snapshot_ids == (
                first_id,
                original_ceiling,
                inserted_id,
            )
            performance_session.rollback()
    finally:
        with SessionLocal() as cleanup_session:
            cleanup_session.execute(
                delete(ShadowPolicyEvaluation).where(
                    ShadowPolicyEvaluation.shadow_enrollment_id == enrollment_id
                )
            )
            cleanup_session.execute(
                delete(ShadowPolicyEnrollment).where(
                    ShadowPolicyEnrollment.id == enrollment_id
                )
            )
            cleanup_session.execute(
                delete(ResearchPolicyCandidate).where(
                    ResearchPolicyCandidate.id == candidate_id
                )
            )
            cleanup_session.execute(
                delete(StrategyReplaySnapshot).where(
                    StrategyReplaySnapshot.user_id == user_id
                )
            )
            cleanup_session.execute(
                delete(AnalysisRun).where(AnalysisRun.user_id == user_id)
            )
            cleanup_session.execute(delete(User).where(User.id == user_id))
            cleanup_session.commit()


def test_postgresql_shadow_automatic_cycle_commits_and_is_idempotent():
    with engine.connect() as connection:
        outer_transaction = connection.begin()
        isolated_sessions = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            with isolated_sessions() as setup_session:
                user = User(name=f"shadow-automatic-{uuid4()}")
                setup_session.add(user)
                setup_session.flush()
                candidate = _create_complete_gate_candidate(
                    setup_session, user, year=2093
                )
                enrollment = ShadowPolicyEnrollmentService(
                    setup_session,
                    now_fn=lambda: datetime(2093, 1, 20, tzinfo=UTC),
                ).enroll(candidate_id=candidate.id)
                assert enrollment.enrollment_status == SHADOW_CREATED
                create_snapshot(
                    setup_session,
                    user,
                    captured_at=datetime(2093, 1, 21, tzinfo=UTC),
                    with_outcomes=False,
                )
                candidate_id = candidate.id
                setup_session.commit()

            def service_factory(session):
                return ShadowPolicyEvaluationService(
                    session, now_fn=lambda: datetime(2093, 1, 22, tzinfo=UTC)
                )

            first = run_shadow_selection_cycle(
                isolated_sessions, evaluation_service_factory=service_factory
            )
            assert first.enrollment_count == 1
            assert first.success_candidate_count == 1
            assert first.created_evaluation_count == 1

            with isolated_sessions() as verify_session:
                stored = tuple(
                    verify_session.scalars(
                        select(ShadowPolicyEvaluation).where(
                            ShadowPolicyEvaluation.candidate_id == candidate_id
                        )
                    )
                )
                assert len(stored) == 1
                signature = stored[0].evaluation_signature
                verify_session.rollback()

            second = run_shadow_selection_cycle(
                isolated_sessions, evaluation_service_factory=service_factory
            )
            assert second.enrollment_count == 1
            assert second.no_new_evaluation_candidate_count == 1
            assert second.created_evaluation_count == 0
            with isolated_sessions() as verify_session:
                rerun = tuple(
                    verify_session.scalars(
                        select(ShadowPolicyEvaluation).where(
                            ShadowPolicyEvaluation.candidate_id == candidate_id
                        )
                    )
                )
                assert len(rerun) == 1
                assert rerun[0].evaluation_signature == signature
                verify_session.rollback()
        finally:
            outer_transaction.rollback()


def test_postgresql_shadow_automatic_cycle_ignores_unenrolled_candidates():
    with engine.connect() as connection:
        outer_transaction = connection.begin()
        isolated_sessions = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            with isolated_sessions() as setup_session:
                user = User(name=f"shadow-automatic-unenrolled-{uuid4()}")
                setup_session.add(user)
                setup_session.flush()
                _register_forward_turnover_candidate(setup_session, user)
                setup_session.commit()

            result = run_shadow_selection_cycle(isolated_sessions)
            assert result.enrollment_count == 0
            assert result.processed_candidate_count == 0
            assert result.created_evaluation_count == 0
        finally:
            outer_transaction.rollback()


def test_postgresql_shadow_selection_concurrent_apply_has_one_immutable_row():
    with SessionLocal() as setup_session:
        user = User(name=f"shadow-selection-concurrent-{uuid4()}")
        setup_session.add(user)
        setup_session.flush()
        candidate = _create_complete_gate_candidate(setup_session, user, year=2089)
        enrollment = ShadowPolicyEnrollmentService(
            setup_session, now_fn=lambda: datetime(2089, 1, 20, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        snapshot = create_snapshot(
            setup_session,
            user,
            captured_at=datetime(2089, 1, 21, tzinfo=UTC),
            with_outcomes=False,
        )
        candidate_id = candidate.id
        enrollment_id = enrollment.enrollment.id
        snapshot_id = snapshot.id
        user_id = user.id
        setup_session.commit()

    barrier = Barrier(2)
    statuses = []
    errors = []

    def evaluate_once():
        try:
            with SessionLocal() as session:
                result = ShadowPolicyEvaluationService(
                    session,
                    now_fn=lambda: datetime(2089, 1, 22, tzinfo=UTC),
                    after_ceiling_fn=barrier.wait,
                ).evaluate(candidate_id=candidate_id)
                session.commit()
                statuses.append(result.status)
        except Exception as error:
            errors.append(error)

    threads = (Thread(target=evaluate_once), Thread(target=evaluate_once))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert sorted(statuses) == sorted(
        [SHADOW_EVALUATION_SUCCESS, SHADOW_NO_NEW_EVALUATIONS]
    )
    with SessionLocal() as verify_session:
        rows = tuple(
            verify_session.scalars(
                select(ShadowPolicyEvaluation).where(
                    ShadowPolicyEvaluation.shadow_enrollment_id == enrollment_id,
                    ShadowPolicyEvaluation.strategy_replay_snapshot_id == snapshot_id,
                )
            )
        )
        assert len(rows) == 1
        verify_session.execute(
            delete(ShadowPolicyEvaluation).where(
                ShadowPolicyEvaluation.shadow_enrollment_id == enrollment_id
            )
        )
        verify_session.execute(
            delete(ShadowPolicyEnrollment).where(
                ShadowPolicyEnrollment.id == enrollment_id
            )
        )
        verify_session.execute(
            delete(ResearchPolicyCandidate).where(
                ResearchPolicyCandidate.id == candidate_id
            )
        )
        verify_session.execute(
            delete(StrategyReplaySnapshot).where(
                StrategyReplaySnapshot.user_id == user_id
            )
        )
        verify_session.commit()


def test_postgresql_shadow_selection_ceiling_defers_concurrent_snapshot():
    with SessionLocal() as setup_session:
        user = User(name=f"shadow-selection-ceiling-{uuid4()}")
        setup_session.add(user)
        setup_session.flush()
        candidate = _create_complete_gate_candidate(setup_session, user, year=2090)
        enrollment = ShadowPolicyEnrollmentService(
            setup_session, now_fn=lambda: datetime(2090, 1, 20, tzinfo=UTC)
        ).enroll(candidate_id=candidate.id)
        first = create_snapshot(
            setup_session,
            user,
            captured_at=datetime(2090, 1, 21, tzinfo=UTC),
            with_outcomes=False,
        )
        candidate_id = candidate.id
        enrollment_id = enrollment.enrollment.id
        first_id = first.id
        user_id = user.id
        setup_session.commit()

    inserted_id = None

    def insert_after_ceiling():
        nonlocal inserted_id
        with SessionLocal() as insert_session:
            insert_user = insert_session.get(User, user_id)
            inserted = create_snapshot(
                insert_session,
                insert_user,
                captured_at=datetime(2090, 1, 22, tzinfo=UTC),
                with_outcomes=False,
            )
            inserted_id = inserted.id
            insert_session.commit()

    with SessionLocal() as evaluation_session:
        first_result = ShadowPolicyEvaluationService(
            evaluation_session,
            now_fn=lambda: datetime(2090, 1, 23, tzinfo=UTC),
            after_ceiling_fn=insert_after_ceiling,
        ).evaluate(candidate_id=candidate_id)
        assert first_result.status == SHADOW_EVALUATION_SUCCESS
        assert first_result.evaluation_snapshot_id_ceiling == first_id
        assert first_result.timeline_snapshot_ids == (first_id,)
        assert inserted_id not in first_result.timeline_snapshot_ids
        evaluation_session.commit()

    with SessionLocal() as next_session:
        next_result = ShadowPolicyEvaluationService(
            next_session, now_fn=lambda: datetime(2090, 1, 24, tzinfo=UTC)
        ).evaluate(candidate_id=candidate_id)
        assert next_result.status == SHADOW_EVALUATION_SUCCESS
        assert next_result.created_evaluation_count == 1
        assert inserted_id in next_result.timeline_snapshot_ids
        next_session.commit()

    with SessionLocal() as cleanup_session:
        cleanup_session.execute(
            delete(ShadowPolicyEvaluation).where(
                ShadowPolicyEvaluation.shadow_enrollment_id == enrollment_id
            )
        )
        cleanup_session.execute(
            delete(ShadowPolicyEnrollment).where(
                ShadowPolicyEnrollment.id == enrollment_id
            )
        )
        cleanup_session.execute(
            delete(ResearchPolicyCandidate).where(
                ResearchPolicyCandidate.id == candidate_id
            )
        )
        cleanup_session.execute(
            delete(StrategyReplaySnapshot).where(
                StrategyReplaySnapshot.user_id == user_id
            )
        )
        cleanup_session.commit()
