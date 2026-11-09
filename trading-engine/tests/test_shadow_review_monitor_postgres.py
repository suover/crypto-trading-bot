from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from crypto_trading_bot.config.settings import Settings
from crypto_trading_bot.db.database import SessionLocal, engine
from crypto_trading_bot.db.models import (
    AnalysisRun,
    ResearchPolicyCandidate,
    ShadowPolicyEnrollment,
    ShadowPolicyPromotionApproval,
    ShadowReviewMonitorState,
    ShadowReviewNotification,
    User,
)
from crypto_trading_bot.db.postgres_advisory_lock import PostgresAdvisoryLock
from crypto_trading_bot.services.forward_candidate_provenance import (
    load_and_validate_forward_candidate,
)
from crypto_trading_bot.services.research_policy_candidate_registry_service import (
    ResearchPolicyCandidateRegistryService,
)
from crypto_trading_bot.services.shadow_policy_enrollment_service import (
    ShadowPolicyEnrollmentService,
)
from crypto_trading_bot.services.shadow_policy_promotion_approval_service import (
    ShadowPolicyPromotionApprovalService,
    promotion_approval_signature,
)
from crypto_trading_bot.services.shadow_review_delivery_service import (
    ShadowReviewDeliveryService,
)
from crypto_trading_bot.services.shadow_review_gate_service import (
    ELIGIBLE_FOR_PROMOTION_REVIEW,
    ShadowReviewGateService,
    shadow_review_decision_signature,
)
from crypto_trading_bot.services.shadow_review_monitor_service import (
    ShadowReviewEligibility,
    ShadowReviewMonitorResult,
    ShadowReviewMonitorService,
)
from scripts import run_shadow_review_monitor_worker as worker
from test_research_policy_candidate_registry_postgres import (
    _policy_data,
    _scenario,
    _snapshot,
)
from test_shadow_policy_enrollment_service import _gate
from test_shadow_review_gate_service import performance


START = datetime(2026, 1, 1, tzinfo=UTC)
DESTINATION = "-100123"
TOKEN = "12345:" + "a" * 30


def config(user, **overrides):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql://test:test@localhost/test",
            "trading_user_id": user,
            "market_universe_mode": "DYNAMIC",
            "market_universe_top_n": 7,
            "market_universe_prefilter_n": 10,
            "shadow_review_monitor_enabled": True,
            "telegram_chat_id": DESTINATION,
            "telegram_bot_token": TOKEN,
            **overrides,
        },
    )


@pytest.fixture
def users():
    ids = []

    def create():
        with SessionLocal() as session:
            user = User(name=f"shadow-monitor-{uuid4()}")
            session.add(user)
            session.flush()
            ids.append(user.id)
            session.commit()
            return ids[-1]

    yield create
    with SessionLocal() as session:
        for model in (
            ShadowReviewNotification,
            ShadowReviewMonitorState,
            ShadowPolicyPromotionApproval,
            ShadowPolicyEnrollment,
            ResearchPolicyCandidate,
            AnalysisRun,
        ):
            session.execute(delete(model).where(model.user_id.in_(ids)))
        session.execute(delete(User).where(User.id.in_(ids)))
        session.commit()


def enroll(user_id, index=0):
    start = START + timedelta(days=index * 5)
    with SessionLocal() as session:
        user = session.get(User, user_id)
        reference = _snapshot(
            session, user, captured_at=start, policy_data=_policy_data()
        )
        momentum = Decimal("0.15") + Decimal("0.01") * index
        candidate = (
            ResearchPolicyCandidateRegistryService(
                session, now_fn=lambda: start + timedelta(days=1)
            )
            .register(
                reference_snapshot_id=reference.id,
                scenario=_scenario(
                    f"candidate-{index}",
                    momentum=str(momentum),
                    liquidity=str(Decimal("0.50") - momentum),
                ),
            )
            .candidate
        )
        latest = _snapshot(
            session,
            user,
            captured_at=start + timedelta(days=2),
            policy_data=_policy_data(),
        )
        gate = _gate(
            candidate=load_and_validate_forward_candidate(
                session, candidate.id
            ).metadata,
            status="ELIGIBLE_FOR_REVIEW",
            evaluated_at=start + timedelta(days=3),
            forward_snapshot_id_ceiling=latest.id,
        )
        result = ShadowPolicyEnrollmentService(
            session,
            gate_service=SimpleNamespace(evaluate=lambda **_: gate),
            now_fn=lambda: start + timedelta(days=4),
        ).enroll(candidate_id=candidate.id)
        assert result.enrollment_status == "CREATED"
        ids = result.enrollment.id, candidate.id
        session.commit()
        return ids


