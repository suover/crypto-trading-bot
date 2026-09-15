from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal, engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ApprovalRequest,
    MarketUniverseCandidate,
    OperationalAlert,
    OrderFill,
    OrderLog,
    TradeRecommendation,
    TradeRecommendationOutcome,
    User,
)
from crypto_trading_bot.services.live_canary_evidence_service import (
    NO_CANARY_ACTIVATION,
    LiveCanaryEvidenceService,
)


def test_postgresql_no_activation_evidence_is_read_only_and_factual():
    with SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(OperationalAlert))
        session.rollback()
        result = LiveCanaryEvidenceService(session).evaluate(
            canary_activation_id=9_223_372_036_854_775_807
        )
        assert result.status == NO_CANARY_ACTIVATION
        assert result.snapshot_consistency == "REPEATABLE_READ_READ_ONLY"
        assert result.payload["safety_flags"]["database_write"] is False
        assert not session.new and not session.dirty and not session.deleted
        session.rollback()
        after = session.scalar(select(func.count()).select_from(OperationalAlert))
        assert before == after


def test_postgresql_evidence_snapshot_does_not_mix_concurrent_insert():
    dedup_key = f"LIVE_CANARY_EVIDENCE_SNAPSHOT_TEST:{uuid4()}"
    with SessionLocal() as evidence_session:
        service = LiveCanaryEvidenceService(evidence_session)
        assert service._start_snapshot() == "REPEATABLE_READ_READ_ONLY"
        first = evidence_session.scalar(
            select(func.count())
            .select_from(OperationalAlert)
            .where(OperationalAlert.dedup_key == dedup_key)
        )
        with SessionLocal() as writer:
            writer.add(
                OperationalAlert(
                    alert_type="LIVE_CANARY_STARTED",
                    severity="WARNING",
                    user_id=None,
                    error_code="CANARY_STARTED",
                    safe_message="snapshot test",
                    dedup_key=dedup_key,
                    delivery_status="PENDING",
                    delivery_attempt_count=0,
                )
            )
            writer.commit()
        still_frozen = evidence_session.scalar(
            select(func.count())
            .select_from(OperationalAlert)
            .where(OperationalAlert.dedup_key == dedup_key)
        )
        assert first == still_frozen == 0
        evidence_session.rollback()

    try:
        with SessionLocal() as fresh:
            assert (
                fresh.scalar(
                    select(func.count())
                    .select_from(OperationalAlert)
                    .where(OperationalAlert.dedup_key == dedup_key)
                )
                == 1
            )
    finally:
        with SessionLocal() as cleanup:
            cleanup.execute(
                delete(OperationalAlert).where(OperationalAlert.dedup_key == dedup_key)
            )
            cleanup.commit()


def test_postgresql_evidence_transaction_rejects_database_writes():
    with SessionLocal() as session:
        LiveCanaryEvidenceService(session)._start_snapshot()
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "INSERT INTO operational_alerts "
                    "(alert_type, severity, safe_message, dedup_key, "
                    "delivery_status, delivery_attempt_count) "
                    "VALUES ('LIVE_CANARY_STARTED', 'WARNING', 'blocked', "
                    "'READ_ONLY_MUST_BLOCK', 'PENDING', 0)"
                )
            )
        session.rollback()


