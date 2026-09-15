from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.models import OrderFill, OperationalAlert
from crypto_trading_bot.services.live_canary_evidence_service import (
    ACTIVE,
    EVIDENCE_AVAILABLE,
    EVIDENCE_SCHEMA_VERSION,
    EXHAUSTED,
    EXPIRED,
    NO_CANARY_RUNS,
    STOPPED,
    LiveCanaryEvidenceError,
    LiveCanaryEvidenceService,
    live_canary_evidence_signature,
)
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertService,
)
from scripts.evaluate_live_canary_evidence import parse_arguments, report


NOW = datetime(2026, 9, 15, 1, 2, 3, tzinfo=UTC)


def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        market_universe_mode="DYNAMIC",
        market_universe_top_n=7,
    )


def service(session=None):
    return LiveCanaryEvidenceService(
        session or MagicMock(), settings=settings(), now_fn=lambda: NOW
    )


def test_signature_is_deterministic_and_canonical():
    first = {
        "b": Decimal("1.00"),
        "a": datetime(2026, 9, 15, 10, tzinfo=timezone(timedelta(hours=9))),
    }
    second = {"a": datetime(2026, 9, 15, 1, tzinfo=UTC), "b": Decimal("1")}
    assert live_canary_evidence_signature(first) == live_canary_evidence_signature(
        second
    )
    assert live_canary_evidence_signature(first).startswith(
        f"{EVIDENCE_SCHEMA_VERSION}:"
    )


@pytest.mark.parametrize(
    "field,value",
    (
        ("activation", "changed"),
        ("ceiling", 2),
        ("evidence_as_of", NOW + timedelta(seconds=1)),
        ("runs", [{"id": 1}]),
        ("orders", [{"id": 2}]),
        ("fills", [{"id": 3}]),
    ),
)
def test_signature_changes_when_evidence_changes(field, value):
    baseline = {
        "activation": "original",
        "ceiling": 1,
        "evidence_as_of": NOW,
        "runs": [],
        "orders": [],
        "fills": [],
    }
    changed = {**baseline, field: value}
    assert live_canary_evidence_signature(baseline) != (
        live_canary_evidence_signature(changed)
    )


@pytest.mark.parametrize(
    ("terminated", "expires_at", "runs", "expected"),
    (
        (object(), NOW + timedelta(hours=1), 0, STOPPED),
        (None, NOW, 0, EXPIRED),
        (None, NOW + timedelta(hours=1), 6, EXHAUSTED),
        (None, NOW + timedelta(hours=1), 5, ACTIVE),
    ),
)
def test_lifecycle_is_factual_and_has_canonical_precedence(
    terminated, expires_at, runs, expected
):
    activation = SimpleNamespace(expires_at=expires_at, max_analysis_runs=6)
    assert service()._lifecycle(activation, terminated, runs, NOW) == expected


def test_evidence_requires_clean_session_before_any_query():
    session = MagicMock()
    session.new = {object()}
    session.dirty = set()
    session.deleted = set()
    with pytest.raises(LiveCanaryEvidenceError, match="clean Session"):
        service(session).evaluate(canary_activation_id=1)
    session.get_bind.assert_not_called()