def gate_with_fixture(session, *, not_eligible=False):
    def evidence(*, candidate_id, **kwargs):
        value = performance()
        row = session.scalar(
            select(ShadowPolicyEnrollment).where(
                ShadowPolicyEnrollment.candidate_id == candidate_id
            )
        )
        value.candidate_id = candidate_id
        value.enrollment = row
        offset = row.shadow_snapshot_id_watermark
        ids = tuple(offset + item for item in value.timeline_snapshot_ids)
        value.shadow_evaluation_snapshot_id_ceiling = ids[-1]
        for key in (
            "timeline_snapshot_ids",
            "candidate_context_snapshot_ids",
            "successful_selection_snapshot_ids",
        ):
            setattr(value, key, ids)
            setattr(value.turnover, key, ids)
        value.turnover.transitions = tuple(
            SimpleNamespace(previous_snapshot_id=a, current_snapshot_id=b)
            for a, b in zip(ids, ids[1:])
        )
        for item in value.gross:
            item.snapshots = tuple(
                SimpleNamespace(snapshot_id=i)
                for i in ids[: item.successful_comparable_snapshot_count]
            )
        for item in value.cost_adjusted:
            item.cost_adjustable_shadow_snapshot_ids = ids[
                : item.cost_adjustable_shadow_snapshot_count
            ]
            if not_eligible:
                item.mean_cost_adjusted_return_delta = Decimal("-0.1")
        return value

    return ShadowReviewGateService(
        session, performance_service=SimpleNamespace(evaluate=evidence)
    )


def eligibility(settings, factory=gate_with_fixture):
    return ShadowReviewEligibility(settings, gate_factory=factory)


def monitor(settings, *, check=None, session_factory=SessionLocal):
    return ShadowReviewMonitorService(
        session_factory, settings=settings, eligibility=check or eligibility(settings)
    )


def notifications(user):
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ShadowReviewNotification)
                .where(ShadowReviewNotification.user_id == user)
                .order_by(ShadowReviewNotification.id)
            )
        )


def approve_fixture(candidate_id):
    with SessionLocal() as session:
        review = gate_with_fixture(session).evaluate(candidate_id=candidate_id)
        assert review.status == ELIGIBLE_FOR_PROMOTION_REVIEW
        # An existing human approval is test data, not a worker action.
        row = ShadowPolicyPromotionApprovalService(session)._build_approval(
            review, datetime.now(UTC)
        )
        row.approval_signature = promotion_approval_signature(row)
        session.add(row)
        session.commit()


def receipt():
    return {"message_id": 77, "chat": {"id": int(DESTINATION)}}


def deliver(
    settings, fake, *, check=None, session_factory=SessionLocal, now=None, report=None
):
    service = ShadowReviewDeliveryService(
        session_factory,
        settings=settings,
        telegram_factory=lambda **_: fake,
        eligibility=check or eligibility(settings),
        **({"now_fn": now} if now else {}),
    )
    result = report or ShadowReviewMonitorResult.from_settings(settings, apply=True)
    service.process_due(result)
    return result


def test_real_gate_no_evaluations_and_read_only_preview(users):
    user = users()
    settings = config(user)
    service = ShadowReviewMonitorService(SessionLocal, settings=settings)
    assert service.run_cycle().inspected_enrollment_count == 0
    enroll(user)
    result = service.run_cycle()
    assert result.insufficient_candidate_count == 1
    assert not result.database_write
    assert not notifications(user)


