import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.operational.error_classifier import OperationalErrorEnvelope
from crypto_trading_bot.operational.error_reporting import (
    OPERATIONAL_ERROR_FILE_ENV,
    read_operational_error,
)
from crypto_trading_bot.services.runtime_user_resolver import RuntimeUserResolver
from crypto_trading_bot.services.pipeline_identity import PIPELINE_RUN_ID_ENV


@dataclass(frozen=True)
class PipelineStep:
    name: str
    module: str


class PipelineStepError(RuntimeError):
    def __init__(
        self,
        step: PipelineStep,
        return_code: int,
        error: OperationalErrorEnvelope | None = None,
    ) -> None:
        self.step = step
        self.return_code = return_code
        self.operational_error = error or OperationalErrorEnvelope.unknown()

        super().__init__(
            f"Pipeline step failed. "
            f"name={step.name}, "
            f"module={step.module}, "
            f"return_code={return_code}"
        )


PIPELINE_STEPS = [
    PipelineStep(
        name="계좌 잔고 스냅샷 수집",
        module="scripts.collect_account_snapshots",
    ),
    PipelineStep(
        name="시장 Universe 및 Multi-Timeframe 데이터 구축",
        module="scripts.build_market_universe",
    ),
    PipelineStep(
        name="Portfolio valuation snapshot 저장",
        module="scripts.capture_portfolio_valuation",
    ),
    PipelineStep(
        name="AI 매매 추천 생성",
        module="scripts.generate_ai_trade_recommendations",
    ),
    PipelineStep(
        name="AI 추천 결과 텔레그램 알림",
        module="scripts.send_latest_ai_trade_recommendations",
    ),
]


def resolve_pipeline_user_id() -> int:
    from crypto_trading_bot.db.database import SessionLocal

    settings = get_settings()
    with SessionLocal() as session:
        return (
            RuntimeUserResolver(session).resolve_configured(settings.trading_user_id).id
        )


def run_step(step: PipelineStep, pipeline_run_id: str | None = None) -> None:
    print("=" * 80, flush=True)
    print(f"START: {step.name}", flush=True)
    print(f"MODULE: {step.module}", flush=True)
    print("=" * 80, flush=True)

    environment = os.environ.copy()
    if pipeline_run_id is not None:
        environment[PIPELINE_RUN_ID_ENV] = pipeline_run_id
    descriptor, error_path = tempfile.mkstemp(
        prefix="crypto-trading-error-", suffix=".json"
    )
    os.close(descriptor)
    try:
        os.chmod(error_path, 0o600)
    except OSError:
        pass
    environment[OPERATIONAL_ERROR_FILE_ENV] = error_path
    try:
        result = subprocess.run(
            [sys.executable, "-m", step.module],
            check=False,
            env=environment,
        )

        if result.returncode != 0:
            raise PipelineStepError(
                step=step,
                return_code=result.returncode,
                error=read_operational_error(error_path),
            )
    finally:
        try:
            os.unlink(error_path)
        except OSError:
            pass

    print("=" * 80, flush=True)
    print(f"DONE: {step.name}", flush=True)
    print("=" * 80, flush=True)
    print(flush=True)


def build_failure_message(error: PipelineStepError, pipeline_run_id: str) -> str:
    failed_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    return "\n".join(
        [
            "[AI 매매 분석 실패]",
            "",
            f"발생 시간: {failed_at}",
            f"Pipeline ID: {pipeline_run_id}",
            f"실패 단계: {error.step.name}",
            f"실행 모듈: {error.step.module}",
            "",
            f"오류 분류: {error.operational_error.category}",
            f"원인: {error.operational_error.safe_message}",
            *(
                [f"HTTP 상태: {error.operational_error.http_status_code}"]
                if error.operational_error.http_status_code is not None
                else []
            ),
            "",
            "이 파이프라인의 이후 단계는 실행되지 않았습니다.",
            "자동 재주문이나 자동 주문 취소는 수행하지 않습니다.",
        ]
    )


