"""ElevenLabs Scribe ASR adapter.

API reference: https://elevenlabs.io/docs/api-reference/speech-to-text/convert
- POST https://api.elevenlabs.io/v1/speech-to-text
- Auth: xi-api-key header
- Body: multipart/form-data with file (binary) + model_id (required) +
  optional language_code (ISO-639-1) + timestamps_granularity="word"
- Response: {language_code, language_probability, text,
            words: [{text, start, end, type, logprob, ...}],
            audio_duration_secs, ...}

Why we need this: pipeline.asr (Doubao SeedASR) is strong for zh/en but
unreliable for Spanish/Portuguese/German source. Scribe covers 99 languages
with word-level timestamps, allowing us to keep zh/en on Doubao and route
non-zh-non-en sources here.

Output is aligned to pipeline.asr.transcribe shape so the rest of the
pipeline does not need to know which engine produced the transcript:
    [{"text": str, "start_time": float, "end_time": float,
      "words": [{"text", "start_time", "end_time", "confidence"}, ...]}]
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import requests

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_api_key,
)

log = logging.getLogger(__name__)

_SCRIBE_ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"
_DEFAULT_MODEL_ID = "scribe_v2"
_REQUEST_TIMEOUT_SEC = 600  # 长视频可能要几分钟，留宽

# Sentence segmentation tuning (Scribe returns word-level only).
_SENTENCE_END_PUNCT: tuple[str, ...] = (".", "!", "?", "。", "！", "？", "…")
_SILENCE_GAP_SEC = 0.6


def _resolve_elevenlabs_api_key() -> str:
    try:
        return require_provider_api_key("elevenlabs_tts")
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc


def transcribe_local_audio(
    local_audio_path: str,
    language_code: str | None = None,
    *,
    api_key: str | None = None,
    model_id: str = _DEFAULT_MODEL_ID,
) -> List[Dict]:
    """Send a local audio/video file to ElevenLabs Scribe and parse to segments.

    Args:
        local_audio_path: 本地音频/视频路径（mp3/wav/m4a/mp4 等 Scribe 支持格式）。
        language_code: ISO-639-1 语言码（如 "es"）。None 时让 Scribe 自动识别。
        api_key: 显式覆盖 ElevenLabs API key。None 时使用系统级 elevenlabs_tts。
        model_id: 默认 "scribe_v2"。

    Returns:
        段列表，与 pipeline.asr.transcribe 同结构：
        [{"text", "start_time", "end_time", "words": [...]}, ...]
    """
    resolved_key = api_key or _resolve_elevenlabs_api_key()

    log.info(
        "[Scribe] 开始识别，file=%s, model=%s, language=%s",
        os.path.basename(local_audio_path),
        model_id,
        language_code or "auto",
    )

    headers = {"xi-api-key": resolved_key}
    data: dict[str, str] = {
        "model_id": model_id,
        "timestamps_granularity": "word",
    }
    if language_code:
        data["language_code"] = language_code

    with open(local_audio_path, "rb") as f:
        files = {"file": (os.path.basename(local_audio_path), f)}
        resp = requests.post(
            _SCRIBE_ENDPOINT,
            headers=headers,
            data=data,
            files=files,
            timeout=_REQUEST_TIMEOUT_SEC,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Scribe ASR 失败 (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    payload = resp.json()
    log.info(
        "[Scribe] 识别完成 language=%s confidence=%.2f duration=%.1fs",
        payload.get("language_code"),
        float(payload.get("language_probability") or 0.0),
        float(payload.get("audio_duration_secs") or 0.0),
    )

    segments = _parse_scribe_response(payload)
    log.info("[Scribe] 聚合为 %d 个 sentence 片段", len(segments))
    return segments


def _parse_scribe_response(payload: dict) -> List[Dict]:
    """聚合 Scribe word-level 输出为 sentence segments，模拟豆包 utterances 形态。

    Scribe 没有原生的句子分段，只输出每个 word 的 (start, end, text)。我们按
    标点（. ! ? 。 ！ ？ …）和静音 gap > 0.6s 两条规则做断句。
    """
    words_raw = payload.get("words") or []

    if not words_raw:
        # 完全没词级时间戳：用整段文本 + audio_duration 兜底
        full_text = (payload.get("text") or "").strip()
        if not full_text:
            return []
        duration = float(payload.get("audio_duration_secs") or 0.0)
        return [{
            "text": full_text,
            "start_time": 0.0,
            "end_time": duration,
            "words": [],
        }]

    # 只保留实际词，丢掉 spacing / audio_event 占位
    word_items = [
        w for w in words_raw
        if (w.get("type") in (None, "word"))
        and (w.get("text") or "").strip()
    ]
    if not word_items:
        return []

    sentences: List[Dict] = []
    current_words: list = []

    for i, word in enumerate(word_items):
        current_words.append(word)
        text = (word.get("text") or "").strip()
        is_sentence_end = any(text.endswith(p) for p in _SENTENCE_END_PUNCT)
        is_last_word = i == len(word_items) - 1

        next_gap = 0.0
        if i + 1 < len(word_items):
            next_start = float(word_items[i + 1].get("start") or 0.0)
            cur_end = float(word.get("end") or 0.0)
            next_gap = next_start - cur_end

        if is_sentence_end or is_last_word or next_gap > _SILENCE_GAP_SEC:
            sent_text = " ".join(
                (w.get("text") or "").strip() for w in current_words
            ).strip()
            if sent_text:
                sentences.append({
                    "text": sent_text,
                    "start_time": float(current_words[0].get("start") or 0.0),
                    "end_time": float(current_words[-1].get("end") or 0.0),
                    "words": [
                        {
                            "text": w.get("text") or "",
                            "start_time": float(w.get("start") or 0.0),
                            "end_time": float(w.get("end") or 0.0),
                            "confidence": float(w.get("logprob") or 0.0),
                        }
                        for w in current_words
                    ],
                })
            current_words = []

    return sentences
