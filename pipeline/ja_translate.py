"""Japanese-specific video translation helpers.

This module intentionally does not use whitespace word counts. Japanese TTS
timing is planned by visible full-width character budget per source segment.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from appcore import llm_client
from pipeline import speech_rate_model
from pipeline.languages import ja as ja_rules

FALLBACK_JA_CPS = 7.0
TARGET_LANGUAGE = "ja"
TARGET_LANGUAGE_NAME = "Japanese"
TARGET_MARKET = "Japan"
MAX_SUBTITLE_CHARS = 21

JA_TRANSLATE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "ja_video_translate",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "asr_index": {"type": "integer"},
                            "text": {"type": "string"},
                            "est_chars": {"type": "integer"},
                            "notes": {"type": "string"},
                        },
                        "required": ["asr_index", "text", "est_chars"],
                    },
                }
            },
            "required": ["sentences"],
        },
    },
}

SYSTEM_PROMPT = """あなたは日本市場向け短尺EC動画の吹き替え台本ローカライズ担当です。
出力は必ず JSON のみです。

重要ルール:
1. 入力された各 sentence に対して、必ず同じ asr_index の日本語文を 1 件返してください。
2. 各文の visible Japanese characters は target_chars_range に収めてください。空白区切りの単語数は使わないでください。
3. 意味、商品カテゴリ、悩み、売り、便利さ、CTA の有無を保持してください。原文にない購入 CTA や過剰な誇張は足さないでください。
4. 日本のショート動画で自然な口語にしてください。丁寧すぎる説明文ではなく、短く聞き取りやすい「です・ます」調を優先します。
5. 日英混在や直訳調を避け、日本語として自然な語順にしてください。
6. 句読点は日本語の「、」「。」を優先し、1 文を長くしすぎないでください。
7. 字幕として読めるよう、助詞だけを独立させる切れ方を避けてください。

返却形式:
{"sentences":[{"asr_index":0,"text":"...","est_chars":12,"notes":"..."}]}"""

REWRITE_SYSTEM_PROMPT = """あなたは日本語吹き替え台本の尺合わせ担当です。
出力は必ず JSON のみです。

目的:
既存の日本語ローカライズを、測定済み TTS 音声の長さに合わせて、自然な日本語のまま短く/長く調整します。

重要ルール:
1. 入力された各 sentence に対して、必ず同じ asr_index の日本語文を 1 件返してください。
2. 各文の visible Japanese characters は target_chars_range に収めてください。
3. direction が shrink の場合は、意味を保ったまま修飾、重複、弱い感嘆を削ってください。
4. direction が expand の場合は、原文の売りや便利さを自然に補い、冗長な説明にしないでください。
5. 商品、悩み、ベネフィット、CTA の有無は変えないでください。
6. 空白区切りの単語数ではなく、日本語の可視文字数で調整してください。

