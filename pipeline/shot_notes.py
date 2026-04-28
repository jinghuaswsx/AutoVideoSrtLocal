from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from appcore import llm_client
from appcore.llm_use_cases import get_use_case

SHOT_NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "global": {
            "type": "object",
            "properties": {
                "product_name": {"type": ["string", "null"]},
                "category": {"type": ["string", "null"]},
                "overall_theme": {"type": "string"},
                "hook_range": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        {"type": "null"},
                    ]
                },
                "demo_range": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        {"type": "null"},
                    ]
                },
                "proof_range": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        {"type": "null"},
                    ]
                },
                "cta_range": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        {"type": "null"},
                    ]
                },
                "observed_selling_points": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "price_mentioned": {"type": ["string", "null"]},
                "on_screen_persistent_text": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "pacing_note": {"type": "string"},
            },
            "required": [
                "product_name",
                "category",
                "overall_theme",
                "hook_range",
                "demo_range",
                "proof_range",
                "cta_range",
                "observed_selling_points",
                "price_mentioned",
                "on_screen_persistent_text",
                "pacing_note",
            ],
        },
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asr_index": {"type": "integer"},
                    "start_time": {"type": "number"},
                    "end_time": {"type": "number"},
                    "scene": {"type": ["string", "null"]},
                    "action": {"type": ["string", "null"]},
                    "on_screen_text": {"type": "array", "items": {"type": "string"}},
                    "product_visible": {"type": "boolean"},
                    "shot_type": {
                        "type": ["string", "null"],
                        "enum": ["close_up", "medium", "wide", "pov", "overlay", None],
                    },
                    "emotion_hint": {"type": ["string", "null"]},
                },
                "required": [
                    "asr_index",
                    "start_time",
                    "end_time",
                    "scene",
                    "action",
                    "on_screen_text",
                    "product_visible",
                    "shot_type",
                    "emotion_hint",
                ],
            },
        },
    },
    "required": ["global", "sentences"],
}

SYSTEM_PROMPT = """你是专业的短视频分镜标注师。
请结合视频画面与输入的 ASR 句列表，输出一份严格 JSON 的画面笔记。

要求：
1. 全局层要识别产品名、类目、整体主题、Hook/Demo/Proof/CTA 范围、卖点、价格、常驻字和节奏。
2. 逐句层必须覆盖每个 ASR index，描述该句发生时的 scene / action / on_screen_text / product_visible / shot_type / emotion_hint。
3. 未知字段填 null，列表字段填空数组，绝不省略字段。
4. 只输出 JSON，不要 Markdown，不要额外解释。"""