def test_postgresql_snapshot_is_repeatable_read_and_read_only():
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.in_transaction.return_value = False
    connection = session.connection.return_value
    assert service(session)._start_snapshot() == "REPEATABLE_READ_READ_ONLY"
    session.connection.assert_called_once_with(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    statement = connection.execute.call_args.args[0]
    assert str(statement) == str(text("SET TRANSACTION READ ONLY"))


def test_postgresql_snapshot_fails_closed_if_session_already_started():
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.in_transaction.return_value = True
    with pytest.raises(LiveCanaryEvidenceError, match="active transaction"):
        service(session)._start_snapshot()
    session.connection.assert_not_called()


def test_evidence_as_of_clock_is_called_once():
    clock = MagicMock(return_value=NOW)
    value = LiveCanaryEvidenceService(
        MagicMock(), settings=settings(), now_fn=clock
    )._capture_evidence_as_of()
    assert value == NOW
    clock.assert_called_once_with()


def test_all_source_ceilings_are_captured_once():
    session = MagicMock()
    session.scalar.side_effect = range(1, 9)
    values = service(session)._capture_ceilings(NOW)
    assert len(values) == 8
    assert session.scalar.call_count == 8


def test_terminal_fill_aggregate_match_and_direct_decimal_summary():
    session = MagicMock()
    order = SimpleNamespace(
        id=10,
        side="BUY",
        status="LIVE_DONE",
        executed_quantity=Decimal("2"),
        executed_funds_krw=Decimal("300"),
        paid_fee=Decimal("0.15"),
    )
    fills = (
        OrderFill(
            id=1,
            order_log_id=10,
            exchange_trade_id="a",
            price=Decimal("100"),
            volume=Decimal("1"),
            funds_krw=Decimal("100"),
            side="BUY",
            raw_data={},
            created_at=NOW,
        ),
        OrderFill(
            id=2,
            order_log_id=10,
            exchange_trade_id="b",
            price=Decimal("200"),
            volume=Decimal("1"),
            funds_krw=Decimal("200"),
            side="BUY",
            raw_data={},
            created_at=NOW,
        ),
    )
    session.scalars.return_value = fills
    _, summary, findings = service(session)._fill_evidence(
        (order,),
        NOW,
        {"order_fill_id_ceiling": 2},
    )
    assert findings == []
    assert summary["filled_buy_quantity"] == Decimal("2")
    assert summary["gross_buy_executed_funds_krw"] == Decimal("300")
    assert summary["total_fee_krw"] == Decimal("0.15")


def test_terminal_fill_mismatch_is_structural_but_pending_no_fill_is_valid():
    session = MagicMock()
    session.scalars.return_value = ()
    terminal = SimpleNamespace(
        id=10,
        side="BUY",
        status="LIVE_DONE",
        executed_quantity=Decimal("1"),
        executed_funds_krw=Decimal("100"),
        paid_fee=None,
    )
    _, _, terminal_findings = service(session)._fill_evidence(
        (terminal,), NOW, {"order_fill_id_ceiling": None}
    )
    pending = SimpleNamespace(
        id=11,
        side="BUY",
        status="LIVE_WAIT",
        executed_quantity=None,
        executed_funds_krw=None,
        paid_fee=None,
    )
    _, _, pending_findings = service(session)._fill_evidence(
        (pending,), NOW, {"order_fill_id_ceiling": None}
    )
    assert terminal_findings == ["ORDER_FILL_AGGREGATE_MISMATCH:10"]
    assert pending_findings == []


@pytest.mark.parametrize(
    ("alert_type", "error_code"),
    (
        ("LIVE_CANARY_STARTED", "CANARY_STARTED"),
        ("LIVE_CANARY_STOPPED", "MANUAL_STOP"),
        ("LIVE_CANARY_BUY_LIMIT_BLOCKED", "PER_ORDER_LIMIT"),
        ("LIVE_CANARY_BUY_LIMIT_BLOCKED", "DAILY_LIMIT"),
        ("LIVE_CANARY_BUY_LIMIT_BLOCKED", "BUDGET_LOCK_BUSY"),
        ("LIVE_CANARY_PROVENANCE_INVALID", "INVALID_CANARY_PROVENANCE"),
    ),
)
def test_canary_alert_persists_structured_error_code(alert_type, error_code):
    session = MagicMock()
    session.scalar.return_value = None
    alert = OperationalAlertService(session).create_canary_alert(
        alert_type=alert_type,
        dedup_key=f"test:{alert_type}:{error_code}",
        safe_message="safe",
        user_id=1,
        error_code=error_code,
    )
    assert isinstance(alert, OperationalAlert)
    assert alert.error_code == error_code
    session.add.assert_called_once_with(alert)
    session.flush.assert_called_once_with()


def test_canary_alert_dedup_contract_is_unchanged():
    existing = OperationalAlert(
        alert_type="LIVE_CANARY_STARTED",
        severity="WARNING",
        user_id=1,
        error_code=None,
        safe_message="legacy",
        dedup_key="CANARY_STARTED:1",
    )
    session = MagicMock()
    session.scalar.return_value = existing
    returned = OperationalAlertService(session).create_canary_alert(
        alert_type="LIVE_CANARY_STARTED",
        dedup_key="CANARY_STARTED:1",
        safe_message="new",
        user_id=1,
        error_code="CANARY_STARTED",
    )
    assert returned is existing
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_cli_has_only_required_activation_argument():
    assert parse_arguments(["--canary-activation-id", "7"]).canary_activation_id == 7
    for forbidden in ("--apply", "--force", "--as-of", "--promote"):
        with pytest.raises(SystemExit):
            parse_arguments(["--canary-activation-id", "7", forbidden])


def test_cli_report_contains_safety_flags_and_deterministic_json():
    result = service()._finish(
        status="NO_CANARY_ACTIVATION",
        lifecycle=None,
        activation_id=99,
        candidate_id=None,
        evidence_as_of=NOW,
        consistency="REPEATABLE_READ_READ_ONLY",
        ceilings={},
        payload=service()._empty_payload(),
    )
    lines = report(result)
    assert "database_write=false" in lines
    assert "external_calls=false" in lines
    assert any(line.startswith("evidence_json={") for line in lines)


def test_valid_activation_without_runs_is_factual_no_canary_runs(monkeypatch):
    activation = SimpleNamespace(
        id=1,
        candidate_id=2,
        expires_at=NOW + timedelta(hours=1),
        max_analysis_runs=6,
    )
    approval = SimpleNamespace(row=SimpleNamespace())
    session = MagicMock()
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    session.scalar.return_value = activation
    session.scalars.return_value = ()
    value = service(session)
    monkeypatch.setattr(value, "_start_snapshot", lambda: "TEST_SNAPSHOT")
    monkeypatch.setattr(value, "_capture_evidence_as_of", lambda: NOW)
    monkeypatch.setattr(value, "_capture_ceilings", lambda _: {})
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.validate_stored_canary_activation",
        lambda *_: (approval, None, None),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_canary_safety_binding",
        lambda *_: object(),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_canary_termination",
        lambda *_: None,
    )
    monkeypatch.setattr(
        value,
        "_collect",
        lambda **_: (
            value._empty_payload(),
            [],
            {
                "activation": True,
                "binding": True,
                "promotion": True,
                "termination": True,
                "runs": True,
                "recommendations": True,
                "orders": True,
                "fills": True,
                "snapshot": True,
            },
        ),
    )
    result = value.evaluate(canary_activation_id=1)
    assert result.status == NO_CANARY_RUNS
    assert result.lifecycle_state == ACTIVE