def test_worker_end_to_end_preview_outbox_and_fake_telegram(users, monkeypatch, capsys):
    user = users()
    enrollment_id, candidate_id = enroll(user)
    settings = config(user, shadow_review_telegram_notifications_enabled=True)
    real_evaluate = ShadowReviewGateService.evaluate
    transaction_modes = []

    def evaluate(self, *, candidate_id):
        transaction_modes.append(
            self.session.scalar(text("SHOW transaction_read_only"))
        )
        return real_evaluate(gate_with_fixture(self.session), candidate_id=candidate_id)

    monkeypatch.setattr(ShadowReviewGateService, "evaluate", evaluate)
    factory = MagicMock()
    assert (
        worker.run_worker(once=True, settings=settings, telegram_factory=factory) == 0
    )
    assert not notifications(user)
    assert transaction_modes == ["on"]
    factory.assert_not_called()
    assert '"eligible_candidate_count": 1' in capsys.readouterr().out

    off = config(user)
    assert (
        worker.run_worker(once=True, apply=True, settings=off, telegram_factory=factory)
        == 0
    )
    assert notifications(user)[0].delivery_status == "PENDING"
    factory.assert_not_called()

    def send(**kwargs):
        (claimed,) = notifications(user)
        assert claimed.delivery_status == "SENDING"
        assert claimed.delivery_attempt_count == 1
        assert kwargs["chat_id"] == DESTINATION
        assert f"Candidate ID: {candidate_id}" in kwargs["text"]
        assert f"Shadow Enrollment ID: {enrollment_id}" in kwargs["text"]
        assert "Shadow Evidence 기준 시각" in kwargs["text"]
        assert "미승인" in kwargs["text"]
        assert len(kwargs["text"].encode("utf-16-le")) // 2 <= 4096
        assert set(kwargs) == {"chat_id", "text"}
        return receipt()

    factory.return_value.send_message.side_effect = send
    assert (
        worker.run_worker(
            once=True, apply=True, settings=settings, telegram_factory=factory
        )
        == 0
    )
    (row,) = notifications(user)
    assert row.delivery_status == "SENT" and row.telegram_message_id == 77
    assert row.sent_at is not None and row.next_retry_at is None
    assert (
        worker.run_worker(
            once=True, apply=True, settings=settings, telegram_factory=factory
        )
        == 0
    )
    factory.assert_called_once()
    factory.return_value.send_message.assert_called_once()
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert (
        '"human_approval_created": false' in output
        and '"live_orders_created": 0' in output
    )


@pytest.mark.parametrize(
    "problem",
    [
        "candidate",
        "enrollment",
        "user",
        "baseline",
        "signature",
        "provenance",
        "checks",
        "invalid",
        "no_enrollment",
        "not_eligible",
    ],
)
def test_gate_result_contract_fails_closed(users, problem):
    user = users()
    enroll(user)
    settings = config(user)

    def factory(session):
        def evaluate(*, candidate_id):
            review = gate_with_fixture(
                session, not_eligible=problem == "not_eligible"
            ).evaluate(candidate_id=candidate_id)
            if problem == "not_eligible":
                return review
            changes = {
                "candidate": {"candidate_id": candidate_id + 1},
                "signature": {"review_decision_signature": "invalid"},
                "provenance": {"shadow_performance_provenance_verified": False},
                "checks": {"passed_checks": ()},
                "invalid": {"status": "INVALID_REVIEW_DATA"},
                "no_enrollment": {"status": "NO_SHADOW_ENROLLMENT"},
            }
            if problem in changes:
                return replace(review, **changes[problem])
            row = SimpleNamespace(
                **{
                    column.name: getattr(review.enrollment, column.name)
                    for column in ShadowPolicyEnrollment.__table__.columns
                }
            )
            if problem == "enrollment":
                row.id += 1
            elif problem == "user":
                row.user_id += 1
            else:
                row.baseline_policy_signature = "other-baseline"
            evidence = SimpleNamespace(**vars(review.performance))
            evidence.enrollment = row
            review = replace(review, enrollment=row, performance=evidence)
            return replace(
                review,
                review_decision_signature=shadow_review_decision_signature(review),
            )

        return SimpleNamespace(evaluate=evaluate)

    result = monitor(settings, check=eligibility(settings, factory)).run_cycle(
        apply=True
    )
    assert not notifications(user)
    if problem == "no_enrollment":
        assert result.no_shadow_enrollment_count == 1
    elif problem == "not_eligible":
        assert result.not_eligible_candidate_count == 1
    else:
        assert result.invalid_candidate_count == 1 and result.status == "PARTIAL"


