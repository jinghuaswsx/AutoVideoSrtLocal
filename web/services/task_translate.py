"""Task translation start workflow."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from appcore.runtime import _VALID_TRANSLATE_PREFS
from web import store
from web.services import pipeline_runner
from web.services.task_prompts import resolve_task_prompt_text


@dataclass(frozen=True)
class TaskTranslateStartOutcome:
    payload: dict
    status_code: int = 200


def start_task_translate(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int | None,
    update_task: Callable[..., object] = store.update,
    set_current_review_step: Callable[..., object] = store.set_current_review_step,
    resolve_prompt_text: Callable[..., str] = resolve_task_prompt_text,
    valid_translate_prefs: Collection[str] = _VALID_TRANSLATE_PREFS,
    runner=pipeline_runner,
) -> TaskTranslateStartOutcome:
    if not task.get("_translate_pre_select"):
        return TaskTranslateStartOutcome({"error": "翻译步骤不在预选状态"}, 400)

    model_provider = str(body.get("model_provider") or "").strip()
    prompt_id = body.get("prompt_id")
    prompt_text = resolve_prompt_text(
        str(body.get("prompt_text") or "").strip(),
        prompt_id,
        user_id=user_id,
    )

    updates = {"_translate_pre_select": False}
    if model_provider in valid_translate_prefs:
        updates["custom_translate_provider"] = model_provider
    if prompt_text:
        updates["custom_translate_prompt"] = prompt_text

    update_task(task_id, **updates)
    set_current_review_step(task_id, "")
    runner.resume(task_id, "translate", user_id=user_id)
    return TaskTranslateStartOutcome({"status": "started"})
