from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.exc import IntegrityError

from crypto_trading_bot.db.database import SessionLocal, engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    FullLivePolicyActivation,
    OrderLog,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    ShadowPolicyPromotionApproval,
    ShadowReviewMonitorState,
    ShadowReviewNotification,
    StrategyReplayCandidateOutcome,
    TelegramPolicyReviewRequest,
    User,
)
from crypto_trading_bot.services.full_live_policy_activation_service import (
    FullLivePolicyActivationService,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ShadowPolicyPromotionApprovalError,
    load_and_validate_shadow_policy_promotion_approval,
    promotion_approval_signature,
)
from crypto_trading_bot.services.shadow_review_delivery_service import (
    ShadowReviewDeliveryService,
)
from crypto_trading_bot.services.shadow_review_monitor_service import (
    ShadowReviewMonitorResult,
)
from crypto_trading_bot.services.telegram_policy_review_service import (
    TelegramPolicyReviewService,
)
from crypto_trading_bot.services.telegram_policy_review_tokens import token_hash
from scripts import run_telegram_approval_listener as listener
from test_cost_adjusted_ranking_evaluation_postgres import _create_snapshot
from test_shadow_review_monitor_postgres import (
    approve_fixture,
    config,
    eligibility,
    enroll,
    gate_with_fixture,
    monitor,
)


ADMIN = 123456
OTHER_ADMIN = 654321