def test_postgresql_persisted_source_collection_is_read_only(monkeypatch):
    with engine.connect() as connection:
        transaction = connection.begin()
        sessions = sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            with sessions() as session:
                now = datetime.now(UTC)
                user = User(name=f"canary-evidence-e2e-{uuid4()}")
                session.add(user)
                session.flush()
                market_run = AnalysisRun(
                    user_id=user.id,
                    pipeline_run_id=str(uuid4()),
                    run_type="MARKET_UNIVERSE",
                    trading_mode="AI_APPROVAL",
                    status="FAILED",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                )
                ai_run = AnalysisRun(
                    user_id=user.id,
                    pipeline_run_id=market_run.pipeline_run_id,
                    run_type="AI_RECOMMENDATION",
                    trading_mode="AI_APPROVAL",
                    status="SUCCESS",
                    started_at=now - timedelta(minutes=8),
                    finished_at=now - timedelta(minutes=7),
                )
                session.add_all((market_run, ai_run))
                session.flush()
                candidate = MarketUniverseCandidate(
                    analysis_run_id=market_run.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market="KRW-BTC",
                    base_asset="BTC",
                    quote_asset="KRW",
                    rank=1,
                    score=Decimal("0.8"),
                    selection_source="RANKED",
                    buy_eligible=True,
                    sell_eligible=True,
                    quote_trade_value_24h=Decimal("1000000000"),
                    market_event_data={},
                    feature_data={},
                )
                session.add(candidate)
                session.flush()
                recommendation = TradeRecommendation(
                    analysis_run_id=ai_run.id,
                    market_snapshot_id=None,
                    universe_candidate_id=candidate.id,
                    user_id=user.id,
                    exchange="UPBIT",
                    market="KRW-BTC",
                    action="BUY",
                    trade_ratio=Decimal("0.1"),
                    confidence=Decimal("0.7"),
                    reason="test",
                    recommended_amount_krw=Decimal("10000"),
                    recommended_quantity=None,
                    ai_model="TEST",
                    ai_response={},
                    status="APPROVED",
                )
                session.add(recommendation)
                session.flush()
                approval_request = ApprovalRequest(
                    recommendation_id=recommendation.id,
                    user_id=user.id,
                    status="APPROVED",
                    callback_token=f"token-{uuid4()}",
                    expires_at=now + timedelta(hours=1),
                    approved_at=now,
                )
                session.add(approval_request)
                session.flush()
                activation = SimpleNamespace(
                    id=901,
                    activation_signature="activation",
                    promotion_approval_id=902,
                    candidate_id=903,
                    user_id=user.id,
                    exchange="UPBIT",
                    quote_asset="KRW",
                    scenario_name="scenario",
                    scenario_definition_signature="scenario-signature",
                    baseline_policy_signature="baseline",
                    canary_policy_signature="canary",
                    effective_top_n=1,
                    started_at=now - timedelta(hours=1),
                    expires_at=now + timedelta(hours=1),
                    max_analysis_runs=6,
                )
                binding = SimpleNamespace(
                    id=904,
                    binding_signature="binding",
                    order_safety_policy_schema_version="safety-v1",
                    order_safety_policy_signature="safety-signature",
                    max_buy_order_amount_krw=Decimal("10000"),
                    daily_max_buy_amount_krw=Decimal("30000"),
                )
                run = SimpleNamespace(
                    id=905,
                    canary_activation_id=activation.id,
                    run_ordinal=1,
                    analysis_run_id=market_run.id,
                    pipeline_run_id=market_run.pipeline_run_id,
                    reserved_at=now - timedelta(minutes=11),
                    run_signature="run",
                    baseline_policy_signature="baseline",
                    canary_policy_signature="canary",
                )
                promotion = SimpleNamespace(
                    id=activation.promotion_approval_id,
                    approval_signature="promotion",
                    shadow_enrollment_id=906,
                    review_decision_signature="review",
                )
                canary_audit = {
                    "mode": "CANARY_RECOMMENDATION",
                    "canary_activation_id": activation.id,
                    "canary_run_id": run.id,
                    "promotion_approval_id": activation.promotion_approval_id,
                    "activation_signature": activation.activation_signature,
                    "safety_binding_signature": binding.binding_signature,
                    "max_buy_order_amount_krw": "10000",
                    "daily_max_buy_amount_krw": "30000",
                }
                order = OrderLog(
                    recommendation_id=recommendation.id,
                    approval_request_id=approval_request.id,
                    user_id=user.id,
                    trading_mode="LIVE",
                    exchange="UPBIT",
                    market="KRW-BTC",
                    side="BUY",
                    order_type="MARKET",
                    amount_krw=Decimal("10000"),
                    quantity=None,
                    status="LIVE_DONE",
                    exchange_order_id=f"order-{uuid4()}",
                    raw_response={"canary": canary_audit},
                    executed_quantity=Decimal("0.1"),
                    executed_funds_krw=Decimal("10000"),
                    average_execution_price=Decimal("100000"),
                    paid_fee=Decimal("5"),
                    remaining_quantity=Decimal("0"),
                    trades_count=1,
                    execution_synced_at=now,
                )
                session.add(order)
                session.flush()
                session.add_all(
                    (
                        OrderFill(
                            order_log_id=order.id,
                            exchange_trade_id=f"fill-{uuid4()}",
                            price=Decimal("100000"),
                            volume=Decimal("0.1"),
                            funds_krw=Decimal("10000"),
                            side="BUY",
                            raw_data={},
                        ),
                        OperationalAlert(
                            alert_type="LIVE_CANARY_BUY_LIMIT_BLOCKED",
                            severity="CRITICAL",
                            user_id=user.id,
                            recommendation_id=recommendation.id,
                            error_code="DAILY_LIMIT",
                            safe_message="safe",
                            dedup_key=f"evidence-e2e-{uuid4()}",
                            delivery_status="SENT",
                            delivery_attempt_count=1,
                        ),
                        TradeRecommendationOutcome(
                            recommendation_id=recommendation.id,
                            user_id=user.id,
                            exchange="UPBIT",
                            market="KRW-BTC",
                            horizon_minutes=60,
                            recommendation_at=now - timedelta(minutes=70),
                            target_at=now - timedelta(minutes=10),
                            reference_price=Decimal("100000"),
                            reference_price_source="MARKET_SNAPSHOT",
                            end_price=Decimal("90000"),
                            end_price_at=now - timedelta(minutes=10),
                            end_price_source="MARKET_CANDLE",
                            market_return_percentage=Decimal("-10"),
                            action_aligned_return_percentage=Decimal("-10"),
                            directional_result="LOSS",
                            evaluation_status="COMPLETE",
                            safe_reason=None,
                            evaluated_at=now - timedelta(minutes=9),
                        ),
                    )
                )
                session.flush()
                provenance = SimpleNamespace(
                    mode="CANARY_RECOMMENDATION",
                    valid=True,
                    activation=activation,
                    canary_run=run,
                    safety_binding=binding,
                    per_order_buy_cap=Decimal("10000"),
                    daily_buy_cap=Decimal("30000"),
                )
                resolver = MagicMock()
                resolver.resolve.return_value = provenance
                monkeypatch.setattr(
                    "crypto_trading_bot.services.live_canary_evidence_service.CanaryTradeProvenanceService",
                    MagicMock(return_value=resolver),
                )
                monkeypatch.setattr(
                    "crypto_trading_bot.services.live_canary_evidence_service.load_and_validate_live_policy_canary_run",
                    lambda *_: (run, activation, binding, promotion),
                )
                service = LiveCanaryEvidenceService(
                    session,
                    settings=Settings(
                        _env_file=None,
                        database_url="postgresql://test:test@localhost/test",
                        market_universe_mode="DYNAMIC",
                        market_universe_top_n=1,
                    ),
                )
                ceilings = {
                    "recommendation_id_ceiling": recommendation.id,
                    "approval_request_id_ceiling": approval_request.id,
                    "order_log_id_ceiling": order.id,
                    "order_fill_id_ceiling": session.scalar(
                        select(func.max(OrderFill.id))
                    ),
                    "operational_alert_id_ceiling": session.scalar(
                        select(func.max(OperationalAlert.id))
                    ),
                    "recommendation_outcome_id_ceiling": session.scalar(
                        select(func.max(TradeRecommendationOutcome.id))
                    ),
                    "portfolio_snapshot_id_ceiling": None,
                }
                clean_state = (
                    len(session.new),
                    len(session.dirty),
                    len(session.deleted),
                )
                payload, findings, checks = service._collect(
                    activation=activation,
                    approval=promotion,
                    binding=binding,
                    termination=None,
                    runs=(run,),
                    evidence_as_of=now + timedelta(minutes=1),
                    ceilings=ceilings,
                )
                assert findings == []
                assert all(checks.values())
                assert payload["run_summary"]["failed_run_count"] == 1
                assert payload["approval_evidence"]["summary"]["approved_count"] == 1
                assert payload["order_evidence"]["summary"]["live_done_count"] == 1
                assert payload["fill_evidence"]["summary"]["fill_count"] == 1
                assert (
                    payload["operational_evidence"]["summary"][
                        "daily_limit_block_count"
                    ]
                    == 1
                )
                assert (
                    payload["recommendation_outcome_evidence"][
                        "recommendation_outcome_count"
                    ]
                    == 1
                )
                assert clean_state == (
                    len(session.new),
                    len(session.dirty),
                    len(session.deleted),
                )
        finally:
            transaction.rollback()
