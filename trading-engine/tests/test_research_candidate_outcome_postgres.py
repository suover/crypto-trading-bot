from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AnalysisRun,
    StrategyReplayCandidate,
    StrategyReplayCandidateOutcome,
    StrategyReplaySnapshot,
    User,
)
from crypto_trading_bot.services.historical_outcome_price_resolver import (
    HistoricalOutcomePriceResolver,
)
from crypto_trading_bot.services.research_candidate_outcome_service import (
    ResearchCandidateOutcomeService,
)


class FakeProvider:
    exchange_code = "UPBIT"

    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls.append((market, to))
        return [
            {
                "candle_date_time_utc": (to - timedelta(minutes=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
                "trade_price": "120",
            }
        ]


def _candidate(
    session,
    *,
    snapshot,
    user,
    market,
    price="100",
    in_prefilter=True,
    buy_eligible=True,
    enough=True,
    held=False,
):
    row = StrategyReplayCandidate(
        strategy_replay_snapshot_id=snapshot.id,
        analysis_run_id=snapshot.analysis_run_id,
        user_id=user.id,
        exchange="UPBIT",
        market=market,
        base_asset=market.split("-")[1],
        quote_asset="KRW",
        in_prefilter=in_prefilter,
        prefilter_rank=1 if in_prefilter else None,
        held=held,
        buy_eligible=buy_eligible,
        sell_eligible=held,
        trading_supported=True,
        original_rank=1 if in_prefilter and buy_eligible and enough else None,
        original_score=(
            Decimal("0.5") if in_prefilter and buy_eligible and enough else None
        ),
        final_selected=False,
        final_rank=None,
        selection_source="HELD" if held and not in_prefilter else None,
        quote_trade_value_24h=Decimal("1000"),
        feature_data={"latest_price": price, "enough_candles": enough},
    )
    session.add(row)
    session.flush()
    return row


def _outcome(session, candidate, snapshot, *, status, reason):
    row = StrategyReplayCandidateOutcome(
        strategy_replay_candidate_id=candidate.id,
        strategy_replay_snapshot_id=snapshot.id,
        user_id=candidate.user_id,
        exchange=candidate.exchange,
        market=candidate.market,
        horizon_minutes=60,
        snapshot_at=snapshot.captured_at,
        reference_at=snapshot.captured_at,
        target_at=snapshot.captured_at + timedelta(minutes=60),
        evaluated_at=snapshot.captured_at + timedelta(minutes=61),
        reference_price=Decimal("100")
        if reason != "REFERENCE_PRICE_UNAVAILABLE"
        else None,
        reference_price_source=(
            "STRATEGY_REPLAY_CANDIDATE_FEATURE_LATEST_PRICE"
            if reason != "REFERENCE_PRICE_UNAVAILABLE"
            else "UNAVAILABLE"
        ),
        end_price=Decimal("120") if status == "COMPLETE" else None,
        end_price_at=(
            snapshot.captured_at + timedelta(minutes=59)
            if status == "COMPLETE"
            else None
        ),
        end_price_source=(
            "UPBIT_MINUTE_CANDLE_1M_CLOSE" if status == "COMPLETE" else "UNAVAILABLE"
        ),
        market_return_percentage=Decimal("20") if status == "COMPLETE" else None,
        evaluation_status=status,
        safe_reason=reason,
    )
    session.add(row)
    session.flush()
    return row


def test_postgres_due_scope_retry_call_count_idempotency_and_cascade() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    with SessionLocal() as session:
        user = User(name=f"research-outcome-{uuid4()}")
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
        snapshot = StrategyReplaySnapshot(
            analysis_run_id=run.id,
            pipeline_run_id=run.pipeline_run_id,
            user_id=user.id,
            exchange="UPBIT",
            quote_asset="KRW",
            dataset_schema_version="strategy-replay-dataset-v1",
            policy_signature="strategy-replay-v1:test",
            policy_data={},
            research_candidate_count=8,
            prefilter_candidate_count=6,
            ranked_candidate_count=4,
            final_candidate_count=0,
            captured_at=now - timedelta(minutes=60),
        )
        session.add(snapshot)
        session.flush()
        complete = _candidate(session, snapshot=snapshot, user=user, market="KRW-BTC")
        non_retryable = _candidate(
            session, snapshot=snapshot, user=user, market="KRW-ETH", price=None
        )
        retryable = _candidate(session, snapshot=snapshot, user=user, market="KRW-XRP")
        new = _candidate(session, snapshot=snapshot, user=user, market="KRW-SOL")
        _candidate(
            session,
            snapshot=snapshot,
            user=user,
            market="KRW-DOGE",
            in_prefilter=False,
            held=True,
        )
        _candidate(
            session,
            snapshot=snapshot,
            user=user,
            market="KRW-ADA",
            buy_eligible=False,
        )
        _candidate(
            session, snapshot=snapshot, user=user, market="KRW-DOT", enough=False
        )
        held_prefilter = _candidate(
            session,
            snapshot=snapshot,
            user=user,
            market="KRW-AVAX",
            held=True,
        )
        _outcome(session, complete, snapshot, status="COMPLETE", reason=None)
        _outcome(
            session,
            non_retryable,
            snapshot,
            status="PARTIAL",
            reason="REFERENCE_PRICE_UNAVAILABLE",
        )
        _outcome(
            session,
            retryable,
            snapshot,
            status="PARTIAL",
            reason="HISTORICAL_PRICE_UNAVAILABLE",
        )
        before_count = session.scalar(
            select(func.count()).select_from(StrategyReplayCandidateOutcome)
        )

        dry_provider = FakeProvider()
        dry = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(dry_provider),
            now_fn=lambda: now,
        ).evaluate_due(horizons=(60, 240), batch_size=20, apply=False)
        assert dry.candidate_count == 3
        assert dry.due_outcome_count == 3
        assert dry.complete_count == 3
        assert {market for market, _ in dry_provider.calls} == {
            retryable.market,
            new.market,
            held_prefilter.market,
        }
        assert (
            session.scalar(
                select(func.count()).select_from(StrategyReplayCandidateOutcome)
            )
            == before_count
        )

        apply_provider = FakeProvider()
        applied = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(apply_provider),
            now_fn=lambda: now,
        ).evaluate_due(horizons=(60,), batch_size=20, apply=True)
        assert applied.new_count == 2
        assert applied.update_count == 1
        assert len(apply_provider.calls) == 3
        assert (
            session.scalar(
                select(func.count()).select_from(StrategyReplayCandidateOutcome)
            )
            == before_count + 2
        )

        no_call_provider = FakeProvider()
        repeated = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(no_call_provider),
            now_fn=lambda: now,
        ).evaluate_due(horizons=(60,), batch_size=20, apply=True)
        assert repeated.candidate_count == 0
        assert no_call_provider.calls == []

        existing_sixty_minute_count = session.scalar(
            select(func.count())
            .select_from(StrategyReplayCandidateOutcome)
            .where(StrategyReplayCandidateOutcome.horizon_minutes == 60)
        )
        new_horizon_provider = FakeProvider()
        new_horizon = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(new_horizon_provider),
            now_fn=lambda: now,
        ).evaluate_due(horizons=(30,), batch_size=20, apply=True)
        assert new_horizon.candidate_count == 5
        assert new_horizon.due_outcome_count == 5
        assert len(new_horizon_provider.calls) == 4
        assert (
            session.scalar(
                select(func.count())
                .select_from(StrategyReplayCandidateOutcome)
                .where(StrategyReplayCandidateOutcome.horizon_minutes == 60)
            )
            == existing_sixty_minute_count
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StrategyReplayCandidateOutcome)
                .where(StrategyReplayCandidateOutcome.horizon_minutes == 30)
            )
            == 5
        )

        session.delete(snapshot)
        session.flush()
        assert (
            session.scalar(
                select(func.count())
                .select_from(StrategyReplayCandidateOutcome)
                .where(
                    StrategyReplayCandidateOutcome.strategy_replay_snapshot_id
                    == snapshot.id
                )
            )
            == 0
        )
        session.rollback()