返却形式:
{"sentences":[{"asr_index":0,"text":"...","est_chars":12,"notes":"..."}]}"""


def count_visible_japanese_chars(text: str) -> int:
    """Count visible characters for Japanese timing, excluding whitespace."""
    return sum(1 for char in str(text or "") if not char.isspace())


def compute_ja_char_range(target_duration: float, voice_id: str | None) -> tuple[int, int]:
    cps = speech_rate_model.get_rate(str(voice_id or ""), TARGET_LANGUAGE)
    if cps is None or cps <= 0:
        cps = FALLBACK_JA_CPS
    duration = max(0.1, float(target_duration or 0.0))
    lo = max(1, int(cps * duration * 0.92))
    hi = max(lo + 1, int(cps * duration * 1.08 + 0.5))
    return lo, hi


def _segment_index(segment: dict, fallback_index: int) -> int:
    return int(segment.get("index", segment.get("asr_index", fallback_index)))


def _segment_start(segment: dict) -> float:
    return float(segment.get("start_time", segment.get("start", 0.0)) or 0.0)


def _segment_end(segment: dict) -> float:
    return float(segment.get("end_time", segment.get("end", 0.0)) or 0.0)


def build_sentence_inputs(script_segments: list[dict], voice_id: str | None) -> list[dict]:
    sentence_inputs: list[dict] = []
    for fallback_index, segment in enumerate(script_segments or []):
        asr_index = _segment_index(segment, fallback_index)
        start_time = _segment_start(segment)
        end_time = _segment_end(segment)
        target_duration = max(0.1, round(end_time - start_time, 3))
        sentence_inputs.append(
            {
                "asr_index": asr_index,
                "start_time": start_time,
                "end_time": end_time,
                "source_text": str(segment.get("text") or ""),
                "target_duration": target_duration,
                "target_chars_range": compute_ja_char_range(target_duration, voice_id),
            }
        )
    return sentence_inputs


def build_source_full_text(script_segments: list[dict]) -> str:
    return "\n".join(
        str(segment.get("text") or "").strip()
        for segment in script_segments or []
        if str(segment.get("text") or "").strip()
    )


def _extract_response_json(response: dict) -> dict:
    if isinstance(response, dict):
        if isinstance(response.get("json"), dict):
            return response["json"]
        if isinstance(response.get("sentences"), list):
            return response
        text = response.get("text")
        if isinstance(text, str) and text.strip():
            return json.loads(text)
    raise ValueError("ja_translate requires a JSON response")


def _merge_output_sentences(raw_sentences: list[dict], sentence_inputs: list[dict]) -> list[dict]:
    raw_by_index: dict[int, dict] = {}
    for item in raw_sentences or []:
        if not isinstance(item, dict):
            continue
        raw_by_index[int(item.get("asr_index", -1))] = item

    missing = [item["asr_index"] for item in sentence_inputs if item["asr_index"] not in raw_by_index]
    if missing:
        raise ValueError(f"ja_translate missing asr_index values: {missing}")

    merged: list[dict] = []
    for ordinal, sentence in enumerate(sentence_inputs):
        raw_item = raw_by_index[sentence["asr_index"]]
        text = re.sub(r"\s+", "", str(raw_item.get("text") or "").strip())
        if not text:
            raise ValueError(f"ja_translate returned empty text for asr_index={sentence['asr_index']}")
        source_segment_indices = [sentence["asr_index"]]
        merged.append(
            {
                "index": ordinal,
                "asr_index": sentence["asr_index"],
                "text": text,
                "est_chars": int(raw_item.get("est_chars") or count_visible_japanese_chars(text)),
                "notes": raw_item.get("notes") or "",
                "source_text": sentence["source_text"],
                "source_segment_indices": source_segment_indices,
                "target_duration": sentence["target_duration"],
                "target_chars_range": sentence["target_chars_range"],
                "start_time": sentence["start_time"],
                "end_time": sentence["end_time"],
            }
        )
    return merged


def build_translate_messages(script_segments: list[dict], voice_id: str | None) -> tuple[list[dict], list[dict]]:
    sentence_inputs = build_sentence_inputs(script_segments, voice_id)
    payload = {
        "target_language": TARGET_LANGUAGE,
        "target_language_name": TARGET_LANGUAGE_NAME,
        "target_market": TARGET_MARKET,
        "char_count_rule": "Count all non-whitespace Japanese visible characters, including punctuation.",
        "sentences": sentence_inputs,
    }
    return (
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        sentence_inputs,
    )


def _allocate_char_targets(char_counts: list[int], target_total_chars: int) -> list[int]:
    if not char_counts:
        return []
    target_total_chars = max(len(char_counts), int(target_total_chars or 0))
    total = sum(max(1, count) for count in char_counts) or len(char_counts)
    raw_targets = [target_total_chars * max(1, count) / total for count in char_counts]
    targets = [max(1, int(value)) for value in raw_targets]
    diff = target_total_chars - sum(targets)
    if diff > 0:
        order = sorted(range(len(raw_targets)), key=lambda i: raw_targets[i] - int(raw_targets[i]), reverse=True)
        for i in order[:diff]:
            targets[i] += 1
    elif diff < 0:
        order = sorted(range(len(targets)), key=lambda i: targets[i], reverse=True)
        remaining = -diff
        for i in order:
            if remaining <= 0:
                break
            removable = min(remaining, max(0, targets[i] - 1))
            targets[i] -= removable
            remaining -= removable
    return targets


def _char_target_range(target_chars: int) -> tuple[int, int]:
    lo = max(1, int(target_chars * 0.92))
    hi = max(lo + 1, int(target_chars * 1.08 + 0.5))
    return lo, hi


def build_rewrite_sentence_inputs(
    localized_translation: dict,
    script_segments: list[dict],
    *,
    target_total_chars: int,
) -> list[dict]:
    source_by_index = {
        _segment_index(segment, fallback_index): segment
        for fallback_index, segment in enumerate(script_segments or [])
    }
    sentences = localized_translation.get("sentences") or []
    char_counts = [count_visible_japanese_chars(sentence.get("text", "")) for sentence in sentences]
    targets = _allocate_char_targets(char_counts, target_total_chars)

    result: list[dict] = []
    for fallback_index, sentence in enumerate(sentences):
        asr_index = int(sentence.get("asr_index", (sentence.get("source_segment_indices") or [fallback_index])[0]))
        source = source_by_index.get(asr_index, {})
        target_chars = targets[fallback_index] if fallback_index < len(targets) else max(1, target_total_chars)
        result.append(
            {
                "asr_index": asr_index,
                "start_time": _segment_start(source),
                "end_time": _segment_end(source),
                "source_text": str(source.get("text") or sentence.get("source_text") or ""),
                "previous_text": _normalize_ja_text(sentence.get("text") or ""),
                "target_chars": target_chars,
                "target_chars_range": _char_target_range(target_chars),
                "source_segment_indices": sentence.get("source_segment_indices") or [asr_index],
                "target_duration": float(sentence.get("target_duration") or max(0.1, _segment_end(source) - _segment_start(source))),
            }
        )
    return result


def build_rewrite_messages(
    localized_translation: dict,
    script_segments: list[dict],
    *,
    target_total_chars: int,
    direction: str,
    last_audio_duration: float,
    video_duration: float,
) -> tuple[list[dict], list[dict]]:
    sentence_inputs = build_rewrite_sentence_inputs(
        localized_translation,
        script_segments,
        target_total_chars=target_total_chars,
    )
    payload = {
        "direction": direction,
        "target_total_chars": int(target_total_chars),
        "last_audio_duration": float(last_audio_duration or 0.0),
        "video_duration": float(video_duration or 0.0),
        "previous_full_text": localized_translation.get("full_text", ""),
        "sentences": sentence_inputs,
    }
    return (
        [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
        sentence_inputs,
    )


def generate_ja_localized_translation(
    *,
    script_segments: list[dict],
    voice_id: str | None,
    user_id: int | None = None,
    project_id: str | None = None,
) -> dict:
    messages, sentence_inputs = build_translate_messages(script_segments, voice_id)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = llm_client.invoke_chat(
                "ja_translate.localize",
                messages=messages,
                user_id=user_id,
                project_id=project_id,
                temperature=0.2,
                max_tokens=8192,
                response_format=JA_TRANSLATE_RESPONSE_FORMAT,
            )
            payload = _extract_response_json(response)
            sentences = _merge_output_sentences(payload.get("sentences") or [], sentence_inputs)
            return {
                "full_text": "".join(sentence["text"] for sentence in sentences),
                "sentences": sentences,
                "_usage": response.get("usage") or {},
                "_messages": messages,
            }
        except Exception as exc:  # pragma: no cover - defensive retry around provider JSON issues
            last_error = exc
            if attempt == 1:
                raise
            time.sleep(0.1)
    if last_error is not None:
        raise last_error
    raise RuntimeError("ja_translate failed without exception")


def rewrite_ja_localized_translation(
    *,
    localized_translation: dict,
    script_segments: list[dict],
    target_total_chars: int,
    direction: str,
    last_audio_duration: float,
    video_duration: float,
    user_id: int | None = None,
    project_id: str | None = None,
) -> dict:
    messages, sentence_inputs = build_rewrite_messages(
        localized_translation,
        script_segments,
        target_total_chars=target_total_chars,
        direction=direction,
        last_audio_duration=last_audio_duration,
        video_duration=video_duration,
    )
    response = llm_client.invoke_chat(
        "ja_translate.rewrite",
        messages=messages,
        user_id=user_id,
        project_id=project_id,
        temperature=0.2,
        max_tokens=8192,
        response_format=JA_TRANSLATE_RESPONSE_FORMAT,
    )
    payload = _extract_response_json(response)
    sentences = _merge_output_sentences(payload.get("sentences") or [], sentence_inputs)
    return {
        "full_text": "".join(sentence["text"] for sentence in sentences),
        "sentences": sentences,
        "_usage": response.get("usage") or {},
        "_messages": messages,
    }


def _normalize_ja_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _split_by_punctuation(text: str) -> list[str]:
    parts = re.findall(r".+?[、。！？!?]|.+$", text)
    return [part for part in parts if part]


def _starts_with_weak_particle(text: str) -> str:
    for starter in sorted(ja_rules.WEAK_STARTERS, key=len, reverse=True):
        if text.startswith(starter):
            return starter
    return ""


def _split_long_ja_part(text: str, max_chars: int = MAX_SUBTITLE_CHARS) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while count_visible_japanese_chars(remaining) > max_chars:
        cut = max_chars
        for punctuation in ("、", "。", "！", "？", "!", "?"):
            pos = remaining.rfind(punctuation, 0, max_chars + 1)
            if pos >= max(6, max_chars // 2):
                cut = pos + 1
                break
        if cut < len(remaining) and _starts_with_weak_particle(remaining[cut:]):
            cut += len(_starts_with_weak_particle(remaining[cut:]))
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _repair_weak_starts(chunks: list[str]) -> list[str]:
    repaired: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        starter = _starts_with_weak_particle(chunk)
        if starter and repaired:
            repaired[-1] += starter
            chunk = chunk[len(starter):]
        if chunk:
            repaired.append(chunk)
    return repaired


def split_ja_subtitle_chunks(text: str, max_chars: int = MAX_SUBTITLE_CHARS) -> list[str]:
    normalized = _normalize_ja_text(text)
    raw_chunks: list[str] = []
    for part in _split_by_punctuation(normalized):
        if count_visible_japanese_chars(part) <= max_chars:
            raw_chunks.append(part)
        else:
            raw_chunks.extend(_split_long_ja_part(part, max_chars=max_chars))
    return _repair_weak_starts(raw_chunks) or ([normalized] if normalized else [])


def build_ja_tts_script(localized_translation: dict) -> dict:
    blocks: list[dict] = []
    subtitle_chunks: list[dict] = []
    for fallback_index, sentence in enumerate(localized_translation.get("sentences") or []):
        text = _normalize_ja_text(sentence.get("text") or "")
        if not text:
            continue
        block_index = len(blocks)
        source_indices = sentence.get("source_segment_indices")
        if not isinstance(source_indices, list) or not source_indices:
            source_indices = [int(sentence.get("asr_index", sentence.get("index", fallback_index)))]
        sentence_index = int(sentence.get("index", fallback_index))
        block = {
            "index": block_index,
            "text": text,
            "source_segment_indices": source_indices,
            "sentence_indices": [sentence_index],
        }
        blocks.append(block)
        for chunk_text in split_ja_subtitle_chunks(text):
            subtitle_chunks.append(
                {
                    "text": chunk_text,
                    "block_indices": [block_index],
                    "source_segment_indices": source_indices,
                }
            )

    if not blocks:
        raise ValueError("ja tts_script requires at least one sentence")

    return {
        "full_text": "".join(block["text"] for block in blocks),
        "blocks": blocks,
        "subtitle_chunks": subtitle_chunks,
    }


def build_ja_tts_segments(tts_script: dict, script_segments: list[dict]) -> list[dict]:
    segments_by_index = {
        _segment_index(segment, fallback_index): segment
        for fallback_index, segment in enumerate(script_segments or [])
    }
    fallback_index = next(iter(segments_by_index), 0)
    result: list[dict] = []
    for block in tts_script.get("blocks") or []:
        indices = [int(i) for i in (block.get("source_segment_indices") or []) if int(i) in segments_by_index]
        if not indices:
            indices = [fallback_index]
        covered = [segments_by_index[index] for index in indices]
        result.append(
            {
                "index": int(block.get("index", len(result))),
                "text": "\n".join(str(segment.get("text") or "") for segment in covered),
                "translated": block.get("text", ""),
                "tts_text": block.get("text", ""),
                "source_segment_indices": indices,
                "start_time": min(_segment_start(segment) for segment in covered),
                "end_time": max(_segment_end(segment) for segment in covered),
            }
        )
    return result


def build_timed_subtitle_chunks(tts_script: dict, tts_segments: list[dict]) -> list[dict]:
    chunks_by_block: dict[int, list[dict]] = {}
    for chunk in tts_script.get("subtitle_chunks") or []:
        block_indices = chunk.get("block_indices") or []
        if not block_indices:
            continue
        chunks_by_block.setdefault(int(block_indices[0]), []).append(chunk)

    timed: list[dict] = []
    cursor = 0.0
    for segment in tts_segments or []:
        block_index = int(segment.get("index", 0))
        duration = float(segment.get("tts_duration") or 0.0)
        if duration <= 0:
            duration = max(0.8, float(segment.get("end_time", 0.0) or 0.0) - float(segment.get("start_time", 0.0) or 0.0))
        block_chunks = chunks_by_block.get(block_index) or [
            {
                "text": segment.get("tts_text") or segment.get("translated") or segment.get("text") or "",
                "block_indices": [block_index],
                "source_segment_indices": segment.get("source_segment_indices") or [],
            }
        ]
        total_chars = sum(max(1, count_visible_japanese_chars(chunk.get("text", ""))) for chunk in block_chunks)
        block_cursor = cursor
        for index, chunk in enumerate(block_chunks):
            chars = max(1, count_visible_japanese_chars(chunk.get("text", "")))
            chunk_duration = duration * chars / total_chars
            if index == len(block_chunks) - 1:
                end_time = cursor + duration
            else:
                end_time = block_cursor + chunk_duration
            timed.append(
                {
                    **chunk,
                    "start_time": round(block_cursor, 3),
                    "end_time": round(end_time, 3),
                }
            )
            block_cursor = end_time
        cursor += duration
    return timed
