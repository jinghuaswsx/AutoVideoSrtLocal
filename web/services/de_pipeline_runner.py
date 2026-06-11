"""German pipeline SocketIO adapter — delegates to lang_pipeline_runner.py."""
from __future__ import annotations

from appcore.runtime_de import DeTranslateRunner
from web.services import lang_pipeline_runner


def start(task_id: str, user_id: int | None = None) -> bool:
    return lang_pipeline_runner.start("de", task_id, user_id)


def resume(task_id: str, start_step: str, user_id: int | None = None) -> bool:
    return lang_pipeline_runner.resume("de", task_id, start_step, user_id)


def run_analysis(task_id: str, user_id: int | None = None):
    return lang_pipeline_runner.run_analysis("de", task_id, user_id)
