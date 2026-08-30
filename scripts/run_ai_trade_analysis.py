import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from crypto_trading_bot.config.settings import get_settings
from crypto_trading_bot.notification.telegram_client import TelegramClient
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
    ) -> None:
        self.step = step
        self.return_code = return_code

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


def run_step(step: PipelineStep, pipeline_run_id: str | None = None) -> None:
    print("=" * 80, flush=True)
    print(f"START: {step.name}", flush=True)
    print(f"MODULE: {step.module}", flush=True)
    print("=" * 80, flush=True)

    environment = os.environ.copy()
    if pipeline_run_id is not None:
        environment[PIPELINE_RUN_ID_ENV] = pipeline_run_id
    result = subprocess.run(
        [sys.executable, "-m", step.module],
        check=False,
        env=environment,
    )

    if result.returncode != 0:
        raise PipelineStepError(
            step=step,
            return_code=result.returncode,
        )

    print("=" * 80, flush=True)
    print(f"DONE: {step.name}", flush=True)
    print("=" * 80, flush=True)
    print(flush=True)


def build_failure_message(error: PipelineStepError) -> str:
    failed_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    return "\n".join(
        [
            "[AI 매매 분석 실패]",
            "",
            f"발생 시간: {failed_at}",
            f"실패 단계: {error.step.name}",
            f"실행 모듈: {error.step.module}",
            f"종료 코드: {error.return_code}",
            "",
            "분석이 중단되어 이후 단계는 실행되지 않았습니다.",
            "실행 로그를 확인해 주세요.",
        ]
    )


def send_failure_notification(error: PipelineStepError) -> None:
    try:
        settings = get_settings()

        if not settings.telegram_chat_id:
            print(
                "TELEGRAM_CHAT_ID is not configured. "
                "Failure notification was not sent.",
                file=sys.stderr,
                flush=True,
            )
            return

        TelegramClient().send_message(
            chat_id=settings.telegram_chat_id,
            text=build_failure_message(error),
        )

        print(
            "Pipeline failure notification sent to Telegram.",
            flush=True,
        )

    except Exception as notification_error:
        # 오류 알림 발송 실패가 원래 파이프라인 오류를 덮어쓰면 안 됨
        print(
            f"Failed to send pipeline failure notification. error={notification_error}",
            file=sys.stderr,
            flush=True,
        )


def run_ai_trade_analysis() -> None:
    pipeline_run_id = str(uuid4())
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

        send_failure_notification(error)

        raise

    print(
        "AI trade analysis pipeline completed successfully.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        run_ai_trade_analysis()
    except PipelineStepError:
        sys.exit(1)
