"""Testcontainers-only schema bootstrap. Never reads repository .env or secrets.

The parent Java test supplies an ephemeral container URL. The real Alembic tree
remains the sole DDL source; ORM objects below are test fixtures, not migrations.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from prepare_trade_analysis import prepare_trade_analysis

from crypto_trading_bot.config import settings
from crypto_trading_bot.db.models import (
    AnalysisRun,
    AccountSnapshot,
    PortfolioSnapshot,
    PortfolioPositionSnapshot,
    PortfolioPerformanceSnapshot,
    BotTradingPnlSummary,
    TradeRecommendation,
    ResearchCandidateAutomationState,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    StrategyReplaySnapshot,
    User,
)


def main():
    # No generic DATABASE_URL fallback: only the test runner may supply this value.
    url = os.environ["BACKEND_TEST_MIGRATION_URL"]
    if not url.startswith("postgresql+psycopg://fixture_owner:fixture-only@"):
        raise RuntimeError("Expected isolated Testcontainers fixture credentials")
    settings.get_settings = lambda: SimpleNamespace(database_url=url)
    engine_root = Path(__file__).resolve().parents[4] / "trading-engine"
    config = Config(str(engine_root / "alembic.ini"))
    config.set_main_option("script_location", str(engine_root / "migrations"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    time = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            User(id=i, name=f"fixture-{i}", is_active=i != 3) for i in range(1, 5)
        )
        session.flush()
        for offset, (user, exchange, quote) in enumerate(
            [(1, "UPBIT", "KRW")] * 3
            + [(2, "UPBIT", "KRW"), (1, "BINANCE", "KRW"), (1, "UPBIT", "BTC")]
        ):
            snapshot_id = 101 + offset
            session.add(
                AnalysisRun(
                    id=snapshot_id,
                    user_id=user,
                    run_type="TEST",
                    trading_mode="AI_APPROVAL",
                    status="SUCCESS",
                )
            )
            session.flush()
            snapshot = StrategyReplaySnapshot(
                id=snapshot_id,
                analysis_run_id=snapshot_id,
                pipeline_run_id=str(snapshot_id),
                user_id=user,
                exchange=exchange,
                quote_asset=quote,
                dataset_schema_version="dataset-v1",
                policy_signature="baseline",
                policy_data={},
                research_candidate_count=20,
                prefilter_candidate_count=10,
                ranked_candidate_count=7,
                final_candidate_count=8,
                captured_at=time + timedelta(hours=min(offset, 1)),
                created_at=time,
            )
            session.add(snapshot)
            session.flush()
            candidate = ResearchPolicyCandidate(
                id=201 + offset,
                candidate_schema_version="candidate-v1",
                user_id=user,
                exchange=exchange,
                quote_asset=quote,
                scenario_name=[
                    "Momentum A",
                    "Momentum B",
                    "Value",
                    "Private",
                    "Other",
                    "BTC",
                ][offset],
                scenario_definition_signature=f"definition-{offset}",
                component_weights={
                    "liquidity": "0.123456789012345678901234567890",
                    "momentum": "0.5",
                },
                reference_snapshot_id=snapshot_id,
                reference_snapshot_captured_at=snapshot.captured_at,
                dataset_schema_version="dataset-v1",
                baseline_policy_signature="baseline",
                effective_top_n=7,
                registered_at=time + timedelta(days=1 if offset == 2 else 0),
                registration_snapshot_id_watermark=snapshot_id,
                registration_captured_at_watermark=snapshot.captured_at,
            )
            session.add(candidate)
            session.flush()
            if offset in (0, 3, 4, 5):
                session.add(
                    ShadowPolicyEnrollment(
                        id=301 + offset,
                        enrollment_schema_version="enrollment-v1",
                        candidate_id=candidate.id,
                        candidate_schema_version="candidate-v1",
                        user_id=user,
                        exchange=exchange,
                        quote_asset=quote,
                        scenario_name=candidate.scenario_name,
                        scenario_definition_signature=candidate.scenario_definition_signature,
                        component_weights=candidate.component_weights,
                        dataset_schema_version="dataset-v1",
                        baseline_policy_signature="baseline",
                        effective_top_n=7,
                        candidate_registered_at=candidate.registered_at,
                        candidate_registration_snapshot_id_watermark=snapshot_id,
                        candidate_registration_captured_at_watermark=snapshot.captured_at,
                        gate_result_type="fixture",
                        gate_policy_schema_version="fixture",
                        gate_policy_signature="not-exposed",
                        gate_policy_definition={},
                        gate_status="ELIGIBLE_FOR_REVIEW",
                        gate_evaluated_at=time,
                        gate_forward_snapshot_id_ceiling=snapshot_id,
                        gate_checks=[],
                        gate_evidence_provenance={},
                        gate_decision_signature="not-exposed",
                        shadow_enrolled_at=time,
                        shadow_snapshot_id_watermark=snapshot_id,
                        shadow_captured_at_watermark=snapshot.captured_at,
                    )
                )
        for user, exchange, quote, signature, top_n in [
            (1, "UPBIT", "KRW", "a", 7),
            (1, "UPBIT", "KRW", "b", 5),
            (2, "UPBIT", "KRW", "a", 7),
            (1, "BINANCE", "KRW", "a", 7),
            (1, "UPBIT", "BTC", "a", 7),
        ]:
            session.add(
                ResearchCandidateAutomationState(
                    user_id=user,
                    exchange=exchange,
                    quote_asset=quote,
                    dataset_schema_version="dataset-v1",
                    baseline_policy_signature=signature,
                    effective_top_n=top_n,
                    last_observed_snapshot_id=103,
                    reviewed_snapshot_id=102,
                    reviewed_captured_at=time,
                    reviewed_created_at=time,
                    review_status="OBSERVED",
                    review_cycle_cap=2,
                    review_context_cap=2,
                )
            )
        prepare_overview(session, time)
        prepare_trade_analysis(session, time)
        session.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE ROLE backend_reader LOGIN PASSWORD 'reader-fixture-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            )
        )
        connection.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
        connection.execute(text("REVOKE ALL ON DATABASE backend_fixture FROM PUBLIC"))
        connection.execute(
            text("GRANT CONNECT ON DATABASE backend_fixture TO backend_reader")
        )
        connection.execute(text("GRANT USAGE ON SCHEMA public TO backend_reader"))
        connection.execute(
            text(
                "GRANT SELECT ON users, research_policy_candidates, strategy_replay_snapshots, "
                "research_candidate_automation_states, shadow_policy_enrollments, "
                "portfolio_snapshots, portfolio_position_snapshots, portfolio_performance_snapshots, "
                "bot_trading_pnl_summaries, trade_recommendations TO backend_reader"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT (id, user_id, pipeline_run_id, run_type, trading_mode, status, started_at, finished_at) ON analysis_runs TO backend_reader"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT (id, analysis_run_id, user_id, exchange, market, base_asset, quote_asset, rank, score, selection_source, buy_eligible, sell_eligible, quote_trade_value_24h, feature_data, created_at) ON market_universe_candidates TO backend_reader"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT (id, recommendation_id, user_id, status, expires_at, approved_at, rejected_at, created_at, updated_at) ON approval_requests TO backend_reader"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT (id, recommendation_id, approval_request_id, order_log_id, user_id, trading_mode, attempt_number, status, error_code, attempted_at, next_retry_at) ON order_execution_attempts TO backend_reader"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT (id, recommendation_id, approval_request_id, user_id, trading_mode, exchange, market, side, order_type, amount_krw, quantity, price, status, executed_quantity, executed_funds_krw, average_execution_price, paid_fee, remaining_quantity, trades_count, execution_synced_at, created_at, updated_at) ON order_logs TO backend_reader"
            )
        )
    engine.dispose()


def prepare_overview(session, time):
    """Separate IDs; do not alter Research fixtures or create any production data."""
    session.add_all(User(id=i, name=f"overview-{i}") for i in (10, 11))
    session.flush()
    big = 9007199254740993
    snapshots = {}
    for sid, user, exchange, quote, day, status, perf in [
        (1001, 1, "UPBIT", "KRW", 0, "COMPLETE", "BASELINE"),
        (big + 1, 1, "UPBIT", "KRW", 1, "COMPLETE", "COMPLETE"),
        (1002, 1, "UPBIT", "KRW", 3, "COMPLETE", "COMPLETE"),
        (big, 1, "UPBIT", "KRW", 3, "PARTIAL", "PARTIAL"),
        (1010, 10, "UPBIT", "KRW", 3, "COMPLETE", "COMPLETE"),
        (1101, 11, "UPBIT", "KRW", 0, "COMPLETE", "BASELINE"),
        (1102, 11, "UPBIT", "KRW", 3, "COMPLETE", None),
        (2001, 2, "UPBIT", "KRW", 9, "COMPLETE", "COMPLETE"),
        (2002, 1, "BINANCE", "KRW", 9, "COMPLETE", "COMPLETE"),
        (2003, 1, "UPBIT", "BTC", 9, "COMPLETE", "COMPLETE"),
        # Inconsistent child owner/exchange rows exercise defensive scope joins.
        (1004, 1, "UPBIT", "KRW", 2, "COMPLETE", None),
        (1005, 1, "UPBIT", "KRW", 2, "COMPLETE", None),
        (2004, 2, "UPBIT", "KRW", 8, "COMPLETE", None),
    ]:
        session.add(
            AnalysisRun(
                id=sid,
                user_id=user,
                run_type="TEST",
                trading_mode="AI_APPROVAL",
                status="SUCCESS",
            )
        )
        session.flush()
        snapshot = PortfolioSnapshot(
            id=sid,
            analysis_run_id=sid,
            pipeline_run_id=str(sid),
            user_id=user,
            exchange=exchange,
            quote_asset=quote,
            valuation_policy_signature="fixture-policy",
            cash_available_krw=Decimal("1000"),
            cash_locked_krw=Decimal("0"),
            cash_total_krw=Decimal("1000"),
            priced_positions_value_krw=Decimal("13000"),
            known_total_value_krw=Decimal("12345678901234567890.1234567890"),
            total_value_krw=None if status == "PARTIAL" else Decimal("14000"),
            unrealized_pnl_krw=None if status == "PARTIAL" else Decimal("0"),
            position_count=3 if sid == big else 0,
            unpriced_asset_count=1 if sid == big else 0,
            missing_cost_basis_count=1 if sid == big else 0,
            valuation_status=status,
            captured_at=time + timedelta(days=day),
        )
        session.add(snapshot)
        session.flush()
        snapshots[sid] = snapshot
        if perf:
            add_performance(session, snapshot, perf, time)
    add_performance(session, snapshots[1004], "COMPLETE", time, user=2)
    add_performance(session, snapshots[1005], "COMPLETE", time, exchange="BINANCE")
    add_performance(session, snapshots[2004], "COMPLETE", time, user=1)
    for pid, sid, currency, market, value, basis, status, exchange in [
        (3001, big, "BTC", "KRW-BTC", "12000", "10000", "PRICED", "UPBIT"),
        (3002, big, "ETH", "KRW-ETH", "1000", None, "PRICED", "UPBIT"),
        (3003, big, "APENFT", None, None, None, "UNPRICED", "UPBIT"),
        (3004, 2001, "OTHER", "KRW-OTHER", "999999", "1", "PRICED", "UPBIT"),
        (3005, big, "WRONG", "KRW-WRONG", "999999", "1", "PRICED", "BINANCE"),
    ]:
        s = snapshots[sid]
        session.add(
            AccountSnapshot(
                id=pid,
                analysis_run_id=s.analysis_run_id,
                user_id=s.user_id,
                exchange=exchange,
                currency=currency,
                balance=Decimal("0.1234567890"),
            )
        )
        session.flush()
        session.add(
            PortfolioPositionSnapshot(
                id=pid,
                portfolio_snapshot_id=sid,
                account_snapshot_id=pid,
                exchange=exchange,
                currency=currency,
                market=market,
                available_quantity=Decimal("0.1234567890"),
                locked_quantity=Decimal("0.1"),
                total_quantity=Decimal("0.2234567890"),
                avg_buy_price=Decimal("100") if basis else None,
                mark_price=Decimal("1000") if value else None,
                market_value_krw=Decimal(value) if value else None,
                estimated_cost_basis_krw=Decimal(basis) if basis else None,
                unrealized_pnl_krw=Decimal("-12.1234567890") if basis else None,
                unrealized_pnl_percentage=Decimal("-0.1234567890") if basis else None,
                valuation_status=status,
                price_source="UNAVAILABLE" if value is None else "MARKET_SNAPSHOT",
            )
        )
    for bid, user, exchange, status in [
        (5001, 1, "UPBIT", "PARTIAL"),
        (5002, 10, "UPBIT", "COMPLETE"),
        (5003, 2, "UPBIT", "COMPLETE"),
        (5004, 1, "BINANCE", "COMPLETE"),
    ]:
        # The source ORM specifies every required accounting field; these are stored fixture values.
        values = {
            column.name: Decimal("0")
            for column in BotTradingPnlSummary.__table__.columns
            if column.type.__class__.__name__ == "Numeric" and not column.nullable
        }
        values.update(
            {
                column.name: 0
                for column in BotTradingPnlSummary.__table__.columns
                if column.type.__class__.__name__ == "Integer" and not column.nullable
            }
        )
        values.update(
            recognized_realized_pnl_krw=Decimal("-12345.6789000000"),
            recognized_realized_return_percentage=Decimal("-1.2345678901"),
            total_fees_krw=Decimal("12.3"),
            processed_order_count=5,
            unmatched_sell_count=1 if status == "PARTIAL" else 0,
        )
        session.add(
            BotTradingPnlSummary(
                id=bid,
                user_id=user,
                exchange=exchange,
                accounting_status=status,
                source_signature="fixture",
                source_last_updated_at=time,
                calculated_at=time + timedelta(hours=2),
                **values,
            )
        )
    for rid, user, exchange, market, action, age in [
        (4001, 1, "UPBIT", "KRW-BTC", "BUY", 0),
        (4002, 1, "UPBIT", "KRW-ETH", "SELL", 0),
        (4003, 1, "UPBIT", "KRW-XRP", "HOLD", 0),
        (big + 10, 1, "UPBIT", "KRW-BTC", "BUY", -1),
        (4004, 2, "UPBIT", "KRW-BTC", "BUY", 1),
        (4005, 1, "BINANCE", "KRW-BTC", "BUY", 1),
        (4006, 1, "UPBIT", "BTC-ETH", "BUY", 1),
        (4007, 1, "UPBIT", "XKRW-BTC", "BUY", 1),
        (4008, 1, "UPBIT", "KRW-", "BUY", 1),
    ]:
        session.add(
            AnalysisRun(
                id=rid,
                user_id=user,
                run_type="TEST",
                trading_mode="AI_APPROVAL",
                status="SUCCESS",
            )
        )
        session.flush()
        session.add(
            TradeRecommendation(
                id=rid,
                analysis_run_id=rid,
                user_id=user,
                exchange=exchange,
                market=market,
                action=action,
                confidence=Decimal("0.9876"),
                trade_ratio=None if action == "HOLD" else Decimal("0.123456789"),
                ai_model=None if action == "HOLD" else "fixture-model",
                status="APPROVED",
                reason="private-reason-not-exposed",
                ai_response={"private": "not-exposed"},
                created_at=time + timedelta(days=age),
            )
        )


def add_performance(session, snapshot, status, time, user=None, exchange=None):
    partial = status == "PARTIAL"
    baseline = status == "BASELINE"
    session.add(
        PortfolioPerformanceSnapshot(
            id=snapshot.id,
            portfolio_snapshot_id=snapshot.id,
            user_id=user if user is not None else snapshot.user_id,
            exchange=exchange if exchange is not None else snapshot.exchange,
            period_start_at=None if baseline else time,
            period_end_at=snapshot.captured_at,
            return_method="MODIFIED_DIETZ",
            period_return_percentage=None
            if partial or baseline
            else Decimal("1.1234567890"),
            cumulative_return_percentage=None
            if partial
            else Decimal("0" if baseline else "2.1234567890"),
            performance_index=None
            if partial
            else Decimal("100" if baseline else "102.1234567890"),
            drawdown_index=None if partial else Decimal("0" if baseline else "-3"),
            drawdown_percentage=None
            if partial
            else Decimal("0" if baseline else "-2.1234567890"),
            max_drawdown_percentage=None
            if partial
            else Decimal("0" if baseline else "-4"),
            performance_status=status,
            safe_reason="UNPRICED_ASSET"
            if partial
            else "NO_PREVIOUS_SNAPSHOT"
            if baseline
            else None,
            calculated_at=snapshot.captured_at + timedelta(hours=1),
            end_value_krw=snapshot.total_value_krw,
        )
    )


if __name__ == "__main__":
    main()
