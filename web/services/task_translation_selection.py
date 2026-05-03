"""Task translation selection workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from web import store


@dataclass(frozen=True)
class TaskTranslationSelectionOutcome:
    payload: dict
    status_code: int = 200


def select_task_translation(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    update_variant: Callable[..., object] = store.update_variant,
    update_task: Callable[..., object] = store.update,
) -> TaskTranslationSelectionOutcome:
    index = body.get("index")
    if index is None:
        return TaskTranslationSelectionOutcome({"error": "index is required"}, 400)
    if isinstance(index, bool) or not isinstance(index, int):
        return TaskTranslationSelectionOutcome({"error": "invalid translation index"}, 400)

    translation_history = task.get("translation_history") or []
    if not (0 <= index < len(translation_history)):
        return TaskTranslationSelectionOutcome({"error": "无效的翻译索引"}, 400)

    selected = translation_history[index].get("result") if isinstance(translation_history[index], dict) else None
    if selected is None:
        return TaskTranslationSelectionOutcome({"error": "无效的翻译索引"}, 400)

    update_variant(task_id, "normal", localized_translation=selected)
    update_task(task_id, selected_translation_index=index)

    return TaskTranslationSelectionOutcome({"status": "ok", "selected_index": index})