def test_invalid_recommendation_lineage_is_retained_as_structural_evidence(
    monkeypatch,
):
    candidate = SimpleNamespace(id=10)
    recommendation = SimpleNamespace(
        id=11,
        analysis_run_id=12,
        universe_candidate_id=10,
        market="KRW-BTC",
        action="BUY",
        trade_ratio=Decimal("0.1"),
        recommended_amount_krw=Decimal("5000"),
        recommended_quantity=None,
        confidence=Decimal("0.5"),
        status="APPROVED",
        created_at=NOW,
        updated_at=NOW,
    )
    session = MagicMock()
    session.scalars.return_value = (recommendation,)
    session.get.return_value = SimpleNamespace(pipeline_run_id="pipeline")
    resolver = MagicMock()
    resolver.resolve.return_value = SimpleNamespace(
        mode="INVALID_CANARY_PROVENANCE",
        valid=False,
        activation=None,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.CanaryTradeProvenanceService",
        MagicMock(return_value=resolver),
    )
    evidence, rows, findings = service(session)._recommendation_evidence(
        SimpleNamespace(id=1),
        (candidate,),
        NOW,
        {"recommendation_id_ceiling": 100},
    )
    assert rows == (recommendation,)
    assert evidence["recommendations"][0]["provenance_valid"] is False
    assert findings == ["RECOMMENDATION_LINEAGE_INVALID:11"]