@pytest.mark.parametrize(
    "change", ["approval", "ineligible", "full_live", "destination"]
)
def test_committed_outbox_requires_current_authority(users, monkeypatch, change):
    user = users()
    _, candidate_id = enroll(user)
    monitor(config(user)).run_cycle(apply=True)
    kwargs = {}
    if change == "approval":
        approve_fixture(candidate_id)
    elif change == "full_live":
        monkeypatch.setattr(
            "crypto_trading_bot.services.shadow_review_monitor_service.LiveRankingPolicyResolver.inspect",
            lambda *_args, **_kw: SimpleNamespace(
                mode="FULL_LIVE", baseline_policy_signature="other"
            ),
        )
    elif change == "destination":
        kwargs["telegram_chat_id"] = "-100456"

    settings = config(user, shadow_review_telegram_notifications_enabled=True, **kwargs)
    check = eligibility(
        settings,
        lambda session: gate_with_fixture(session, not_eligible=change == "ineligible"),
    )
    fake = MagicMock()
    report = deliver(settings, fake, check=check)
    assert not report.external_calls
    fake.send_message.assert_not_called()
    (row,) = notifications(user)
    assert row.delivery_attempt_count == 0
    assert row.delivery_status == (
        "PENDING" if change == "destination" else "SUPPRESSED"
    )
    assert (
        row.safe_failure_code
        == {
            "approval": "ALREADY_APPROVED",
            "ineligible": "NOT_ELIGIBLE",
            "full_live": "CONTEXT_BLOCKED",
            "destination": None,
        }[change]
    )


def test_existing_approval_skips_gate_and_does_not_create_outbox(users):
    user = users()
    _, candidate_id = enroll(user)
    approve_fixture(candidate_id)
    settings = config(user)
    factory = MagicMock()
    report = monitor(settings, check=eligibility(settings, factory)).run_cycle(
        apply=True
    )
    assert report.already_approved_candidate_count == 1
    factory.assert_not_called()
    assert not notifications(user)


def test_approval_lookup_failure_leaves_outbox_unsent(users, monkeypatch):
    user = users()
    enroll(user)
    monitor(config(user)).run_cycle(apply=True)

    def failed_lookup(*_):
        raise OperationalError("query", {}, RuntimeError("private"))

    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_review_monitor_service.approval_exists",
        failed_lookup,
    )
    fake = MagicMock()
    settings = config(user, shadow_review_telegram_notifications_enabled=True)
    with pytest.raises(OperationalError):
        deliver(settings, fake)
    fake.send_message.assert_not_called()
    assert notifications(user)[0].delivery_status == "PENDING"


def test_old_policy_notification_does_not_consume_new_policy_key(users):
    user = users()
    enroll(user)
    settings = config(user)
    monitor(settings).run_cycle(apply=True)
    with SessionLocal() as session:
        row = session.get(ShadowReviewNotification, notifications(user)[0].id)
        row.review_policy_signature = "older-policy-signature"
        session.commit()

    assert monitor(settings).run_cycle(apply=True).created_notification_count == 1
    assert len(notifications(user)) == 2
    assert monitor(settings).run_cycle(apply=True).existing_notification_count == 1


