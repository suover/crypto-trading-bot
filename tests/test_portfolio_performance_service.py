from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from crypto_trading_bot.db.models import (
    AccountActivity,
    AccountCashFlowValuation,
    PortfolioSnapshot,
)
from crypto_trading_bot.services.cash_flow_valuation_service import (
    CashFlowValuationPlan,
    CashFlowValuationService,
)
from crypto_trading_bot.services.portfolio_performance_service import (
    PortfolioPerformanceService,
    TimedCashFlow,
    modified_dietz_return,
)


START = datetime(2026, 9, 1, tzinfo=UTC)


class CandleProvider:
    exchange_code = "UPBIT"

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_minute_candles(self, market, unit, count, to=None):
        self.calls.append((market, unit, count, to))
        return self.rows


def snapshot(
    snapshot_id: int,
    hours: int,
    value: str | None,
    status="COMPLETE",
    policy: str | None = None,
):
    row = PortfolioSnapshot(
        analysis_run_id=snapshot_id,
        pipeline_run_id=f"00000000-0000-0000-0000-{snapshot_id:012d}",
        user_id=1,
        exchange="UPBIT",
        quote_asset="KRW",
        valuation_policy_signature=policy,
        priced_positions_value_krw=Decimal("0"),
        position_count=0,
        unpriced_asset_count=0,
        missing_cost_basis_count=0,
        valuation_status=status,
        total_value_krw=Decimal(value) if value is not None else None,
        captured_at=START + timedelta(hours=hours),
    )
    row.id = snapshot_id
    return row


def coverage(start=START, end=START + timedelta(days=2)):
    return {
        source: SimpleNamespace(
            source_type=source,
            sync_status="COMPLETE",
            coverage_start_at=start,
            coverage_end_at=end,
        )
        for source in ("UPBIT_DEPOSIT", "UPBIT_WITHDRAWAL")
    }


def flow(activity_id: int, hours: int, value: str, direction="IN"):
    amount = Decimal(value)
    return CashFlowValuationPlan(
        account_activity_id=activity_id,
        user_id=1,
        exchange="UPBIT",
        direction=direction,
        currency="KRW",
        native_amount=amount,
        event_time=START + timedelta(hours=hours),
        valuation_price_krw=Decimal("1"),
        cash_flow_value_krw=amount,
        price_source="NATIVE_KRW",
        valuation_status="COMPLETE",
        safe_reason=None,
        valued_at=START,
    )


def account_flow(
    activity_id: int,
    *,
    currency="KRW",
    amount="100",
    direction="IN",
    source="UPBIT_DEPOSIT",
    activity_type="DEPOSIT",
    state="ACCEPTED",
    occurred_at=START,
    completed_at=START,
):
    row = AccountActivity(
        user_id=1,
        exchange="UPBIT",
        source_type=source,
        activity_type=activity_type,
        origin="ACCOUNT_EXTERNAL",
        exchange_activity_id=f"flow-{activity_id}",
        state=state,
        currency=currency,
        amount=amount,
        cash_flow_direction=direction,
        occurred_at=occurred_at,
        completed_at=completed_at,
    )
    row.id = activity_id
    return row


def service():
    return PortfolioPerformanceService(None, now_fn=lambda: START)