@pytest.fixture
def policy_user():
    with SessionLocal() as session:
        user = User(name=f"policy-approval-{uuid4()}")
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()
    yield user_id
    with SessionLocal() as session:
        notices = select(ShadowReviewNotification.id).where(
            ShadowReviewNotification.user_id == user_id
        )
        session.execute(
            delete(TelegramPolicyReviewRequest).where(
                TelegramPolicyReviewRequest.notification_id.in_(notices)
            )
        )
        for model in (
            ShadowPolicyPromotionApproval,
            ShadowReviewNotification,
            ShadowReviewMonitorState,
            ShadowPolicyEnrollment,
            ResearchPolicyCandidate,
            AnalysisRun,
        ):
            session.execute(delete(model).where(model.user_id == user_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def settings_for(user, **overrides):
    return config(
        user,
        **{
            "shadow_review_telegram_notifications_enabled": True,
            "policy_telegram_review_enabled": True,
            "policy_telegram_approval_enabled": True,
            "policy_telegram_allowed_user_ids": f"{ADMIN},{OTHER_ADMIN}",
            "telegram_chat_id": str(ADMIN),
            **overrides,
        },
    )


def row_for(candidate_id):
    with SessionLocal() as session:
        return session.scalar(
            select(TelegramPolicyReviewRequest).where(
                TelegramPolicyReviewRequest.candidate_id == candidate_id
            )
        )


def make_callback(data, *, actor=ADMIN, chat=ADMIN, message_id=77, chat_type="private"):
    return {
        "id": "test-callback",
        "data": data,
        "from": {"id": actor},
        "message": {"message_id": message_id, "chat": {"id": chat, "type": chat_type}},
    }


def flow(user_id, *, settings=None, fake=None):
    settings = settings or settings_for(user_id)
    _, candidate_id = enroll(user_id)
    now = SimpleNamespace(value=datetime.now(UTC) - timedelta(minutes=20))
    change = SimpleNamespace(kind=None, outcome_snapshot_id=None)

    def clock():
        return now.value

    def gate_factory(session):
        gate = gate_with_fixture(session, not_eligible=change.kind == "ineligible")
        gate.now_fn = clock
        evaluate = gate.performance_service.evaluate

        def performance(**kwargs):
            evidence = evaluate(**kwargs)
            evidence.performance_evidence_as_of = clock()
            if change.kind == "snapshot":
                evidence.shadow_evaluation_snapshot_id_ceiling += 1
            if change.kind == "outcome":
                evidence.gross[0].mean_return_delta *= 2
            if change.outcome_snapshot_id is not None:
                sample = evidence.gross[0].snapshots[0]
                sample.snapshot_id = change.outcome_snapshot_id
                sample.baseline_top_markets = ("KRW-A",)
            return evidence

        gate.performance_service.evaluate = performance
        if change.kind == "signature":
            evaluate_review = gate.evaluate
            gate.evaluate = lambda **kw: replace(
                evaluate_review(**kw), review_decision_signature="changed"
            )
        return gate

    check = eligibility(settings, gate_factory)
    monitor(settings, check=check).run_cycle(apply=True)
    telegram = fake or MagicMock()
    if fake is None:
        telegram.send_message.return_value = {
            "message_id": 77,
            "chat": {"id": int(settings.shadow_review_destination)},
        }
    delivery = ShadowReviewDeliveryService(
        SessionLocal,
        settings=settings,
        eligibility=check,
        now_fn=clock,
        telegram_factory=lambda **_: telegram,
    )
    result = ShadowReviewMonitorResult.from_settings(settings, apply=True)
    delivery.process_due(result)
    service = TelegramPolicyReviewService(
        SessionLocal, settings=settings, eligibility=check, now_fn=clock
    )
    markup = telegram.send_message.call_args.kwargs.get("reply_markup")
    initial = markup["inline_keyboard"][0][0]["callback_data"] if markup else None
    return SimpleNamespace(
        candidate_id=candidate_id,
        initial=initial,
        settings=settings,
        service=service,
        telegram=telegram,
        now=now,
        change=change,
        delivery=delivery,
    )


def confirm_details(value):
    result = value.service.process(make_callback(value.initial))
    assert result.request_status == "REVIEWED"
    assert (
        "BASELINE → 후보 가중치" in result.message
        and "Shadow 연구 근거" in result.message
    )
    assert "PASS " in result.message and "Review Policy Signature" in result.message
    assert result.review_signature_verified and not result.human_approval_recorded
    buttons = result.markup["inline_keyboard"][0]
    assert all(len(button["callback_data"].encode()) <= 64 for button in buttons)
    return [button["callback_data"] for button in buttons]


def count(model):
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("elapsed", [1, 300])
def test_clock_change_approves_once_and_full_live_preview_accepts_audit(
    policy_user, elapsed
):
    value = flow(policy_user)
    row = row_for(value.candidate_id)
    assert row.telegram_message_id == 77
    assert row.review_expires_at == value.now.value + timedelta(days=7)
    assert row.review_token_hash == token_hash("review", value.initial.split(":")[1])
    assert not row.evidence_fingerprint
    approvals = count(ShadowPolicyPromotionApproval)
    activations, orders = count(FullLivePolicyActivation), count(OrderLog)
    confirm, reject = confirm_details(value)
    reviewed = row_for(value.candidate_id)
    assert reviewed.authorized_telegram_user_id == ADMIN
    assert reviewed.confirmation_token_hash == token_hash(
        "decision", confirm.split(":")[1]
    )
    assert reviewed.confirmation_token_hash != reviewed.review_token_hash
    assert reviewed.confirmation_expires_at == value.now.value + timedelta(seconds=900)
    value.now.value += timedelta(seconds=elapsed)

    result = value.service.process(make_callback(confirm))
    assert result.request_status == "APPROVED"
    assert result.evidence_unchanged and result.approval_signature_verified
    assert result.human_approval_recorded and not result.live_policy_change
    assert result.live_orders_created == 0
    duplicate = value.service.process(make_callback(confirm))
    assert duplicate.already_processed and not duplicate.database_write
    assert value.service.process(make_callback(reject)).already_processed
    assert count(ShadowPolicyPromotionApproval) == approvals + 1
    row = row_for(value.candidate_id)
    with SessionLocal() as session:
        approval = load_and_validate_shadow_policy_promotion_approval(
            session, row.approval_id
        ).approval
        assert approval.approval_source == "TELEGRAM"
        assert (
            approval.approval_signature
            == promotion_approval_signature(approval)
            == row.approval_signature
        )
        assert approval.telegram_review_context["telegram_user_id"] == ADMIN
        assert (
            approval.telegram_review_context["evidence_fingerprint"]
            == row.evidence_fingerprint
        )
        preview = FullLivePolicyActivationService(
            session, settings=value.settings
        ).preview(
            promotion_approval_id=approval.id,
            user_id=policy_user,
            exchange="UPBIT",
            quote_asset="KRW",
        )
        assert preview.activation_status == "DRY_RUN", preview.safe_reason
        assert not preview.database_write
        audit = session.get(TelegramPolicyReviewRequest, row.id)
        audit.authorized_telegram_user_id = OTHER_ADMIN
        session.flush()
        with pytest.raises(ShadowPolicyPromotionApprovalError, match="audit"):
            load_and_validate_shadow_policy_promotion_approval(session, row.approval_id)
    assert count(FullLivePolicyActivation) == activations and count(OrderLog) == orders


@pytest.mark.parametrize(
    "change",
    ["snapshot", "outcome", "ineligible", "signature", "approval", "enrollment"],
)
def test_review_change_fails_closed(policy_user, change):
    value = flow(policy_user)
    confirm, _ = confirm_details(value)
    if change == "approval":
        approve_fixture(value.candidate_id)
    elif change == "enrollment":
        with SessionLocal() as session:
            row = session.get(
                ShadowPolicyEnrollment, row_for(value.candidate_id).shadow_enrollment_id
            )
            row.scenario_definition_signature = "tampered"
            session.commit()
    else:
        value.change.kind = change
    before = count(ShadowPolicyPromotionApproval)
    result = value.service.process(make_callback(confirm))
    assert result.request_status == "STALE"
    assert not result.human_approval_recorded
    assert count(ShadowPolicyPromotionApproval) == before


def test_selected_outcome_change_detected_even_when_aggregates_match(policy_user):
    value = flow(policy_user)
    with SessionLocal() as session:
        snapshot = _create_snapshot(
            session,
            session.get(User, policy_user),
            captured_at=datetime(2026, 2, 1, tzinfo=UTC),
            sequence=1,
        )
        outcome = session.scalar(
            select(StrategyReplayCandidateOutcome).where(
                StrategyReplayCandidateOutcome.strategy_replay_snapshot_id
                == snapshot.id,
                StrategyReplayCandidateOutcome.market == "KRW-A",
                StrategyReplayCandidateOutcome.horizon_minutes == 60,
            )
        )
        outcome.created_at = outcome.updated_at = value.now.value - timedelta(seconds=1)
        value.change.outcome_snapshot_id, outcome_id = snapshot.id, outcome.id
        session.commit()
    confirm, _ = confirm_details(value)
    value.now.value += timedelta(seconds=5)
    with SessionLocal() as session:
        outcome = session.get(StrategyReplayCandidateOutcome, outcome_id)
        outcome.market_return_percentage += 1
        outcome.updated_at = value.now.value
        session.commit()
    result = value.service.process(make_callback(confirm))
    assert result.request_status == "STALE"
    assert row_for(value.candidate_id).approval_id is None


def test_actor_message_and_token_are_bound_and_rejection_is_terminal(policy_user):
    value = flow(policy_user)
    for callback in (
        make_callback(value.initial, actor=ADMIN + 1),
        make_callback(value.initial, chat=-100999),
        make_callback(value.initial, message_id=78),
        make_callback(value.initial, chat_type="supergroup"),
        make_callback(value.initial + "x"),
        make_callback("policy_review:" + "X" * 43),
        make_callback(value.initial, actor=None),
    ):
        result = value.service.process(callback)
        assert result.request_status == "DENIED" and result.candidate_id is None
    assert row_for(value.candidate_id).status == "PENDING"
    confirm, reject = confirm_details(value)
    assert (
        value.service.process(make_callback(confirm, actor=OTHER_ADMIN)).request_status
        == "DENIED"
    )
    forged = confirm.replace("policy_confirm", "policy_review", 1)
    assert value.service.process(make_callback(forged)).request_status == "DENIED"
    assert value.service.process(make_callback(value.initial)).already_processed
    before = count(ShadowPolicyPromotionApproval)
    result = value.service.process(make_callback(reject))
    assert result.request_status == "REJECTED" and result.evidence_unchanged
    assert result.markup == {"inline_keyboard": []}
    assert value.service.process(make_callback(reject)).already_processed
    assert value.service.process(make_callback(confirm)).already_processed
    row = row_for(value.candidate_id)
    assert row.decided_at is not None and row.authorized_telegram_user_id == ADMIN
    assert row.approval_id is None and row.approval_signature is None
    assert count(ShadowPolicyPromotionApproval) == before


@pytest.mark.parametrize("stage", ["review", "decision"])
def test_expiration_blocks_at_exact_ttl(policy_user, stage):
    value = flow(policy_user)
    data = value.initial if stage == "review" else confirm_details(value)[0]
    row = row_for(value.candidate_id)
    value.now.value = (
        row.review_expires_at if stage == "review" else row.confirmation_expires_at
    )
    result = value.service.process(make_callback(data))
    assert result.request_status == "EXPIRED" and result.token_expired
    assert row_for(value.candidate_id).approval_id is None


def test_review_only_renders_evidence_without_confirmation(policy_user):
    value = flow(
        policy_user,
        settings=settings_for(policy_user, policy_telegram_approval_enabled=False),
    )
    result = value.service.process(make_callback(value.initial))
    assert result.request_status == "REVIEWED"
    assert result.markup == {"inline_keyboard": []}
    row = row_for(value.candidate_id)
    assert row.confirmation_token_hash is None and row.approval_id is None
    assert (
        value.service.process(
            make_callback("policy_confirm:" + "A" * 43)
        ).request_status
        == "DISABLED"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "policy_telegram_review_enabled": False,
            "policy_telegram_approval_enabled": False,
            "telegram_chat_id": "-100123",
        },
        {"policy_telegram_allowed_user_ids": ""},
    ],
)
def test_initial_alert_without_policy_buttons(policy_user, overrides):
    value = flow(policy_user, settings=settings_for(policy_user, **overrides))
    assert value.initial is None and row_for(value.candidate_id) is None


