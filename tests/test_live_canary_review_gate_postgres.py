from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from crypto_trading_bot.db.database import SessionLocal
from crypto_trading_bot.db.models import OperationalAlert
from crypto_trading_bot.services.live_canary_evidence_service import (
    ACTIVE,
    EXHAUSTED,
    EXPIRED,
    NO_CANARY_RUNS,
    STOPPED,
)
from crypto_trading_bot.services.live_canary_review_gate_service import (
    ELIGIBLE_FOR_FULL_LIVE_REVIEW,
    INSUFFICIENT_DATA,
    INVALID_CANARY_DATA,
    NOT_ELIGIBLE,
    NO_CANARY,
    LiveCanaryReviewGateService,
)
from tests.test_live_canary_review_gate_service import (
    NOW,
    FakeEvidenceService,
    _payload,
    _report,
)


def test_postgresql_no_canary_review_uses_real_read_only_evidence_snapshot():
    with SessionLocal() as session:
        session.rollback()
        result = LiveCanaryReviewGateService(session, now_fn=lambda: NOW).evaluate(
            canary_activation_id=9_223_372_036_854_775_807
        )
        assert result.status == NO_CANARY
        assert result.evidence.snapshot_consistency == "REPEATABLE_READ_READ_ONLY"
        assert result.database_write is False
        assert result.external_calls is False
        session.rollback()


def _scenario(name):
    payload = _payload()
    status = None
    lifecycle = EXHAUSTED
    if name == "no_runs":
        status = NO_CANARY_RUNS
        lifecycle = ACTIVE
        payload["runs"] = []
        payload["run_summary"].update(
            reserved_run_count=0,
            successful_market_universe_run_count=0,
            failed_market_universe_run_count=0,
            unfinished_market_universe_run_count=0,
        )
        payload["recommendation_evidence"]["recommendations"] = []
    elif name == "active":
        lifecycle = ACTIVE
    elif name == "stopped":
        lifecycle = STOPPED
    elif name == "expired_insufficient":
        lifecycle = EXPIRED
        payload["runs"] = payload["runs"][:5]
        payload["run_summary"]["reserved_run_count"] = 5
        payload["run_summary"]["successful_market_universe_run_count"] = 5
        payload["recommendation_evidence"]["recommendations"] = payload[
            "recommendation_evidence"
        ]["recommendations"][:5]
    elif name == "failed_run":
        payload["runs"][0]["market_universe_analysis_status"] = "FAILED"
        payload["run_summary"]["successful_market_universe_run_count"] = 5
        payload["run_summary"]["failed_market_universe_run_count"] = 1
    elif name == "cap_bypass":
        payload["order_evidence"]["orders"][0]["amount_krw"] = Decimal("10001")
    elif name == "live_unknown":
        payload["order_evidence"]["summary"]["live_unknown_count"] = 1
    elif name == "ledger_corruption":
        payload["integrity"]["order_fill_integrity_verified"] = False
        payload["integrity"]["findings"] = ["ORDER_FILL_AGGREGATE_MISMATCH:301"]
    elif name == "short_span":
        for index, run in enumerate(payload["runs"]):
            run["reserved_at"] = NOW - timedelta(hours=5 - index)
    elif name in {"full_valid", "negative_financial"}:
        pass
    else:
        raise AssertionError(f"unknown scenario: {name}")
    arguments = {"lifecycle": lifecycle, "payload": payload}
    if status is not None:
        arguments["status"] = status
    return _report(**arguments)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    (
        ("no_runs", INSUFFICIENT_DATA),
        ("active", INSUFFICIENT_DATA),
        ("stopped", NOT_ELIGIBLE),
        ("expired_insufficient", INSUFFICIENT_DATA),
        ("failed_run", NOT_ELIGIBLE),
        ("short_span", INSUFFICIENT_DATA),
        ("full_valid", ELIGIBLE_FOR_FULL_LIVE_REVIEW),
        ("cap_bypass", NOT_ELIGIBLE),
        ("live_unknown", NOT_ELIGIBLE),
        ("ledger_corruption", INVALID_CANARY_DATA),
        ("negative_financial", ELIGIBLE_FOR_FULL_LIVE_REVIEW),
    ),
)
def test_postgresql_review_decisions_are_read_only(scenario, expected):
    with SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(OperationalAlert))
        session.rollback()
        evidence = FakeEvidenceService(_scenario(scenario))
        result = LiveCanaryReviewGateService(
            session,
            evidence_service=evidence,
            now_fn=lambda: NOW,
        ).evaluate(canary_activation_id=7)
        assert result.status == expected
        assert evidence.calls == [7]
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()
        after = session.scalar(select(func.count()).select_from(OperationalAlert))
        assert before == after