def test_modified_dietz_hand_calculated_cash_flow_neutral_returns() -> None:
    end = START + timedelta(hours=24)
    midpoint = START + timedelta(hours=12)
    assert (
        modified_dietz_return(
            Decimal("100"),
            Decimal("200"),
            START,
            end,
            [TimedCashFlow(midpoint, Decimal("100"))],
        )
        == 0
    )
    assert (
        modified_dietz_return(
            Decimal("200"),
            Decimal("100"),
            START,
            end,
            [TimedCashFlow(midpoint, Decimal("-100"))],
        )
        == 0
    )
    # (230 - 100 - 100) / (100 + 0.5*100) = 20%.
    assert modified_dietz_return(
        Decimal("100"),
        Decimal("230"),
        START,
        end,
        [TimedCashFlow(midpoint, Decimal("100"))],
    ) == Decimal("0.2")
    # Withdrawal plus loss: (90 - 200 - -100) / (200 - 0.5*100).
    assert modified_dietz_return(
        Decimal("200"),
        Decimal("90"),
        START,
        end,
        [TimedCashFlow(midpoint, Decimal("-100"))],
    ) == Decimal("-10") / Decimal("150")
    # Two timed flows: net flow=50, weighted flow=75-12.5.
    assert modified_dietz_return(
        Decimal("100"),
        Decimal("160"),
        START,
        end,
        [
            TimedCashFlow(START + timedelta(hours=6), Decimal("100")),
            TimedCashFlow(START + timedelta(hours=18), Decimal("-50")),
        ],
    ) == Decimal("10") / Decimal("162.5")


def test_baseline_cumulative_chain_performance_index_and_drawdown() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, "100"),
            snapshot(2, 24, "110"),
            snapshot(3, 48, "99"),
        ),
        (),
        coverage(),
    )

    assert plans[0].performance_status == "BASELINE"
    assert plans[0].period_return_percentage is None
    assert plans[0].performance_index == 100
    assert plans[1].period_return_percentage == Decimal("10.0000000000")
    assert plans[1].performance_index == Decimal("110.0000000000")
    assert plans[2].period_return_percentage == Decimal("-10.0000000000")
    assert plans[2].performance_index == Decimal("99.0000000000")
    assert plans[2].high_water_mark_index == Decimal("110.0000000000")
    assert plans[2].high_water_mark_krw == Decimal("110.0000000000")
    assert plans[2].drawdown_krw == Decimal("-11.0000000000")
    assert plans[2].drawdown_percentage == Decimal("-10.0000000000")
    assert plans[2].max_drawdown_percentage == Decimal("-10.0000000000")


def test_cash_flow_periods_and_partial_sources_fail_closed() -> None:
    deposit_plans = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "200")),
        (flow(1, 12, "100"),),
        coverage(),
    )
    assert deposit_plans[1].period_return_percentage == 0
    assert deposit_plans[1].external_inflow_krw == 100

    partial_nav = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, None, "PARTIAL")),
        (),
        coverage(),
    )
    assert partial_nav[1].performance_status == "PARTIAL"
    assert partial_nav[1].safe_reason == "NAV_INCOMPLETE"
    assert partial_nav[1].period_return_percentage is None

    missing_coverage = coverage()
    missing_coverage.pop("UPBIT_WITHDRAWAL")
    uncovered = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "110")),
        (),
        missing_coverage,
    )
    assert uncovered[1].safe_reason == "ACCOUNT_ACTIVITY_COVERAGE_INCOMPLETE"


def test_incomplete_cash_flow_rebaselines_next_complete_snapshot() -> None:
    incomplete = flow(1, 12, "100")
    incomplete = CashFlowValuationPlan(
        **{
            **incomplete.__dict__,
            "cash_flow_value_krw": None,
            "valuation_status": "PARTIAL",
            "safe_reason": "HISTORICAL_PRICE_UNAVAILABLE",
        }
    )
    plans = service()._calculate(
        (
            snapshot(1, 0, "100", policy="policy-a"),
            snapshot(2, 24, "200", policy="policy-a"),
            snapshot(3, 48, "210", policy="policy-b"),
        ),
        (incomplete,),
        coverage(),
    )
    assert plans[1].safe_reason == "CASH_FLOW_VALUATION_INCOMPLETE"
    assert plans[2].performance_status == "BASELINE"
    assert plans[2].safe_reason == "REBASELINE_AFTER_PARTIAL_GAP"
    assert plans[2].performance_index == 100