def test_cursor_resumes_and_advances_past_deleted_tail_and_new_enrollment(users):
    user = users()
    first, _ = enroll(user)
    second, _ = enroll(user, 1)
    other = users()
    enroll(other)
    settings = config(user, shadow_review_monitor_batch_size=1)
    result = monitor(settings).run_cycle(apply=True)
    assert (result.cursor_enrollment_id, result.sweep_enrollment_id_ceiling) == (
        first,
        second,
    )
    assert monitor(settings).run_cycle().cursor_enrollment_id == second
    third, _ = enroll(user, 2)
    with SessionLocal() as session:
        session.execute(
            delete(ShadowPolicyEnrollment).where(ShadowPolicyEnrollment.id == second)
        )
        session.commit()

    skipped = monitor(settings).run_cycle(apply=True)
    assert skipped.inspected_enrollment_count == 0
    assert skipped.cursor_enrollment_id == second
    assert monitor(settings).run_cycle(apply=True).cursor_enrollment_id == first
    result = monitor(settings).run_cycle(apply=True)
    assert result.cursor_enrollment_id == third
    assert [row.shadow_enrollment_id for row in notifications(user)] == [first, third]
    assert not notifications(other)


def test_candidate_failure_isolated_but_database_error_rolls_back(users):
    user = users()
    _, first = enroll(user)
    enroll(user, 1)
    settings = config(user)
    checker = eligibility(settings)

    def evaluate(session, row, context):
        if row.candidate_id == first:
            raise RuntimeError("fake token must not be logged")
        return checker.evaluate(session, row, context)

    result = monitor(settings, check=SimpleNamespace(evaluate=evaluate)).run_cycle(
        apply=True
    )
    assert result.status == "PARTIAL" and result.execution_error_count == 1
    assert result.created_notification_count == 1
    assert "fake token" not in repr(result)

    def database_error(*_):
        raise OperationalError("query", {}, Exception("private value"))

    with pytest.raises(OperationalError):
        monitor(settings, check=SimpleNamespace(evaluate=database_error)).run_cycle(
            apply=True
        )
    assert len(notifications(user)) == 1


def test_outbox_commit_failure_rolls_back_notification_and_cursor(users):
    user = users()
    enroll(user)

    def failing_session():
        session = SessionLocal()
        session.commit = MagicMock(side_effect=RuntimeError("private"))
        return session

    with pytest.raises(RuntimeError):
        monitor(config(user), session_factory=failing_session).run_cycle(apply=True)
    assert not notifications(user)
    with SessionLocal() as session:
        assert session.get(ShadowReviewMonitorState, (user, "UPBIT", "KRW")) is None


def test_concurrent_enqueue_and_claim_allow_one_send(users):
    user = users()
    enroll(user)
    settings = config(user, shadow_review_telegram_notifications_enabled=True)
    checker = eligibility(settings)
    barrier = Barrier(2)

    def synchronized(session, row, context):
        answer = checker.evaluate(session, row, context)
        barrier.wait(timeout=10)
        return answer

    check = SimpleNamespace(evaluate=synchronized)
    with ThreadPoolExecutor(2) as pool:
        runs = [
            pool.submit(monitor(settings, check=check).run_cycle, apply=True)
            for _ in range(2)
        ]
        assert (
            sum(future.result(timeout=20).created_notification_count for future in runs)
            == 1
        )
    assert len(notifications(user)) == 1
    fake = MagicMock()
    fake.send_message.return_value = receipt()
    with ThreadPoolExecutor(2) as pool:
        runs = [pool.submit(deliver, settings, fake, check=check) for _ in range(2)]
        assert (
            sum(future.result(timeout=20).telegram_messages_sent for future in runs)
            == 1
        )
    fake.send_message.assert_called_once()
    assert notifications(user)[0].delivery_status == "SENT"


def test_definite_rejection_respects_delay_and_retry_limit(users):
    user = users()
    enroll(user)
    settings = config(
        user,
        shadow_review_telegram_notifications_enabled=True,
        shadow_review_notification_max_retries=1,
    )
    monitor(settings).run_cycle(apply=True)
    fake = MagicMock()
    request = httpx.Request("POST", "https://example.invalid/fake")
    response = httpx.Response(
        429, json={"ok": False, "error_code": 429}, request=request
    )
    fake.send_message.side_effect = httpx.HTTPStatusError(
        "private URL", request=request, response=response
    )
    now = datetime.now(UTC)
    deliver(settings, fake, now=lambda: now)
    (row,) = notifications(user)
    assert (row.delivery_status, row.safe_failure_code) == (
        "FAILED",
        "TELEGRAM_REJECTED_429",
    )
    assert row.next_retry_at == now + timedelta(minutes=1)
    deliver(settings, fake, now=lambda: now)
    assert fake.send_message.call_count == 1
    now += timedelta(minutes=1)
    deliver(settings, fake, now=lambda: now)
    assert notifications(user)[0].next_retry_at is None
    now += timedelta(days=1)
    deliver(settings, fake, now=lambda: now)
    assert fake.send_message.call_count == 2


