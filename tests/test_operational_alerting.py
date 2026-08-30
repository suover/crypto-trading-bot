import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    JSON,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from crypto_trading_bot.config import settings as settings_module
from crypto_trading_bot.db.models import AnalysisRun, OperationalAlert, OrderLog, User
from crypto_trading_bot.exchange.upbit_order_exceptions import (
    UpbitOrderRejectedError,
    UpbitSafeError,
)
from crypto_trading_bot.operational.error_classifier import (
    OperationalErrorClassifier,
)
from crypto_trading_bot.operational.error_reporting import (
    OPERATIONAL_ERROR_FILE_ENV,
    read_operational_error,
    write_operational_error,
)
from crypto_trading_bot.services.operational_alert_service import (
    OperationalAlertDeliveryService,
    OperationalAlertService,
)
from scripts import run_operational_alert_worker as worker_script
from scripts.run_ai_trade_analysis import PipelineStep, PipelineStepError, run_step
from scripts.run_ai_trade_analysis import (
    build_failure_message,
    run_ai_trade_analysis,
    send_failure_notification,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
SECRET_TEXT = (
    "postgresql://user:password@host/db Authorization Bearer SECRET "
    "UPBIT_SECRET OPENAI_SECRET"
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (User, AnalysisRun, OrderLog, OperationalAlert):
        constraints = []
        if model is OperationalAlert:
            constraints.append(UniqueConstraint("dedup_key"))
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    (
                        JSON()
                        if isinstance(column.type, JSONB)
                        else Integer()
                        if isinstance(column.type, BigInteger)
                        else column.type
                    ),
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                    server_default=column.server_default,
                )
                for column in model.__table__.columns
            ),
            *constraints,
        )
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def add_user(session) -> User:
    user = User(name="operator")
    session.add(user)
    session.flush()
    return user


def add_order(
    session,
    user_id: int,
    status: str,
    *,
    age_seconds: int,
    trading_mode: str = "LIVE",
    exchange: str = "UPBIT",
) -> OrderLog:
    order = OrderLog(
        recommendation_id=session.query(OrderLog).count() + 1,
        user_id=user_id,
        trading_mode=trading_mode,
        exchange=exchange,
        market="KRW-BTC",
        side="BUY",
        order_type="MARKET",
        status=status,
        created_at=NOW - timedelta(seconds=age_seconds),
        updated_at=NOW,
    )
    session.add(order)
    session.flush()
    return order


def http_error(status: int, host: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://{host}/test")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        "unsafe " + SECRET_TEXT, request=request, response=response
    )


@pytest.mark.parametrize(
    ("error", "hint", "category"),
    [
        (http_error(401, "api.upbit.com"), None, "UPBIT_AUTH"),
        (http_error(429, "api.upbit.com"), None, "UPBIT_RATE_LIMIT"),
        (http_error(503, "api.upbit.com"), None, "UPBIT_API"),
        (http_error(401, "api.openai.com"), None, "OPENAI_AUTH"),
        (http_error(429, "api.openai.com"), None, "OPENAI_RATE_LIMIT"),
        (http_error(500, "api.openai.com"), None, "OPENAI_API"),
        (http_error(500, "api.telegram.org"), None, "TELEGRAM_API"),
        (ValueError(SECRET_TEXT), "TELEGRAM", "TELEGRAM_API"),
        (ValueError(SECRET_TEXT), None, "DATA_VALIDATION"),
        (RuntimeError(SECRET_TEXT), None, "UNKNOWN"),
    ],
)
def test_error_classifier_taxonomy(error, hint, category) -> None:
    envelope = OperationalErrorClassifier.classify(error, service_hint=hint)
    assert envelope.category == category
    assert SECRET_TEXT not in envelope.safe_message


@pytest.mark.parametrize(
    ("host", "exception_type", "category"),
    [
        ("api.upbit.com", httpx.ReadTimeout, "UPBIT_NETWORK"),
        ("api.openai.com", httpx.ConnectError, "OPENAI_NETWORK"),
        ("api.telegram.org", httpx.ConnectError, "TELEGRAM_NETWORK"),
    ],
)
def test_error_classifier_network(host, exception_type, category) -> None:
    request = httpx.Request("GET", f"https://{host}/test")
    envelope = OperationalErrorClassifier.classify(
        exception_type(SECRET_TEXT, request=request)
    )
    assert envelope.category == category