def test_partial_then_complete_creates_new_baseline() -> None:
    plans = service()._calculate(
        (snapshot(1, 0, None, "PARTIAL"), snapshot(2, 24, "150")),
        (),
        coverage(),
    )

    assert plans[0].performance_status == "PARTIAL"
    assert plans[0].safe_reason == "NAV_INCOMPLETE"
    assert plans[1].performance_status == "BASELINE"
    assert plans[1].safe_reason == "REBASELINE_AFTER_PARTIAL_GAP"
    assert plans[1].period_return_percentage is None
    assert plans[1].cumulative_return_percentage == 0
    assert plans[1].performance_index == 100
    assert plans[1].drawdown_percentage == 0
    assert plans[1].max_drawdown_percentage == 0


def test_return_starts_after_rebaseline_and_multiple_partial_snapshots() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, None, "PARTIAL"),
            snapshot(2, 12, None, "PARTIAL"),
            snapshot(3, 24, "100"),
            snapshot(4, 48, "110"),
        ),
        (),
        coverage(),
    )

    assert [plan.performance_status for plan in plans[:2]] == ["PARTIAL", "PARTIAL"]
    assert plans[2].performance_status == "BASELINE"
    assert plans[2].performance_index == 100
    assert plans[3].performance_status == "COMPLETE"
    assert plans[3].period_return_percentage == Decimal("10.0000000000")
    assert plans[3].performance_index == Decimal("110.0000000000")


def test_complete_partial_complete_starts_a_fresh_baseline() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, "100"),
            snapshot(2, 24, None, "PARTIAL"),
            snapshot(3, 48, "125"),
        ),
        (),
        coverage(),
    )

    assert plans[0].safe_reason == "NO_PREVIOUS_SNAPSHOT"
    assert plans[1].performance_status == "PARTIAL"
    assert plans[2].performance_status == "BASELINE"
    assert plans[2].safe_reason == "REBASELINE_AFTER_PARTIAL_GAP"
    assert plans[2].performance_index == 100


def test_policy_change_rebaselines_without_treating_removed_value_as_loss() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, "1000", policy="policy-a"),
            snapshot(2, 24, "757", policy="policy-b"),
            snapshot(3, 48, "832.7", policy="policy-b"),
        ),
        (),
        coverage(),
    )

    assert plans[0].performance_status == "BASELINE"
    assert plans[1].performance_status == "BASELINE"
    assert plans[1].safe_reason == "REBASELINE_AFTER_VALUATION_POLICY_CHANGE"
    assert plans[1].period_return_percentage is None
    assert plans[1].cumulative_return_percentage == 0
    assert plans[1].performance_index == 100
    assert plans[1].drawdown_percentage == 0
    assert plans[1].max_drawdown_percentage == 0
    assert plans[2].performance_status == "COMPLETE"
    assert plans[2].period_return_percentage == Decimal("10.0000000000")
    assert plans[2].performance_index == Decimal("110.0000000000")


def test_same_policy_keeps_normal_complete_chronology() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, "100", policy="policy-a"),
            snapshot(2, 24, "110", policy="policy-a"),
        ),
        (),
        coverage(),
    )

    assert plans[1].performance_status == "COMPLETE"
    assert plans[1].period_return_percentage == Decimal("10.0000000000")


def test_partial_gap_reason_remains_distinct_from_policy_change() -> None:
    plans = service()._calculate(
        (
            snapshot(1, 0, "100", policy="policy-a"),
            snapshot(2, 24, None, "PARTIAL", policy="policy-b"),
            snapshot(3, 48, "90", policy="policy-b"),
        ),
        (),
        coverage(),
    )

    assert plans[1].safe_reason == "NAV_INCOMPLETE"
    assert plans[2].performance_status == "BASELINE"
    assert plans[2].safe_reason == "REBASELINE_AFTER_PARTIAL_GAP"


def test_malformed_complete_cash_flow_is_still_fail_closed() -> None:
    malformed = flow(1, 12, "100")
    malformed = CashFlowValuationPlan(**{**malformed.__dict__, "direction": None})
    plans = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "200")),
        (malformed,),
        coverage(),
    )

    assert plans[1].performance_status == "PARTIAL"
    assert plans[1].safe_reason == "CASH_FLOW_VALUATION_INCOMPLETE"