def test_outbox_uncertain_send_never_activates_button(policy_user):
    telegram = MagicMock()
    telegram.send_message.side_effect = httpx.ReadTimeout("synthetic private response")
    value = flow(policy_user, fake=telegram)
    row = row_for(value.candidate_id)
    assert row.status == "CANCELLED"
    assert row.review_expires_at is None and row.telegram_message_id is None
    assert (
        value.service.process(make_callback(value.initial)).request_status == "DENIED"
    )
    value.delivery.process_due(
        ShadowReviewMonitorResult.from_settings(value.settings, apply=True)
    )
    telegram.send_message.assert_called_once()


def test_retry_rotates_unbound_token_and_starts_ttl_at_receipt(policy_user):
    telegram = MagicMock()
    telegram.send_message.side_effect = [
        httpx.ConnectError("synthetic failure"),
        {"message_id": 77, "chat": {"id": ADMIN}},
    ]
    value = flow(policy_user, fake=telegram)
    before = row_for(value.candidate_id)
    assert before.status == "PENDING" and before.review_expires_at is None
    value.now.value += timedelta(minutes=5)
    value.delivery.process_due(
        ShadowReviewMonitorResult.from_settings(value.settings, apply=True)
    )
    after = row_for(value.candidate_id)
    assert before.id == after.id and before.review_token_hash != after.review_token_hash
    assert after.review_expires_at == value.now.value + timedelta(days=7)
    assert (
        value.service.process(make_callback(value.initial)).request_status == "DENIED"
    )


