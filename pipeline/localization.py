from __future__ import annotations

import json
import re


VARIANT_KEYS = ("normal", "hook_cta")
VARIANT_LABELS = {
    "normal": "普通版",
    "hook_cta": "黄金3秒 + CTA版",
}

LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a US short-video commerce copywriter.
Return valid JSON only.
Translate the Chinese source into natural, native, sales-capable American English.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices.
Keep each sentence concise and punchy for subtitles. Prefer 6-10 words and avoid long compound sentences.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks."""

HOOK_CTA_TRANSLATION_SYSTEM_PROMPT = """You are a US short-video e-commerce copywriter.
Return valid JSON only.
Translate the Chinese source into natural, native, sales-capable American English.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices.
Keep each sentence concise and punchy for subtitles. Prefer 6-10 words and avoid long compound sentences.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.
Sentence 1 must function as the first-3-seconds hook for a US short-form video.
Treat the first 3 spoken seconds as roughly the first 7-10 English words.
Sentence 1 should prioritize one of these hook patterns: strong outcome, obvious benefit, curiosity, or surprise contrast.
The full script must contain exactly one clear purchase CTA.
Put the CTA where it feels most natural, usually in the middle or near the end.
You may reorder emphasis to improve hook performance, but you must preserve the original selling points."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing text for ElevenLabs narration and subtitle display.
Return valid JSON only.
Use the localized English as the only wording source.
blocks optimize speaking rhythm.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text.
Each subtitle chunk should usually be 5-10 words.
Avoid 1-3 word fragments unless there is no natural way to merge them.
Prefer semantically complete chunks that still read naturally on screen.
Do not end subtitle_chunks with punctuation.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks."""

LOCALIZED_TRANSLATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "localized_translation",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "full_text": {"type": "string"},
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["index", "text", "source_segment_indices"],
                    },
                },
            },
            "required": ["full_text", "sentences"],
        },
    },
}

TTS_SCRIPT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "tts_script",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "full_text": {"type": "string"},
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "sentence_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["index", "text", "sentence_indices", "source_segment_indices"],
                    },
                },
                "subtitle_chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "block_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "sentence_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": [
                            "index",
                            "text",
                            "block_indices",
                            "sentence_indices",
                            "source_segment_indices",
                        ],
                    },
                },
            },
            "required": ["full_text", "blocks", "subtitle_chunks"],
        },
    },
}


def build_source_full_text_zh(script_segments: list[dict]) -> str:
    return "\n".join(
        (segment.get("text") or "").strip()
        for segment in script_segments
        if (segment.get("text") or "").strip()
    )


def _concat_items(items: list[dict], key: str) -> str:
    return " ".join(
        (item.get(key) or "").strip()
        for item in items
        if (item.get(key) or "").strip()
    ).strip()


def _sanitize_model_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.replace("’", "'").replace("‘", "'")
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = re.sub(r"[–—―]", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    return cleaned.strip()


def _sanitize_text_items(items: list[dict], text_key: str) -> list[dict]:
    normalized = []
    for item in items:
        item_copy = dict(item)
        item_copy[text_key] = _sanitize_model_text(item_copy.get(text_key, ""))
        normalized.append(item_copy)
    return normalized


def _subtitle_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text))


def _subtitle_word_signature(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text)]


def _strip_terminal_punctuation(text: str) -> str:
    return re.sub(r"[,.!?;:]+$", "", (text or "").strip()).strip()


def _capitalize_first_character(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    return cleaned[0].upper() + cleaned[1:]


def _split_block_text_balanced(text: str, min_words: int = 5, max_words: int = 10) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [text.strip()]

    weak_starters = {"and", "or", "but", "to", "for", "with", "of", "the", "a", "an"}
    chunk_count = max(1, (len(words) + max_words - 1) // max_words)
    base_size = len(words) // chunk_count
    remainder = len(words) % chunk_count
    target_sizes = [base_size + (1 if index < remainder else 0) for index in range(chunk_count)]

    chunks: list[str] = []
    cursor = 0

    for chunk_index in range(chunk_count):
        remaining_words = len(words) - cursor
        remaining_chunks = chunk_count - chunk_index
        if remaining_chunks == 1:
            chunks.append(" ".join(words[cursor:]).strip())
            break

        min_size = max(min_words, remaining_words - max_words * (remaining_chunks - 1))
        max_size = min(max_words, remaining_words - min_words * (remaining_chunks - 1))
        target_size = target_sizes[chunk_index]

        best_end = cursor + min_size
        best_score = None
        for size in range(min_size, max_size + 1):
            end = cursor + size
            previous_token = words[end - 1]
            next_token = words[end] if end < len(words) else ""

            score = abs(size - target_size)
            if previous_token.endswith((",", ";", ":", ".", "!", "?")):
                score -= 1.2
            if next_token.strip(",.;:!?").lower() in weak_starters:
                score += 0.85
            if previous_token.strip(",.;:!?").lower() in weak_starters:
                score += 0.55

            if best_score is None or score < best_score:
                best_score = score
                best_end = end

        chunks.append(" ".join(words[cursor:best_end]).strip())
        cursor = best_end

    return [chunk for chunk in chunks if chunk]


def _merge_chunk_dicts(left: dict, right: dict) -> dict:
    return {
        "text": f"{left['text']} {right['text']}".strip(),
        "block_indices": sorted(set((left.get("block_indices") or []) + (right.get("block_indices") or []))),
        "sentence_indices": sorted(set((left.get("sentence_indices") or []) + (right.get("sentence_indices") or []))),
        "source_segment_indices": sorted(
            set((left.get("source_segment_indices") or []) + (right.get("source_segment_indices") or []))
        ),
    }


def _finalize_subtitle_chunk(chunk: dict) -> dict:
    chunk_copy = dict(chunk)
    chunk_copy["text"] = _capitalize_first_character(
        _strip_terminal_punctuation(chunk_copy.get("text", ""))
    )
    return chunk_copy


def _merge_short_subtitle_chunks(chunks: list[dict], min_words: int = 5, max_words: int = 10) -> list[dict]:
    merged = [dict(chunk) for chunk in chunks]

    while True:
        changed = False
        index = 0
        while index < len(merged):
            current = merged[index]
            current_words = _subtitle_word_count(current["text"])
            if current_words >= min_words:
                index += 1
                continue

            candidates = []
            if index > 0:
                previous = merged[index - 1]
                if previous.get("sentence_indices") == current.get("sentence_indices"):
                    merged_prev = _merge_chunk_dicts(previous, current)
                    if _subtitle_word_count(merged_prev["text"]) <= max_words:
                        candidates.append(("prev", merged_prev))
            if index + 1 < len(merged):
                following = merged[index + 1]
                if following.get("sentence_indices") == current.get("sentence_indices"):
                    merged_next = _merge_chunk_dicts(current, following)
                    if _subtitle_word_count(merged_next["text"]) <= max_words:
                        candidates.append(("next", merged_next))

            if not candidates:
                index += 1
                continue

            target_size = 7
            direction, replacement = min(
                candidates,
                key=lambda item: abs(_subtitle_word_count(item[1]["text"]) - target_size),
            )
            if direction == "prev":
                merged[index - 1] = replacement
                del merged[index]
            else:
                merged[index] = replacement
                del merged[index + 1]
            changed = True
            break

        if not changed:
            break

    finalized = []
    for index, chunk in enumerate(merged):
        chunk_copy = _finalize_subtitle_chunk(chunk)
        chunk_copy["index"] = index
        finalized.append(chunk_copy)
    return finalized


def _rebuild_subtitle_chunks(blocks: list[dict], min_words: int = 5, max_words: int = 10) -> list[dict]:
    rebuilt: list[dict] = []

    for block in blocks:
        pieces = _split_block_text_balanced(
            block.get("text", ""),
            min_words=min_words,
            max_words=max_words,
        )
        for piece in pieces:
            rebuilt.append(
                {
                    "text": piece,
                    "block_indices": [block["index"]],
                    "sentence_indices": list(block.get("sentence_indices") or []),
                    "source_segment_indices": list(block.get("source_segment_indices") or []),
                }
            )

    return _merge_short_subtitle_chunks(rebuilt, min_words=min_words, max_words=max_words)


def build_localized_translation_messages(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    if custom_system_prompt:
        prompt = custom_system_prompt
    elif variant == "hook_cta":
        prompt = HOOK_CTA_TRANSLATION_SYSTEM_PROMPT
    else:
        prompt = LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                "Source Chinese full text:\n"
                f"{source_full_text_zh}\n\n"
                "Source Chinese segments:\n"
                f"{json.dumps(items, ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def build_tts_script_messages(localized_translation: dict) -> list[dict]:
    return [
        {"role": "system", "content": TTS_SCRIPT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(localized_translation, ensure_ascii=False, indent=2),
        },
    ]


def build_tts_segments(tts_script: dict, script_segments: list[dict]) -> list[dict]:
    segments_by_index = {segment["index"]: segment for segment in script_segments}
    result = []

    for block in tts_script.get("blocks", []):
        indices = block["source_segment_indices"]
        covered = [segments_by_index[index] for index in indices]
        result.append(
            {
                "index": block["index"],
                "text": "\n".join(segment["text"] for segment in covered),
                "translated": block["text"],
                "tts_text": block["text"],
                "source_segment_indices": indices,
                "start_time": min(segment["start_time"] for segment in covered),
                "end_time": max(segment["end_time"] for segment in covered),
            }
        )

    return result


def validate_localized_translation(payload: dict) -> dict:
    sentences = _sanitize_text_items(payload.get("sentences") or [], "text")
    full_text = _sanitize_model_text(payload.get("full_text") or "")
    if not full_text or not sentences:
        raise ValueError("localized_translation requires full_text and sentences")

    for sentence in sentences:
        indices = sentence.get("source_segment_indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError("localized_translation sentence missing source_segment_indices")

    if _concat_items(sentences, "text") != full_text:
        raise ValueError("localized_translation full_text does not match sentences")

    return {"full_text": full_text, "sentences": sentences}


def validate_tts_script(payload: dict) -> dict:
    blocks = _sanitize_text_items(payload.get("blocks") or [], "text")
    full_text = _sanitize_model_text(payload.get("full_text") or "")
    if not full_text or not blocks:
        raise ValueError("tts_script requires full_text and blocks")

    if _concat_items(blocks, "text") != full_text:
        raise ValueError("tts_script blocks do not match full_text")

    subtitle_chunks = _rebuild_subtitle_chunks(blocks, min_words=5, max_words=10)
    if not subtitle_chunks:
        raise ValueError("tts_script could not rebuild subtitle_chunks from blocks")
    if _subtitle_word_signature(_concat_items(subtitle_chunks, "text")) != _subtitle_word_signature(full_text):
        raise ValueError("tts_script subtitle_chunks do not match full_text")
    for chunk in subtitle_chunks:
        if _subtitle_word_count(chunk["text"]) > 10:
            raise ValueError("tts_script subtitle_chunks must be 10 words or fewer")

    return {
        "full_text": full_text,
        "blocks": blocks,
        "subtitle_chunks": subtitle_chunks,
    }


LOCALIZED_TRANSLATION_SYSTEM_PROMPT_ZH = """你是一名美国短视频电商文案写手。
仅返回合法 JSON。
将中文原文翻译成自然、地道、具有销售力的美式英语。
可以本土化表达方式，但每句话必须保留原意并包含 source_segment_indices。
每句保持简洁有力，适合字幕显示。优选 6-10 个单词，避免长复合句。
不要使用破折号。仅使用纯 ASCII 标点，优选逗号、句号和问号。"""

HOOK_CTA_TRANSLATION_SYSTEM_PROMPT_ZH = """你是一名美国短视频电商文案写手。
仅返回合法 JSON。
将中文原文翻译成自然、地道、具有销售力的美式英语。
可以本土化表达方式，但每句话必须保留原意并包含 source_segment_indices。
每句保持简洁有力，适合字幕显示。优选 6-10 个单词，避免长复合句。
不要使用破折号。仅使用纯 ASCII 标点，优选逗号、句号和问号。
第 1 句必须作为美国短视频的前 3 秒钩子。
前 3 秒口播大约对应英文前 7-10 个单词。
第 1 句应优先使用以下钩子模式之一：强结果、明显好处、好奇心或反差惊喜。
完整脚本必须包含恰好一个清晰的购买 CTA。
CTA 放在最自然的位置，通常在中间或接近结尾。
可以重新排列重点以提高钩子效果，但必须保留原始卖点。"""

DEFAULT_PROMPTS = [
    {"name": "普通翻译", "prompt_text": LOCALIZED_TRANSLATION_SYSTEM_PROMPT, "prompt_text_zh": LOCALIZED_TRANSLATION_SYSTEM_PROMPT_ZH, "is_default": True},
    {"name": "黄金3秒+CTA", "prompt_text": HOOK_CTA_TRANSLATION_SYSTEM_PROMPT, "prompt_text_zh": HOOK_CTA_TRANSLATION_SYSTEM_PROMPT_ZH, "is_default": True},
]