def test_completed_activity_without_derived_valuation_fails_closed() -> None:
    activity = AccountActivity(
        user_id=1,
        exchange="UPBIT",
        source_type="UPBIT_DEPOSIT",
        activity_type="DEPOSIT",
        origin="ACCOUNT_EXTERNAL",
        exchange_activity_id="deposit-1",
        state="ACCEPTED",
        currency="BTC",
        amount=Decimal("1"),
        cash_flow_direction="IN",
        occurred_at=START + timedelta(hours=12),
        completed_at=START + timedelta(hours=12),
    )
    activity.id = 99
    plans = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "110")),
        (),
        coverage(),
        (activity,),
    )

    assert plans[1].performance_status == "PARTIAL"
    assert plans[1].safe_reason == "CASH_FLOW_VALUATION_MISSING"


def test_deposit_uses_completed_at_for_price_and_dietz_weight() -> None:
    created_at = START + timedelta(hours=3)
    completed_at = START + timedelta(hours=12)
    provider = CandleProvider(
        [{"candle_date_time_utc": "2026-09-01T11:59:00", "trade_price": "50"}]
    )
    valuation_service = CashFlowValuationService(
        None, market_data_provider=provider, now_fn=lambda: START
    )
    activity = account_flow(
        10,
        currency="BTC",
        amount="2",
        occurred_at=created_at,
        completed_at=completed_at,
    )

    valuation = valuation_service._plan(activity, None)
    plans = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "230")),
        (valuation,),
        coverage(),
        (activity,),
    )

    assert valuation.event_time == completed_at
    assert provider.calls == [("KRW-BTC", 1, 10, completed_at)]
    assert plans[1].external_inflow_krw == 100
    assert plans[1].period_return_percentage == Decimal("20.0000000000")


def test_withdrawal_uses_completed_at_for_price_and_dietz_weight() -> None:
    created_at = START + timedelta(hours=3)
    completed_at = START + timedelta(hours=12)
    provider = CandleProvider(
        [{"candle_date_time_utc": "2026-09-01T11:59:00", "trade_price": "50"}]
    )
    valuation_service = CashFlowValuationService(
        None, market_data_provider=provider, now_fn=lambda: START
    )
    activity = account_flow(
        11,
        currency="BTC",
        amount="2",
        direction="OUT",
        source="UPBIT_WITHDRAWAL",
        activity_type="WITHDRAWAL",
        state="DONE",
        occurred_at=created_at,
        completed_at=completed_at,
    )

    valuation = valuation_service._plan(activity, None)
    plans = service()._calculate(
        (snapshot(1, 0, "200"), snapshot(2, 24, "90")),
        (valuation,),
        coverage(),
        (activity,),
    )

    assert valuation.event_time == completed_at
    assert provider.calls == [("KRW-BTC", 1, 10, completed_at)]
    assert plans[1].external_outflow_krw == 100
    assert plans[1].period_return_percentage == Decimal("-6.6666666667")


def test_completed_activity_without_completed_at_is_partial_without_fallback() -> None:
    provider = CandleProvider([])
    valuation_service = CashFlowValuationService(
        None, market_data_provider=provider, now_fn=lambda: START
    )
    activity = account_flow(
        12,
        currency="BTC",
        occurred_at=START + timedelta(hours=6),
        completed_at=None,
    )

    valuation = valuation_service._plan(activity, None)
    plans = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "200")),
        (valuation,),
        coverage(),
        (activity,),
    )

    assert valuation.event_time is None
    assert valuation.valuation_status == "PARTIAL"
    assert valuation.safe_reason == "INVALID_EVENT_TIME"
    assert provider.calls == []
    assert plans[1].performance_status == "PARTIAL"
    assert plans[1].safe_reason == "CASH_FLOW_COMPLETION_TIME_MISSING"