def test_error_classifier_known_upbit_and_database() -> None:
    upbit = UpbitOrderRejectedError(
        UpbitSafeError("HTTPStatusError", "create_order", 403, message=SECRET_TEXT)
    )
    assert OperationalErrorClassifier.classify(upbit).category == "UPBIT_AUTH"
    database = OperationalError("statement", {}, Exception(SECRET_TEXT))
    assert OperationalErrorClassifier.classify(database).category == "DATABASE"


def test_structured_error_file_is_sanitized_and_permissions_are_restricted(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "error.json"
    monkeypatch.setenv(OPERATIONAL_ERROR_FILE_ENV, str(path))
    envelope = write_operational_error(RuntimeError(SECRET_TEXT))
    payload = path.read_text(encoding="utf-8")
    assert envelope.category == "UNKNOWN"
    assert SECRET_TEXT not in payload
    assert "password" not in payload
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_reader_rejects_raw_message_and_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "error.json"
    path.write_text(
        json.dumps(
            {
                "category": "DATABASE",
                "code": "DB_OPERATION_ERROR",
                "safe_message": SECRET_TEXT,
            }
        ),
        encoding="utf-8",
    )
    envelope = read_operational_error(path)
    assert envelope.category == "DATABASE"
    assert SECRET_TEXT not in envelope.safe_message
    path.write_text("not-json", encoding="utf-8")
    assert read_operational_error(path).category == "UNKNOWN"


def test_pipeline_step_reads_structured_error_and_cleans_temp_file(monkeypatch) -> None:
    captured_path = ""

    def run(_command, **kwargs):
        nonlocal captured_path
        captured_path = kwargs["env"][OPERATIONAL_ERROR_FILE_ENV]
        Path(captured_path).write_text(
            json.dumps(
                {
                    "category": "UPBIT_RATE_LIMIT",
                    "code": "HTTP_429",
                    "safe_message": SECRET_TEXT,
                    "http_status_code": 429,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("scripts.run_ai_trade_analysis.subprocess.run", run)
    with pytest.raises(PipelineStepError) as caught:
        run_step(PipelineStep("universe", "scripts.test"), "pipeline-id")
    assert caught.value.operational_error.category == "UPBIT_RATE_LIMIT"
    assert caught.value.operational_error.http_status_code == 429
    assert SECRET_TEXT not in caught.value.operational_error.safe_message
    assert not Path(captured_path).exists()


def test_pipeline_success_is_unchanged_and_temp_file_is_removed(monkeypatch) -> None:
    captured_path = ""

    def run(_command, **kwargs):
        nonlocal captured_path
        captured_path = kwargs["env"][OPERATIONAL_ERROR_FILE_ENV]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.run_ai_trade_analysis.subprocess.run", run)
    run_step(PipelineStep("success", "scripts.test"), "pipeline-id")
    assert not Path(captured_path).exists()


@pytest.mark.parametrize("payload", [None, "corrupt"])
def test_pipeline_step_missing_or_corrupt_error_falls_back_to_unknown(
    monkeypatch, payload
) -> None:
    def run(_command, **kwargs):
        if payload is not None:
            Path(kwargs["env"][OPERATIONAL_ERROR_FILE_ENV]).write_text(
                payload, encoding="utf-8"
            )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("scripts.run_ai_trade_analysis.subprocess.run", run)
    with pytest.raises(PipelineStepError) as caught:
        run_step(PipelineStep("failed", "scripts.test"), "pipeline-id")
    assert caught.value.operational_error.category == "UNKNOWN"


@pytest.mark.parametrize(("age", "expected"), [(599, 0), (600, 1), (601, 1)])
def test_stale_threshold_uses_created_at(session_factory, age, expected) -> None:
    with session_factory() as session:
        user = add_user(session)
        order = add_order(session, user.id, "LIVE_UNKNOWN", age_seconds=age)
        order.updated_at = NOW + timedelta(days=1)
        order.execution_synced_at = NOW + timedelta(days=1)
        candidates = OperationalAlertService(session).find_stale_live_orders(
            stale_after_seconds=600, now=NOW
        )
    assert len(candidates) == expected


@pytest.mark.parametrize(
    ("status", "mode", "expected"),
    [
        ("LIVE_PLACED", "LIVE", 1),
        ("LIVE_WAIT", "LIVE", 1),
        ("LIVE_UNKNOWN", "LIVE", 1),
        ("LIVE_DONE", "LIVE", 0),
        ("LIVE_CANCELLED", "LIVE", 0),
        ("LIVE_EXECUTED_CANCELLED", "LIVE", 0),
        ("LIVE_FAILED", "LIVE", 0),
        ("LIVE_UNKNOWN", "MOCK", 0),
    ],
)
def test_stale_status_and_mode_filter(session_factory, status, mode, expected) -> None:
    with session_factory() as session:
        user = add_user(session)
        add_order(session, user.id, status, age_seconds=601, trading_mode=mode)
        candidates = OperationalAlertService(session).find_stale_live_orders(
            stale_after_seconds=600, now=NOW
        )
    assert len(candidates) == expected


def test_stale_alert_dedup_and_resolution_do_not_mutate_order(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        order = add_order(session, user.id, "LIVE_UNKNOWN", age_seconds=601)
        service = OperationalAlertService(session, now_fn=lambda: NOW)
        candidates = service.find_stale_live_orders(stale_after_seconds=600, now=NOW)
        assert len(service.create_stale_alerts(candidates)) == 1
        assert len(service.create_stale_alerts(candidates)) == 0
        session.commit()
        assert session.query(OperationalAlert).count() == 1
        assert order.status == "LIVE_UNKNOWN"

        order.status = "LIVE_DONE"
        session.commit()
        assert service.resolve_terminal_stale_alerts(now=NOW) == 1
        session.commit()
        resolved_at = session.query(OperationalAlert).one().resolved_at
        assert OperationalAlertService._as_utc(resolved_at) == NOW
        assert order.status == "LIVE_DONE"


class FakeTelegram:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.messages: list[str] = []

    def send_message(self, *, chat_id, text):
        self.messages.append(text)
        if not self.outcomes.pop(0):
            raise httpx.ConnectError(SECRET_TEXT)
        return {"message_id": 1}


def test_delivery_retry_delay_success_and_no_repeat(session_factory) -> None:
    current = NOW
    with session_factory() as session:
        alert = OperationalAlert(
            alert_type="PIPELINE_FAILURE",
            severity="CRITICAL",
            safe_message="safe body",
            dedup_key="pipeline:test",
            delivery_status="PENDING",
            delivery_attempt_count=0,
        )
        session.add(alert)
        session.commit()
    telegram = FakeTelegram([False, True])
    service = OperationalAlertDeliveryService(
        session_factory,
        telegram_client=telegram,  # type: ignore[arg-type]
        telegram_chat_id="1",
        max_retries=3,
        retry_delays_minutes=[1, 5, 15],
        now_fn=lambda: current,
    )
    first = service.process_due()
    assert first[0].delivery_status == "FAILED"
    assert first[0].attempt_count == 1
    assert first[0].next_retry_at == NOW + timedelta(minutes=1)
    assert service.process_due() == ()

    current = NOW + timedelta(minutes=1)
    second = service.process_due()
    assert second[0].delivery_status == "SENT"
    assert second[0].attempt_count == 2
    assert service.process_due() == ()
    assert telegram.messages == ["safe body", "safe body"]
    assert SECRET_TEXT not in "".join(telegram.messages)


def test_delivery_stops_after_initial_attempt_and_max_retries(session_factory) -> None:
    current = NOW
    with session_factory() as session:
        session.add(
            OperationalAlert(
                alert_type="PIPELINE_FAILURE",
                severity="CRITICAL",
                safe_message="safe body",
                dedup_key="pipeline:max-retry",
                delivery_status="PENDING",
                delivery_attempt_count=0,
            )
        )
        session.commit()
    telegram = FakeTelegram([False, False])
    service = OperationalAlertDeliveryService(
        session_factory,
        telegram_client=telegram,  # type: ignore[arg-type]
        telegram_chat_id="1",
        max_retries=1,
        retry_delays_minutes=[1],
        now_fn=lambda: current,
    )
    assert service.process_due()[0].attempt_count == 1
    current += timedelta(minutes=1)
    exhausted = service.process_due()[0]
    assert exhausted.attempt_count == 2
    assert exhausted.next_retry_at is None
    current += timedelta(days=1)
    assert service.process_due() == ()
    assert len(telegram.messages) == 2


def test_resolved_stale_alert_is_not_delivered(session_factory) -> None:
    with session_factory() as session:
        session.add(
            OperationalAlert(
                alert_type="STALE_LIVE_ORDER",
                severity="WARNING",
                safe_message="obsolete body",
                dedup_key="stale:resolved",
                delivery_status="PENDING",
                delivery_attempt_count=0,
                resolved_at=NOW,
            )
        )
        session.commit()
    telegram = FakeTelegram([])
    service = OperationalAlertDeliveryService(
        session_factory,
        telegram_client=telegram,  # type: ignore[arg-type]
        telegram_chat_id="1",
        max_retries=3,
        retry_delays_minutes=[1, 5, 15],
        now_fn=lambda: NOW,
    )
    assert service.process_due() == ()
    assert telegram.messages == []


def test_pipeline_alert_links_latest_analysis_and_deduplicates(session_factory) -> None:
    with session_factory() as session:
        user = add_user(session)
        run = AnalysisRun(
            user_id=user.id,
            pipeline_run_id="11111111-1111-1111-1111-111111111111",
            run_type="MARKET_UNIVERSE",
            trading_mode="AI_APPROVAL",
            status="FAILED",
        )
        session.add(run)
        session.flush()
        service = OperationalAlertService(session)
        envelope = OperationalErrorClassifier.classify(http_error(429, "api.upbit.com"))
        first = service.create_pipeline_failure(
            pipeline_run_id=run.pipeline_run_id,
            step_module="scripts.build_market_universe",
            envelope=envelope,
            safe_message="safe message",
        )
        second = service.create_pipeline_failure(
            pipeline_run_id=run.pipeline_run_id,
            step_module="scripts.build_market_universe",
            envelope=envelope,
            safe_message="safe message",
        )
        assert first.id == second.id
        assert first.analysis_run_id == run.id
        assert first.user_id == user.id
        assert session.query(OperationalAlert).count() == 1


def test_pipeline_alert_persists_without_telegram_chat(
    session_factory, monkeypatch
) -> None:
    from crypto_trading_bot.db import database as database_module

    monkeypatch.setattr(database_module, "SessionLocal", session_factory)
    monkeypatch.setattr(
        "scripts.run_ai_trade_analysis.get_settings",
        lambda: SimpleNamespace(
            telegram_chat_id="",
            operational_alert_max_retries=3,
            operational_alert_retry_delay_list=[1, 5, 15],
        ),
    )
    error = PipelineStepError(
        PipelineStep("database", "scripts.test"),
        1,
        OperationalErrorClassifier.classify(OperationalError("sql", {}, Exception())),
    )
    send_failure_notification(error, "22222222-2222-2222-2222-222222222222")
    with session_factory() as session:
        alert = session.query(OperationalAlert).one()
        assert alert.delivery_status == "PENDING"
        assert alert.error_category == "DATABASE"


def test_pipeline_failure_message_and_alert_do_not_contain_raw_error(
    session_factory,
) -> None:
    envelope = OperationalErrorClassifier.classify(RuntimeError(SECRET_TEXT))
    error = PipelineStepError(PipelineStep("failure", "scripts.test"), 1, envelope)
    message = build_failure_message(error, "pipeline-id")
    with session_factory() as session:
        alert = OperationalAlertService(session).create_pipeline_failure(
            pipeline_run_id="pipeline-id",
            step_module="scripts.test",
            envelope=envelope,
            safe_message=message,
        )
        assert SECRET_TEXT not in alert.safe_message
        assert "password" not in alert.safe_message


def test_pipeline_failure_notification_does_not_replace_original_error(
    monkeypatch,
) -> None:
    original = PipelineStepError(PipelineStep("failure", "scripts.test"), 1)

    def fail_step(_step, _pipeline_run_id):
        raise original

    monkeypatch.setattr("scripts.run_ai_trade_analysis.run_step", fail_step)
    monkeypatch.setattr(
        "scripts.run_ai_trade_analysis.send_failure_notification",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("notification failed")),
    )
    with pytest.raises(PipelineStepError) as caught:
        run_ai_trade_analysis()
    assert caught.value is original


def test_disabled_worker_does_not_initialize_database(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            operational_alerting_enabled=False,
            operational_alert_interval_seconds=60,
        ),
    )
    worker_script.run_worker(once=True)
    assert "inactive (disabled)" in capsys.readouterr().out


def test_worker_lock_key_is_unique() -> None:
    from scripts.run_ai_trade_scheduler import AI_TRADE_SCHEDULER_LOCK_KEY
    from scripts.run_bot_trading_pnl_worker import BOT_TRADING_PNL_WORKER_LOCK_KEY
    from scripts.run_live_order_reconciliation_worker import (
        LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
    )

    assert (
        len(
            {
                worker_script.OPERATIONAL_ALERT_WORKER_LOCK_KEY,
                AI_TRADE_SCHEDULER_LOCK_KEY,
                BOT_TRADING_PNL_WORKER_LOCK_KEY,
                LIVE_ORDER_RECONCILIATION_WORKER_LOCK_KEY,
            }
        )
        == 4
    )