@pytest.mark.parametrize("failure", ["timeout", "bad_receipt", "commit"])
def test_uncertain_send_never_retries_after_restart(users, failure):
    user = users()
    enroll(user)
    settings = config(user, shadow_review_telegram_notifications_enabled=True)
    monitor(settings).run_cycle(apply=True)
    fake = MagicMock()
    fail_commit = False

    def send(**_):
        nonlocal fail_commit
        if failure == "timeout":
            raise httpx.ReadTimeout("fake-secret")
        if failure == "bad_receipt":
            return {"message_id": 99}
        fail_commit = True
        return receipt()

    def session_factory():
        session = SessionLocal()
        if fail_commit:
            session.commit = MagicMock(side_effect=RuntimeError("private"))
        return session

    fake.send_message.side_effect = send
    now = datetime.now(UTC)
    if failure == "commit":
        report = ShadowReviewMonitorResult.from_settings(settings, apply=True)
        with pytest.raises(RuntimeError):
            deliver(
                settings,
                fake,
                session_factory=session_factory,
                report=report,
                now=lambda: now,
            )
        assert report.external_calls and report.telegram_messages_sent == 1
        assert report.sending_notification_count == 1
        assert notifications(user)[0].delivery_status == "SENDING"
    else:
        assert (
            deliver(settings, fake, now=lambda: now).uncertain_notification_count == 1
        )

    deliver(settings, fake, now=lambda: now + timedelta(minutes=16))
    assert notifications(user)[0].delivery_status == "UNCERTAIN"
    fake.send_message.assert_called_once()


def test_cycle_limits_creation_and_delivery_and_constraints(users):
    user = users()
    for index in range(3):
        enroll(user, index)
    settings = config(
        user,
        shadow_review_telegram_notifications_enabled=True,
        shadow_review_notification_max_per_cycle=1,
    )
    first = monitor(settings).run_cycle(apply=True)
    assert (
        first.created_notification_count == 1 and first.deferred_notification_count == 2
    )
    assert monitor(settings).run_cycle(apply=True).created_notification_count == 1
    fake = MagicMock()
    fake.send_message.return_value = receipt()
    assert deliver(settings, fake).telegram_messages_sent == 1
    assert sum(row.delivery_status == "PENDING" for row in notifications(user)) == 1

    schema = inspect(engine)
    assert len(schema.get_foreign_keys("shadow_review_notifications")) == 3
    assert {
        "shadow_enrollment_id",
        "review_policy_signature",
        "notification_destination",
    } in [
        set(item["column_names"])
        for item in schema.get_unique_constraints("shadow_review_notifications")
    ]
    with SessionLocal() as session:
        row = session.get(ShadowReviewNotification, notifications(user)[0].id)
        row.delivery_status = "INVALID"
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_advisory_lock_excludes_worker_and_releases(capsys):
    lock_key = worker.SHADOW_REVIEW_MONITOR_LOCK_KEY
    session_factory = MagicMock()
    with PostgresAdvisoryLock(lock_key) as lock:
        assert lock.acquired
        assert (
            worker.run_worker(
                once=True, settings=config(1), session_factory=session_factory
            )
            == 0
        )
    session_factory.assert_not_called()
    assert "LOCK_NOT_ACQUIRED" in capsys.readouterr().out

    session_factory.side_effect = RuntimeError("private token")
    assert (
        worker.run_worker(
            once=True, settings=config(1), session_factory=session_factory
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "EXECUTION_ERROR" in output and "private token" not in output
    with PostgresAdvisoryLock(lock_key) as lock:
        assert lock.acquired