USER_PROMPT_TEMPLATE = """目标市场: {target_market}
目标语言: {target_language}

请基于以下 ASR 句列表分析视频画面，严格按 schema 输出 JSON。
sentences 数组长度必须等于输入 ASR 句数。

ASR:
{script_segments_json}
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _segment_asr_index(segment: dict, fallback_index: int) -> int:
    raw_index = segment.get("index", segment.get("asr_index", fallback_index))
    return int(raw_index)


def _segment_start(segment: dict) -> float:
    return float(segment.get("start_time", segment.get("start", 0.0)))


def _segment_end(segment: dict) -> float:
    return float(segment.get("end_time", segment.get("end", 0.0)))


def _segment_text(segment: dict) -> str:
    return str(segment.get("text") or "")


def _default_global() -> dict:
    return {
        "product_name": None,
        "category": None,
        "overall_theme": "",
        "hook_range": None,
        "demo_range": None,
        "proof_range": None,
        "cta_range": None,
        "observed_selling_points": [],
        "price_mentioned": None,
        "on_screen_persistent_text": [],
        "pacing_note": "",
    }


def _default_sentence_note(segment: dict, fallback_index: int) -> dict:
    return {
        "asr_index": _segment_asr_index(segment, fallback_index),
        "start_time": _segment_start(segment),
        "end_time": _segment_end(segment),
        "scene": None,
        "action": None,
        "on_screen_text": [],
        "product_visible": False,
        "shot_type": None,
        "emotion_hint": None,
    }


def _normalize_sentence_note(note: dict, segment: dict, fallback_index: int) -> dict:
    default = _default_sentence_note(segment, fallback_index)
    if not isinstance(note, dict):
        return default
    return {
        "asr_index": int(note.get("asr_index", default["asr_index"])),
        "start_time": float(note.get("start_time", default["start_time"])),
        "end_time": float(note.get("end_time", default["end_time"])),
        "scene": note.get("scene"),
        "action": note.get("action"),
        "on_screen_text": list(note.get("on_screen_text") or []),
        "product_visible": bool(note.get("product_visible", False)),
        "shot_type": note.get("shot_type"),
        "emotion_hint": note.get("emotion_hint"),
    }


def _build_prompt(script_segments: list[dict], target_language: str, target_market: str) -> str:
    payload = []
    for fallback_index, segment in enumerate(script_segments):
        payload.append(
            {
                "index": _segment_asr_index(segment, fallback_index),
                "start": _segment_start(segment),
                "end": _segment_end(segment),
                "text": _segment_text(segment),
            }
        )
    return USER_PROMPT_TEMPLATE.format(
        target_market=target_market,
        target_language=target_language,
        script_segments_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _normalize_output(raw_output: dict, script_segments: list[dict]) -> dict:
    raw_global = raw_output.get("global") if isinstance(raw_output, dict) else None
    raw_sentences = raw_output.get("sentences") if isinstance(raw_output, dict) else None
    note_by_index: dict[int, dict] = {}

    for fallback_index, segment in enumerate(list(raw_sentences or [])):
        if not isinstance(segment, dict):
            continue
        asr_index = int(segment.get("asr_index", fallback_index))
        note_by_index[asr_index] = segment

    normalized_sentences = []
    for fallback_index, script_segment in enumerate(script_segments):
        asr_index = _segment_asr_index(script_segment, fallback_index)
        normalized_sentences.append(
            _normalize_sentence_note(
                note_by_index.get(asr_index, {}),
                script_segment,
                fallback_index,
            )
        )

    use_case = get_use_case("video_translate.shot_notes")
    return {
        "global": {
            **_default_global(),
            **(raw_global if isinstance(raw_global, dict) else {}),
        },
        "sentences": normalized_sentences,
        "generated_at": _utc_now_iso(),
        "model": {
            "provider": use_case["default_provider"],
            "model": use_case["default_model"],
        },
    }


def build_fallback_shot_notes(script_segments: list[dict], *, reason: str | None = None) -> dict:
    notes = _normalize_output({}, script_segments)
    notes["fallback"] = {
        "used": True,
        "reason": reason or "shot notes generation unavailable",
    }
    notes["global"]["overall_theme"] = notes["global"].get("overall_theme") or "Visual analysis unavailable"
    notes["global"]["pacing_note"] = (
        notes["global"].get("pacing_note")
        or "Use the ASR sentence timeline as the primary pacing constraint."
    )
    return notes


def generate_shot_notes(
    *,
    video_path: str | Path,
    script_segments: list[dict],
    target_language: str,
    target_market: str,
    user_id: int | None = None,
    project_id: str | None = None,
    max_retries: int = 2,
) -> dict:
    prompt = _build_prompt(script_segments, target_language, target_market)
    media = [str(video_path)]
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = llm_client.invoke_generate(
                "video_translate.shot_notes",
                prompt=prompt,
                system=SYSTEM_PROMPT,
                media=media,
                response_schema=SHOT_NOTES_SCHEMA,
                user_id=user_id,
                project_id=project_id,
                temperature=0.2,
                max_output_tokens=4096,
            )
            return _normalize_output(result, script_segments)
        except Exception as exc:  # pragma: no cover - error path asserted by tests
            last_error = exc
            if attempt >= max_retries:
                raise
            time.sleep(0.1 * (2**attempt))

    if last_error is not None:
        raise last_error
    raise RuntimeError("shot notes generation failed without exception")