def test_concurrent_confirmation_only_creates_one_approval(policy_user):
    value = flow(policy_user)
    confirm, _ = confirm_details(value)
    with ThreadPoolExecutor(2) as pool:
        runs = [
            pool.submit(value.service.process, make_callback(confirm)) for _ in range(2)
        ]
        results = [run.result(timeout=15) for run in runs]
    assert sum(result.database_write for result in results) == 1
    assert sum(result.already_processed for result in results) == 1
    assert row_for(value.candidate_id).status == "APPROVED"


@pytest.mark.parametrize("failure", ["commit", "audit"])
def test_transaction_failure_rolls_back_approval_and_request(
    policy_user, monkeypatch, failure
):
    value = flow(policy_user)
    confirm, _ = confirm_details(value)
    before = count(ShadowPolicyPromotionApproval)
    if failure == "audit":
        monkeypatch.setattr(
            "crypto_trading_bot.services.telegram_policy_review_service.load_and_validate_shadow_policy_promotion_approval",
            MagicMock(side_effect=RuntimeError("synthetic audit failure")),
        )
    else:

        def fail_commit():
            session = SessionLocal()
            session.commit = MagicMock(
                side_effect=RuntimeError("synthetic commit failure")
            )
            return session

        value.service.session_factory = fail_commit
    with pytest.raises(RuntimeError):
        value.service.process(make_callback(confirm))
    assert row_for(value.candidate_id).status == "REVIEWED"
    assert count(ShadowPolicyPromotionApproval) == before


