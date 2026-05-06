"""Runtime helpers for persisting LLM debug calls into task artifacts."""

from __future__ import annotations

import re
from collections.abc import Callable

import appcore.task_state as task_state


_SAFE_PART_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_part(value: object, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = _SAFE_PART_RE.sub("_", text).strip("._")
    return text or fallback


def save_llm_debug_calls(
    *,
    task_id: str,
    task_dir: str,
    step: str,
    calls: list[dict] | None,
    save_json: Callable[[str, str, dict], object],
) -> None:
    if not calls:
        return
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            continue
        phase = _safe_part(call.get("phase"), f"call_{index}")
        filename = f"{step}_llm_debug.{index:02d}.{phase}.json"
        save_json(task_dir, filename, call)
        task_state.add_llm_debug_ref(task_id, step, {
            "id": f"{step}.{phase}.{index}",
            "label": call.get("label") or call.get("phase") or f"LLM 调用 {index}",
            "path": filename,
            "phase": call.get("phase") or "",
            "use_case": call.get("use_case_code") or call.get("use_case") or "",
            "provider": call.get("provider") or "",
            "model": call.get("model") or "",
        })