def test_twenty_due_candidates_make_at_most_twenty_exact_public_lookups() -> None:
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    with SessionLocal() as session:
        user = User(name=f"research-call-count-{uuid4()}")
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
        snapshot = StrategyReplaySnapshot(
            analysis_run_id=run.id,
            pipeline_run_id=run.pipeline_run_id,
            user_id=user.id,
            exchange="UPBIT",
            quote_asset="KRW",
            dataset_schema_version="strategy-replay-dataset-v1",
            policy_signature="strategy-replay-v1:test",
            policy_data={},
            research_candidate_count=20,
            prefilter_candidate_count=20,
            ranked_candidate_count=20,
            final_candidate_count=0,
            captured_at=now - timedelta(minutes=60),
        )
        session.add(snapshot)
        session.flush()
        for index in range(20):
            _candidate(
                session,
                snapshot=snapshot,
                user=user,
                market=f"KRW-T{index}",
            )
        provider = FakeProvider()
        result = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(provider),
            now_fn=lambda: now,
        ).evaluate_due(horizons=(60,), batch_size=20, user_id=user.id, apply=False)
        assert result.candidate_count == 20
        assert result.due_outcome_count == 20
        assert len(provider.calls) == 20
        session.rollback()


def test_user_with_zero_snapshots_has_no_due_work_or_public_calls() -> None:
    with SessionLocal() as session:
        user = User(name=f"research-empty-{uuid4()}")
        session.add(user)
        session.flush()
        provider = FakeProvider()
        result = ResearchCandidateOutcomeService(
            session,
            price_resolver=HistoricalOutcomePriceResolver(provider),
            now_fn=lambda: datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        ).evaluate_due(horizons=(60,), batch_size=20, user_id=user.id, apply=False)
        assert result.candidate_count == 0
        assert provider.calls == []
        session.rollback()