def test_legacy_alert_message_is_never_parsed_as_structured_reason():
    legacy = SimpleNamespace(
        id=1,
        alert_type="LIVE_CANARY_BUY_LIMIT_BLOCKED",
        error_code=None,
        pipeline_run_id=None,
        analysis_run_id=None,
        order_log_id=None,
        recommendation_id=10,
        delivery_status="SENT",
        resolved_at=None,
        created_at=NOW,
        safe_message="reason_code=PER_ORDER_LIMIT",
    )
    session = MagicMock()
    session.scalars.return_value = (legacy,)
    result = service(session)._operational_evidence(
        SimpleNamespace(id=1, user_id=2),
        (),
        (10,),
        (),
        NOW,
        {"operational_alert_id_ceiling": 1},
    )
    assert result["summary"]["per_order_limit_block_count"] == 0
    assert result["summary"]["legacy_unstructured_canary_alert_count"] == 1


def test_preflight_failure_before_canary_resolution_is_not_submission_or_corruption(
    monkeypatch,
):
    recommendation = SimpleNamespace(
        id=1,
        user_id=2,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
    )
    order = SimpleNamespace(
        id=3,
        recommendation_id=1,
        approval_request_id=4,
        user_id=2,
        trading_mode="LIVE",
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        amount_krw=Decimal("5000"),
        quantity=None,
        status="LIVE_FAILED",
        exchange_order_id=None,
        executed_quantity=None,
        executed_funds_krw=None,
        paid_fee=None,
        raw_response={
            "actual_order_executed": False,
            "preflight": {"result": "FAILED"},
        },
        created_at=NOW,
        updated_at=NOW,
    )
    session = MagicMock()
    session.scalars.return_value = (order,)
    resolver = MagicMock()
    resolver.resolve.return_value = SimpleNamespace(
        canary_run=SimpleNamespace(id=5),
        safety_binding=SimpleNamespace(binding_signature="binding"),
        per_order_buy_cap=Decimal("10000"),
        daily_buy_cap=Decimal("30000"),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.CanaryTradeProvenanceService",
        MagicMock(return_value=resolver),
    )
    evidence, _, findings = service(session)._order_evidence(
        SimpleNamespace(
            id=6,
            promotion_approval_id=7,
            activation_signature="activation",
            user_id=2,
        ),
        (recommendation,),
        NOW,
        {"order_log_id_ceiling": 10},
    )
    assert findings == []
    assert evidence["orders"][0]["preflight_failed_before_canary_audit"] is True
    assert service()._order_was_submitted(order) is False


