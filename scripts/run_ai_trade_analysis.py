import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineStep:
    name: str
    module: str


PIPELINE_STEPS = [
    PipelineStep(
        name="시장 현재가 스냅샷 수집",
        module="scripts.collect_market_snapshots",
    ),
    PipelineStep(
        name="계좌 잔고 스냅샷 수집",
        module="scripts.collect_account_snapshots",
    ),
    PipelineStep(
        name="시장 캔들 수집",
        module="scripts.collect_market_candles",
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


def run_step(step: PipelineStep) -> None:
    print("=" * 80)
    print(f"START: {step.name}")
    print(f"MODULE: {step.module}")
    print("=" * 80)

    result = subprocess.run(
        [sys.executable, "-m", step.module],
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Pipeline step failed. name={step.name}, module={step.module}, "
            f"returncode={result.returncode}"
        )

    print("=" * 80)
    print(f"DONE: {step.name}")
    print("=" * 80)
    print()


def run_ai_trade_analysis() -> None:
    for step in PIPELINE_STEPS:
        run_step(step)

    print("AI trade analysis pipeline completed successfully.")


if __name__ == "__main__":
    run_ai_trade_analysis()