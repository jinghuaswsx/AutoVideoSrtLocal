"""LLM prompt/request debug payloads for translation task detail pages."""

from __future__ import annotations

import json
import os

from appcore.llm_debug_payloads import build_chat_request_payload
from appcore.safe_paths import PathSafetyError, resolve_under_allowed_roots


STEP_LABELS = {
    "extract": "音频提取",
    "asr": "语音识别",
    "asr_normalize": "原文标准化",
    "asr_clean": "原文纯净化",
    "alignment": "分段确认",
    "translate": "翻译本土化",
    "tts": "语音生成",
    "quality_assessment": "翻译质量评估",
    "analysis": "AI 视频分析",
}


def _safe_debug_file(task: dict, path: str | None) -> str | None:
    task_dir = str(task.get("task_dir") or "").strip()
    if not task_dir or not path:
        return None
    candidate = path if os.path.isabs(path) else os.path.join(task_dir, path)
    try:
        resolved = resolve_under_allowed_roots(candidate, [task_dir])
    except PathSafetyError:
        return None
    return str(resolved) if resolved.is_file() else None


def _load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {"payload": payload}
    except Exception:
        return None


def _message_stats(messages: list[dict]) -> dict:
    role_counts: dict[str, int] = {}
    total_chars = 0
    for msg in messages:
        role = str((msg or {}).get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = (msg or {}).get("content")
        if isinstance(content, str):
            total_chars += len(content)
        else:
            total_chars += len(json.dumps(content, ensure_ascii=False, default=str))
    return {
        "message_count": len(messages),
        "role_counts": role_counts,
        "total_chars": total_chars,
    }


def _normalize_item(ref: dict, file_payload: dict) -> dict:
    messages = file_payload.get("messages")
    if not isinstance(messages, list):
        request_messages = (file_payload.get("request_payload") or {}).get("messages")
        messages = request_messages if isinstance(request_messages, list) else []
    request_payload = file_payload.get("request_payload")
    if not isinstance(request_payload, dict):
        request_payload = build_chat_request_payload(
            use_case_code=file_payload.get("use_case_code") or ref.get("use_case"),
            provider=file_payload.get("provider") or ref.get("provider"),
            model=file_payload.get("model") or ref.get("model"),
            messages=messages,
        )
    item = {
        "id": ref.get("id") or ref.get("path") or file_payload.get("phase") or "",
        "label": ref.get("label") or file_payload.get("label") or file_payload.get("phase") or "LLM 调用",
        "title": ref.get("title") or file_payload.get("title") or ref.get("label") or "提示词",
        "phase": file_payload.get("phase") or ref.get("phase") or "",
        "step": ref.get("step") or "",
        "round": ref.get("round") or file_payload.get("round"),
        "attempt": ref.get("attempt") or file_payload.get("attempt"),
        "use_case": file_payload.get("use_case_code") or ref.get("use_case") or "",
        "provider": file_payload.get("provider") or ref.get("provider") or request_payload.get("provider") or "",
        "model": file_payload.get("model") or ref.get("model") or request_payload.get("model") or "",
        "created_at": ref.get("created_at") or file_payload.get("created_at") or "",
        "messages": messages,
        "request_payload": request_payload,
        "input_snapshot": file_payload.get("input_snapshot") or [],
        "raw_payload": file_payload,
        "message_stats": _message_stats(messages),
    }
    return item


def build_llm_debug_payload(task: dict, step: str) -> dict | None:
    refs = (task.get("llm_debug_refs") or {}).get(step) or []
    if not isinstance(refs, list) or not refs:
        return None
    items = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        path = _safe_debug_file(task, ref.get("path"))
        if not path:
            continue
        file_payload = _load_json(path)
        if file_payload is None:
            continue
        normalized_ref = dict(ref)
        normalized_ref.setdefault("step", step)
        items.append(_normalize_item(normalized_ref, file_payload))
    if not items:
        return None
    providers = sorted({item.get("provider") for item in items if item.get("provider")})
    models = sorted({item.get("model") for item in items if item.get("model")})
    return {
        "step": step,
        "step_label": STEP_LABELS.get(step, step),
        "summary": {
            "call_count": len(items),
            "providers": providers,
            "models": models,
            "message_count": sum((item.get("message_stats") or {}).get("message_count", 0) for item in items),
            "total_chars": sum((item.get("message_stats") or {}).get("total_chars", 0) for item in items),
        },
        "items": items,
    }