def test_full_persisted_lifecycle_is_factual_read_only(monkeypatch):
    activation = SimpleNamespace(
        id=10,
        activation_signature="activation",
        candidate_id=20,
        promotion_approval_id=30,
        user_id=40,
        exchange="UPBIT",
        quote_asset="KRW",
        scenario_name="scenario",
        scenario_definition_signature="scenario-signature",
        baseline_policy_signature="baseline",
        canary_policy_signature="canary",
        effective_top_n=1,
        started_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        max_analysis_runs=6,
        created_at=NOW - timedelta(hours=1),
    )
    approval = SimpleNamespace(
        id=30,
        approval_signature="promotion",
        shadow_enrollment_id=31,
        review_decision_signature="review",
    )
    binding = SimpleNamespace(
        id=32,
        binding_signature="binding",
        order_safety_policy_schema_version="safety-v1",
        order_safety_policy_signature="safety-signature",
        max_buy_order_amount_krw=Decimal("10000"),
        daily_max_buy_amount_krw=Decimal("30000"),
    )
    run = SimpleNamespace(
        id=50,
        canary_activation_id=10,
        run_ordinal=1,
        analysis_run_id=60,
        pipeline_run_id="pipeline",
        reserved_at=NOW - timedelta(minutes=50),
        run_signature="run",
        baseline_policy_signature="baseline",
        canary_policy_signature="canary",
    )
    market_analysis = SimpleNamespace(
        id=60,
        user_id=40,
        pipeline_run_id="pipeline",
        run_type="MARKET_UNIVERSE",
        status="FAILED",
        created_at=NOW - timedelta(minutes=50),
        finished_at=NOW - timedelta(minutes=49),
    )
    ai_analysis = SimpleNamespace(
        id=61,
        user_id=40,
        pipeline_run_id="pipeline",
        run_type="AI_RECOMMENDATION",
    )
    ranked = SimpleNamespace(
        id=70,
        analysis_run_id=60,
        user_id=40,
        exchange="UPBIT",
        market="KRW-BTC",
        rank=1,
        score=Decimal("0.8"),
        selection_source="RANKED",
        buy_eligible=True,
        sell_eligible=True,
    )
    held = SimpleNamespace(
        id=71,
        analysis_run_id=60,
        user_id=40,
        exchange="UPBIT",
        market="KRW-ETH",
        rank=None,
        score=None,
        selection_source="HELD",
        buy_eligible=False,
        sell_eligible=True,
    )
    recommendation = SimpleNamespace(
        id=80,
        analysis_run_id=61,
        universe_candidate_id=70,
        user_id=40,
        exchange="UPBIT",
        market="KRW-BTC",
        action="BUY",
        trade_ratio=Decimal("0.1"),
        recommended_amount_krw=Decimal("10000"),
        recommended_quantity=None,
        confidence=Decimal("0.7"),
        status="APPROVED",
        created_at=NOW - timedelta(minutes=40),
        updated_at=NOW - timedelta(minutes=39),
    )
    approval_request = SimpleNamespace(
        id=90,
        recommendation_id=80,
        status="APPROVED",
        expires_at=NOW,
        approved_at=NOW - timedelta(minutes=35),
        rejected_at=None,
        created_at=NOW - timedelta(minutes=40),
        updated_at=NOW - timedelta(minutes=35),
    )
    canary_audit = {
        "mode": "CANARY_RECOMMENDATION",
        "canary_activation_id": 10,
        "canary_run_id": 50,
        "promotion_approval_id": 30,
        "activation_signature": "activation",
        "safety_binding_signature": "binding",
        "max_buy_order_amount_krw": "10000",
        "daily_max_buy_amount_krw": "30000",
    }
    order = SimpleNamespace(
        id=100,
        recommendation_id=80,
        approval_request_id=90,
        user_id=40,
        trading_mode="LIVE",
        exchange="UPBIT",
        market="KRW-BTC",
        side="BUY",
        amount_krw=Decimal("10000"),
        quantity=None,
        status="LIVE_DONE",
        exchange_order_id="remote",
        executed_quantity=Decimal("0.1"),
        executed_funds_krw=Decimal("10000"),
        paid_fee=Decimal("5"),
        raw_response={"canary": canary_audit},
        created_at=NOW - timedelta(minutes=30),
        updated_at=NOW - timedelta(minutes=29),
    )
    fill = OrderFill(
        id=110,
        order_log_id=100,
        exchange_trade_id="trade",
        price=Decimal("100000"),
        volume=Decimal("0.1"),
        funds_krw=Decimal("10000"),
        side="BUY",
        raw_data={},
        created_at=NOW - timedelta(minutes=29),
    )
    alert = SimpleNamespace(
        id=120,
        alert_type="LIVE_CANARY_BUY_LIMIT_BLOCKED",
        error_code="DAILY_LIMIT",
        pipeline_run_id=None,
        analysis_run_id=None,
        order_log_id=None,
        recommendation_id=80,
        delivery_status="SENT",
        resolved_at=None,
        created_at=NOW - timedelta(minutes=20),
    )
    outcome = SimpleNamespace(
        id=130,
        recommendation_id=80,
        horizon_minutes=60,
        reference_price=Decimal("100000"),
        end_price=Decimal("90000"),
        market_return_percentage=Decimal("-10"),
        action_aligned_return_percentage=Decimal("-10"),
        directional_result="LOSS",
        evaluation_status="COMPLETE",
        target_at=NOW - timedelta(minutes=10),
        evaluated_at=NOW - timedelta(minutes=9),
        created_at=NOW - timedelta(minutes=9),
    )
    before_portfolio = SimpleNamespace(
        id=140,
        pipeline_run_id="before",
        user_id=40,
        exchange="UPBIT",
        cash_total_krw=Decimal("100000"),
        priced_positions_value_krw=Decimal("0"),
        known_total_value_krw=Decimal("100000"),
        total_value_krw=Decimal("100000"),
        unrealized_pnl_krw=Decimal("0"),
        valuation_status="COMPLETE",
        valuation_policy_signature="valuation",
        captured_at=NOW - timedelta(hours=2),
    )
    run_portfolio = SimpleNamespace(
        **{
            **before_portfolio.__dict__,
            "id": 141,
            "pipeline_run_id": "pipeline",
            "total_value_krw": Decimal("99000"),
            "captured_at": NOW - timedelta(minutes=45),
        }
    )
    pnl = SimpleNamespace(
        accounting_status="COMPLETE",
        source_signature="pnl",
        source_order_count=1,
        recognized_realized_pnl_krw=Decimal("-5"),
        open_bot_cost_basis_krw=Decimal("10005"),
        calculated_at=NOW - timedelta(minutes=5),
    )
    provenance = SimpleNamespace(
        mode="CANARY_RECOMMENDATION",
        valid=True,
        activation=activation,
        canary_run=run,
        safety_binding=binding,
        per_order_buy_cap=Decimal("10000"),
        daily_buy_cap=Decimal("30000"),
    )
    session = MagicMock()
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    session.scalar.side_effect = [activation, pnl]
    session.scalars.side_effect = [
        (run,),
        (ranked, held),
        (recommendation,),
        (approval_request,),
        (order,),
        (fill,),
        (alert,),
        (outcome,),
        (before_portfolio, run_portfolio),
    ]
    session.get.side_effect = lambda model, row_id: {
        60: market_analysis,
        61: ai_analysis,
    }.get(row_id)
    value = service(session)
    monkeypatch.setattr(value, "_start_snapshot", lambda: "TEST_SNAPSHOT")
    monkeypatch.setattr(value, "_capture_evidence_as_of", lambda: NOW)
    monkeypatch.setattr(
        value,
        "_capture_ceilings",
        lambda _: {
            name: 1000
            for name in (
                "canary_run_id_ceiling",
                "recommendation_id_ceiling",
                "approval_request_id_ceiling",
                "order_log_id_ceiling",
                "order_fill_id_ceiling",
                "operational_alert_id_ceiling",
                "recommendation_outcome_id_ceiling",
                "portfolio_snapshot_id_ceiling",
            )
        },
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.validate_stored_canary_activation",
        lambda *_: (SimpleNamespace(row=approval), None, None),
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_canary_safety_binding",
        lambda *_: binding,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_canary_termination",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_live_policy_canary_run",
        lambda *_: (run, activation, binding, approval),
    )
    resolver = MagicMock()
    resolver.resolve.return_value = provenance
    monkeypatch.setattr(
        "crypto_trading_bot.services.live_canary_evidence_service.CanaryTradeProvenanceService",
        MagicMock(return_value=resolver),
    )

    result = value.evaluate(canary_activation_id=10)

    assert result.status == EVIDENCE_AVAILABLE
    assert result.payload["run_summary"]["failed_run_count"] == 1
    assert len(result.payload["selection_evidence"]["ranked_candidates"]) == 1
    assert len(result.payload["selection_evidence"]["held_only_candidates"]) == 1
    assert result.payload["approval_evidence"]["summary"]["approved_count"] == 1
    assert result.payload["order_evidence"]["summary"]["live_done_count"] == 1
    assert result.payload["fill_evidence"]["summary"]["fill_count"] == 1
    assert (
        result.payload["operational_evidence"]["summary"]["daily_limit_block_count"]
        == 1
    )
    assert (
        result.payload["recommendation_outcome_evidence"][
            "outcome_is_actual_execution_pnl"
        ]
        is False
    )
    assert result.payload["account_bot_pnl_context"]["canary_attributed"] is False
    assert result.payload["portfolio_context"]["portfolio_delta_is_canary_pnl"] is False
    assert result.payload["integrity"]["findings"] == []
    assert not session.new and not session.dirty and not session.deleted