def send_failure_notification(error: PipelineStepError, pipeline_run_id: str) -> None:
    message = build_failure_message(error, pipeline_run_id)
    try:
        settings = get_settings()
    except Exception as settings_error:
        print(
            "Failed to load settings for pipeline failure alert. "
            f"error_type={type(settings_error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return

    try:
        from crypto_trading_bot.db.database import SessionLocal
        from crypto_trading_bot.services.operational_alert_service import (
            OperationalAlertDeliveryService,
            OperationalAlertService,
        )

        with SessionLocal() as session:
            alert = OperationalAlertService(session).create_pipeline_failure(
                pipeline_run_id=pipeline_run_id,
                step_module=error.step.module,
                envelope=error.operational_error,
                safe_message=message,
            )
            alert_id = alert.id
            session.commit()
    except Exception as persistence_error:
        print(
            "Failed to persist pipeline failure alert; attempting safe direct delivery. "
            f"error_type={type(persistence_error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        if not settings.telegram_chat_id:
            print(
                "TELEGRAM_CHAT_ID is not configured. Failure notification was not sent.",
                file=sys.stderr,
                flush=True,
            )
            return
        try:
            from crypto_trading_bot.notification.telegram_client import TelegramClient

            TelegramClient().send_message(
                chat_id=settings.telegram_chat_id,
                text=message,
            )
        except Exception as delivery_error:
            print(
                "Failed to directly deliver pipeline failure alert. "
                f"error_type={type(delivery_error).__name__}",
                file=sys.stderr,
                flush=True,
            )
        return

    if not settings.telegram_chat_id:
        print(
            f"Pipeline failure alert persisted. alert_id={alert_id} "
            "delivery_status=PENDING; TELEGRAM_CHAT_ID is not configured.",
            file=sys.stderr,
            flush=True,
        )
        return

    try:
        result = OperationalAlertDeliveryService(
            SessionLocal,
            telegram_chat_id=settings.telegram_chat_id,
            max_retries=settings.operational_alert_max_retries,
            retry_delays_minutes=settings.operational_alert_retry_delay_list,
        ).deliver_alert(alert_id)

        print(
            "Pipeline failure alert processed. "
            f"alert_id={alert_id} delivery_status="
            f"{result.delivery_status if result is not None else 'UNCHANGED'}",
            flush=True,
        )

    except Exception as delivery_error:
        # 오류 알림 발송 실패가 원래 파이프라인 오류를 덮어쓰면 안 됨
        print(
            "Failed to deliver persisted pipeline failure alert. "
            f"error_type={type(delivery_error).__name__}",
            file=sys.stderr,
            flush=True,
        )


def run_ai_trade_analysis() -> None:
    user_id = resolve_pipeline_user_id()
    pipeline_run_id = str(uuid4())
    previous_user_id = os.environ.get("TRADING_USER_ID")
    os.environ["TRADING_USER_ID"] = str(user_id)
    print(f"PIPELINE_RUN_ID: {pipeline_run_id}", flush=True)
    try:
        for step in PIPELINE_STEPS:
            run_step(step, pipeline_run_id)

    except PipelineStepError as error:
        print(
            f"PIPELINE FAILED: {error}",
            file=sys.stderr,
            flush=True,
        )

        try:
            send_failure_notification(error, pipeline_run_id)
        except Exception as notification_error:
            print(
                "Unexpected pipeline failure notification error. "
                f"error_type={type(notification_error).__name__}",
                file=sys.stderr,
                flush=True,
            )

        raise

    finally:
        if previous_user_id is None:
            os.environ.pop("TRADING_USER_ID", None)
        else:
            os.environ["TRADING_USER_ID"] = previous_user_id

    print(
        "AI trade analysis pipeline completed successfully.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        run_ai_trade_analysis()
    except PipelineStepError:
        sys.exit(1)