def test_period_boundaries_use_open_start_and_closed_end() -> None:
    at_start = flow(20, 0, "100")
    at_end = flow(21, 24, "100")

    start_boundary = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "100")),
        (at_start,),
        coverage(),
    )
    end_boundary = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "200")),
        (at_end,),
        coverage(),
    )

    assert start_boundary[1].external_inflow_krw == 0
    assert start_boundary[1].period_return_percentage == 0
    assert end_boundary[1].external_inflow_krw == 100
    assert end_boundary[1].period_return_percentage == 0


def test_three_snapshot_boundary_flow_is_included_exactly_once() -> None:
    boundary = flow(22, 24, "100")
    plans = service()._calculate(
        (
            snapshot(1, 0, "100"),
            snapshot(2, 24, "200"),
            snapshot(3, 48, "200"),
        ),
        (boundary,),
        coverage(),
    )

    assert plans[1].external_inflow_krw == 100
    assert plans[2].external_inflow_krw == 0
    assert sum(plan.external_inflow_krw or 0 for plan in plans) == 100
    assert plans[1].period_return_percentage == 0
    assert plans[2].period_return_percentage == 0


def test_cached_valuation_event_time_must_match_completed_at() -> None:
    occurred_at = START + timedelta(hours=3)
    completed_at = START + timedelta(hours=12)
    activity = account_flow(
        23,
        currency="BTC",
        occurred_at=occurred_at,
        completed_at=completed_at,
    )
    stale = AccountCashFlowValuation(
        account_activity_id=activity.id,
        user_id=1,
        exchange="UPBIT",
        direction="IN",
        currency="BTC",
        native_amount=Decimal("100"),
        event_time=occurred_at,
        valuation_price_krw=Decimal("10"),
        cash_flow_value_krw=Decimal("1000"),
        price_source="UPBIT_MINUTE_CANDLE_1M_CLOSE",
        valuation_status="COMPLETE",
        valued_at=START,
    )
    provider = CandleProvider(
        [{"candle_date_time_utc": "2026-09-01T11:59:00", "trade_price": "20"}]
    )

    plan = CashFlowValuationService(
        None, market_data_provider=provider, now_fn=lambda: START
    )._plan(activity, stale)

    assert plan.event_time == completed_at
    assert plan.valuation_price_krw == 20
    assert provider.calls == [("KRW-BTC", 1, 10, completed_at)]

    stale_plan = CashFlowValuationPlan(
        account_activity_id=activity.id,
        user_id=1,
        exchange="UPBIT",
        direction="IN",
        currency="BTC",
        native_amount=Decimal("100"),
        event_time=occurred_at,
        valuation_price_krw=Decimal("10"),
        cash_flow_value_krw=Decimal("1000"),
        price_source="UPBIT_MINUTE_CANDLE_1M_CLOSE",
        valuation_status="COMPLETE",
        safe_reason=None,
        valued_at=START,
    )
    performance = service()._calculate(
        (snapshot(1, 0, "100"), snapshot(2, 24, "1100")),
        (stale_plan,),
        coverage(),
        (activity,),
    )
    assert performance[1].performance_status == "PARTIAL"
    assert performance[1].safe_reason == "CASH_FLOW_VALUATION_EVENT_TIME_STALE"


def test_historical_price_uses_only_fully_closed_pre_event_candle() -> None:
    event = datetime(2026, 9, 3, 12, 34, 30, tzinfo=UTC)
    provider = CandleProvider(
        [
            {"candle_date_time_utc": "2026-09-03T12:34:00", "trade_price": "999"},
            {"candle_date_time_utc": "2026-09-03T12:33:00", "trade_price": "100"},
            {"candle_date_time_utc": "2026-09-03T12:32:00", "trade_price": "90"},
        ]
    )
    valuation_service = CashFlowValuationService(None, market_data_provider=provider)

    assert valuation_service._historical_price("KRW-BTC", event) == 100
    assert provider.calls == [("KRW-BTC", 1, 10, event)]