def test_listener_policy_callbacks_use_no_order_paths_and_edit_failure_is_safe(
    policy_user, monkeypatch, capsys
):
    value = flow(policy_user)
    fake = value.telegram
    monkeypatch.setattr(
        "crypto_trading_bot.notification.telegram_policy_callback.get_settings",
        lambda: value.settings,
    )
    monkeypatch.setattr(
        "crypto_trading_bot.notification.telegram_policy_callback.TelegramPolicyReviewService",
        lambda *_args, **_kwargs: value.service,
    )
    decision, execution, upbit = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(listener, "ApprovalDecisionService", decision)
    monkeypatch.setattr(listener, "ApprovedOrderExecutionService", execution)
    monkeypatch.setattr(listener, "UpbitClient", upbit)
    for payload in (value.initial, "policy_unknown:secret", "policy_confirm"):
        listener.process_callback_update(
            fake, {"callback_query": make_callback(payload)}
        )
    confirm = fake.edit_message_text.call_args.kwargs["reply_markup"][
        "inline_keyboard"
    ][0][0]["callback_data"]
    fake.edit_message_text.side_effect = RuntimeError("sensitive synthetic token")
    for _ in range(2):
        listener.process_callback_update(
            fake, {"callback_query": make_callback(confirm)}
        )
    assert row_for(value.candidate_id).status == "APPROVED"
    for call in (decision, execution, upbit):
        call.assert_not_called()
    output = capsys.readouterr().out
    assert "sensitive synthetic token" not in output and confirm not in output
    assert '"already_processed": true' in output


@pytest.mark.parametrize(
    ("side", "command", "expected_calls"),
    [
        ("BUY", "approve", 1),
        ("SELL", "approve", 1),
        ("SELL", "reject", 0),
        ("BUY", "unknown", 0),
    ],
)
def test_trade_callback_keeps_legacy_routing(
    monkeypatch, side, command, expected_calls
):
    fake = MagicMock()
    policy = MagicMock()
    decision = MagicMock()
    execution = MagicMock()
    decision.return_value.process_decision.return_value = SimpleNamespace(
        decision=command.upper(),
        already_processed=False,
        recommendation=SimpleNamespace(id=123, action=side),
        approval_request=SimpleNamespace(id=456),
    )
    execution.return_value.execute.return_value = listener.ApprovedOrderExecutionResult(
        execution_mode="MOCK"
    )
    monkeypatch.setattr(
        "crypto_trading_bot.notification.telegram_policy_callback.process_policy_callback",
        policy,
    )
    monkeypatch.setattr(listener, "ApprovalDecisionService", decision)
    monkeypatch.setattr(listener, "ApprovedOrderExecutionService", execution)
    listener.process_callback_update(
        fake, {"callback_query": make_callback(f"{command}:trade-test")}
    )
    policy.assert_not_called()
    assert execution.call_count == expected_calls
    if expected_calls:
        execution.return_value.execute.assert_called_once_with(
            recommendation_id=123, approval_request_id=456
        )
    if command == "unknown":
        decision.assert_not_called()
    else:
        decision.return_value.process_decision.assert_called_once()


def test_request_schema_enforces_status_and_token_constraints(policy_user):
    value = flow(policy_user)
    schema = inspect(engine)
    table = "telegram_policy_review_requests"
    assert len(schema.get_foreign_keys(table)) == 4
    assert {
        tuple(key["column_names"]) for key in schema.get_unique_constraints(table)
    } >= {
        ("review_token_hash",),
        ("confirmation_token_hash",),
        ("notification_id",),
        ("approval_id",),
    }
    with SessionLocal() as session:
        row = session.get(TelegramPolicyReviewRequest, row_for(value.candidate_id).id)
        row.status = "INVALID"
        with pytest.raises(IntegrityError):
            session.flush()
