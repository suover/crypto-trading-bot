from types import SimpleNamespace
from uuid import uuid4

from crypto_trading_bot.services.pipeline_identity import PIPELINE_RUN_ID_ENV
from scripts.run_ai_trade_analysis import PipelineStep, run_step


def test_pipeline_run_id_is_passed_to_subprocess(monkeypatch: object) -> None:
    captured: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.run_ai_trade_analysis.subprocess.run", run)
    pipeline_run_id = str(uuid4())

    run_step(PipelineStep("test", "scripts.test"), pipeline_run_id)

    assert captured["env"][PIPELINE_RUN_ID_ENV] == pipeline_run_id
