from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import (
    AccountActivity,
    AccountActivitySyncState,
    AccountCashFlowValuation,
    AnalysisRun,
    PortfolioPerformanceSnapshot,
    PortfolioSnapshot,
    User,
)
from crypto_trading_bot.services.cash_flow_valuation_service import (
    CashFlowValuationService,
)
from crypto_trading_bot.services.portfolio_performance_service import (
    PortfolioPerformanceService,
)


def test_apply_is_idempotent_on_postgresql_and_dry_run_does_not_add_rows() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    with SessionLocal() as session:
        user = User(name=f"portfolio-performance-{uuid4()}")
        session.add(user)
        session.flush()
        snapshots = []
        for index, value in enumerate(("100", "200"), start=1):
            run = AnalysisRun(
                user_id=user.id,
                pipeline_run_id=str(uuid4()),
                run_type="PORTFOLIO_VALUATION",
                trading_mode="AI_APPROVAL",
                status="SUCCESS",
            )
            session.add(run)
            session.flush()
            snapshots.append(
                PortfolioSnapshot(
                    analysis_run_id=run.id,
                    pipeline_run_id=run.pipeline_run_id,
                    user_id=user.id,
                    exchange="UPBIT",
                    quote_asset="KRW",
                    priced_positions_value_krw=Decimal("0"),
                    known_total_value_krw=Decimal(value),
                    total_value_krw=Decimal(value),
                    position_count=0,
                    unpriced_asset_count=0,
                    missing_cost_basis_count=0,
                    valuation_status="COMPLETE",
                    captured_at=start + timedelta(days=index - 1),
                )
            )
        session.add_all(snapshots)
        session.add(
            AccountActivity(
                user_id=user.id,
                exchange="UPBIT",
                source_type="UPBIT_DEPOSIT",
                activity_type="DEPOSIT",
                origin="ACCOUNT_EXTERNAL",
                exchange_activity_id=str(uuid4()),
                currency="KRW",
                state="ACCEPTED",
                amount=Decimal("100"),
                cash_flow_direction="IN",
                occurred_at=start + timedelta(hours=3),
                completed_at=start + timedelta(hours=12),
            )
        )
        for source in ("UPBIT_DEPOSIT", "UPBIT_WITHDRAWAL"):
            session.add(
                AccountActivitySyncState(
                    user_id=user.id,
                    exchange="UPBIT",
                    source_type=source,
                    sync_status="COMPLETE",
                    coverage_start_at=start,
                    coverage_end_at=start + timedelta(days=1),
                )
            )
        session.flush()

        cash_service = CashFlowValuationService(session, now_fn=lambda: start)
        performance_service = PortfolioPerformanceService(session, now_fn=lambda: start)
        dry_cash = cash_service.value_scope(user.id, apply=False)
        dry_performance = performance_service.rebuild(
            user.id, valuation_plans=dry_cash.plans, apply=False
        )
        assert dry_performance.plans[-1].period_return_percentage == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountCashFlowValuation)
                .where(AccountCashFlowValuation.user_id == user.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PortfolioPerformanceSnapshot)
                .where(PortfolioPerformanceSnapshot.user_id == user.id)
            )
            == 0
        )

        for _ in range(2):
            cash_result = cash_service.value_scope(user.id, apply=True)
            performance_service.rebuild(
                user.id,
                valuation_plans=cash_result.plans,
                apply=True,
            )

        assert (
            session.scalar(
                select(func.count())
                .select_from(AccountCashFlowValuation)
                .where(AccountCashFlowValuation.user_id == user.id)
            )
            == 1
        )
        persisted_cash_flow = session.scalar(
            select(AccountCashFlowValuation).where(
                AccountCashFlowValuation.user_id == user.id
            )
        )
        assert persisted_cash_flow.event_time == start + timedelta(hours=12)
        assert (
            session.scalar(
                select(func.count())
                .select_from(PortfolioPerformanceSnapshot)
                .where(PortfolioPerformanceSnapshot.user_id == user.id)
            )
            == 2
        )
        session.rollback()