def test_krw_and_crypto_cash_flows_are_valued_without_current_price_fallback() -> None:
    provider = CandleProvider(
        [{"candle_date_time_utc": "2026-08-31T23:59:00", "trade_price": "50"}]
    )
    valuation_service = CashFlowValuationService(
        None,
        market_data_provider=provider,
        now_fn=lambda: START,
    )

    krw = valuation_service._plan(account_flow(1), None)
    btc = valuation_service._plan(account_flow(2, currency="BTC", amount="2"), None)

    assert krw.valuation_status == "COMPLETE"
    assert krw.valuation_price_krw == 1
    assert krw.cash_flow_value_krw == 100
    assert krw.price_source == "NATIVE_KRW"
    assert btc.valuation_status == "COMPLETE"
    assert btc.valuation_price_krw == 50
    assert btc.cash_flow_value_krw == 100
    assert btc.price_source == "UPBIT_MINUTE_CANDLE_1M_CLOSE"
    assert provider.calls == [("KRW-BTC", 1, 10, START)]


def test_missing_or_invalid_historical_price_is_partial() -> None:
    for rows in (
        [],
        [
            {
                "candle_date_time_utc": "2026-08-31T23:59:00",
                "trade_price": "Infinity",
            }
        ],
    ):
        plan = CashFlowValuationService(
            None,
            market_data_provider=CandleProvider(rows),
            now_fn=lambda: START,
        )._plan(account_flow(1, currency="BTC"), None)
        assert plan.valuation_status == "PARTIAL"
        assert plan.cash_flow_value_krw is None
        assert plan.safe_reason == "HISTORICAL_PRICE_UNAVAILABLE"


def test_invalid_cash_flow_amount_is_not_coerced_to_zero() -> None:
    plan = CashFlowValuationService(
        None,
        market_data_provider=CandleProvider([]),
        now_fn=lambda: START,
    )._plan(account_flow(1, amount="NaN"), None)

    assert plan.valuation_status == "PARTIAL"
    assert plan.native_amount is None
    assert plan.cash_flow_value_krw is None
    assert plan.safe_reason == "INVALID_AMOUNT"


def test_completed_state_filter_excludes_pending_failed_and_orders() -> None:
    def activity(source, activity_type, state):
        return AccountActivity(
            source_type=source,
            activity_type=activity_type,
            state=state,
        )

    assert CashFlowValuationService._is_completed(
        activity("UPBIT_DEPOSIT", "DEPOSIT", "ACCEPTED")
    )
    assert CashFlowValuationService._is_completed(
        activity("UPBIT_WITHDRAWAL", "WITHDRAWAL", "DONE")
    )
    for item in (
        activity("UPBIT_DEPOSIT", "DEPOSIT", "PROCESSING"),
        activity("UPBIT_WITHDRAWAL", "WITHDRAWAL", "FAILED"),
        activity("UPBIT_WITHDRAWAL", "WITHDRAWAL", "CANCELLED"),
        activity("UPBIT_CLOSED_ORDER", "ORDER", "DONE"),
    ):
        assert not CashFlowValuationService._is_completed(item)


def test_dry_run_services_do_not_write_flush_or_commit() -> None:
    cash_session = MagicMock()
    cash_session.scalars.side_effect = [iter(()), iter(())]
    cash_result = CashFlowValuationService(
        cash_session, market_data_provider=CandleProvider([])
    ).value_scope(1, apply=False)
    assert cash_result.plans == ()
    cash_session.add.assert_not_called()
    cash_session.delete.assert_not_called()
    cash_session.flush.assert_not_called()
    cash_session.commit.assert_not_called()

    performance_session = MagicMock()
    performance_session.scalars.side_effect = [
        iter(()),
        iter(()),
        iter(()),
        iter(()),
        iter(()),
    ]
    result = PortfolioPerformanceService(performance_session).rebuild(1, apply=False)
    assert result.plans == ()
    performance_session.add.assert_not_called()
    performance_session.delete.assert_not_called()
    performance_session.flush.assert_not_called()
    performance_session.commit.assert_not_called()
