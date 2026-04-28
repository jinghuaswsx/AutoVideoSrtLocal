"""ElevenLabs Scribe ASR adapter。

API：https://elevenlabs.io/docs/api-reference/speech-to-text/convert
- POST https://api.elevenlabs.io/v1/speech-to-text
- Auth：xi-api-key header
- Body：multipart/form-data：file（binary）+ model_id + 可选 language_code (ISO-639-1)
        + timestamps_granularity="word"
- Response：{language_code, language_probability, text,
            words: [{text, start, end, type, logprob, ...}],
            audio_duration_secs, ...}

Scribe 没有原生 sentence 分段，只输出 word-level，本 adapter 按标点 + 静音 gap
聚合为 sentence Utterance。

凭据复用 elevenlabs_tts（同一个 ElevenLabs 账号）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List

import requests

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_api_key,
)

from .base import ASRCapabilities, BaseASRAdapter, Utterance

log = logging.getLogger(__name__)

_SCRIBE_ENDPOINT = "https://api.elevenlabs.io/v1/speech-to-text"
_DEFAULT_MODEL_ID = "scribe_v2"
_REQUEST_TIMEOUT_SEC = 600  # 长视频可能要几分钟

_SENTENCE_END_PUNCT: tuple[str, ...] = (".", "!", "?", "。", "！", "？", "…")
_SILENCE_GAP_SEC = 0.6
_MAX_UNPUNCTUATED_SEGMENT_SEC = 6.0


def _resolve_elevenlabs_api_key() -> str:
    try:
        return require_provider_api_key("elevenlabs_tts")
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc


class ScribeAdapter(BaseASRAdapter):
    provider_code = "elevenlabs_tts"
    display_name = "ElevenLabs Scribe"
    default_model_id = _DEFAULT_MODEL_ID
    capabilities = ASRCapabilities(
        supports_force_language=True,
        supported_languages=frozenset({"*"}),
        accepts_local_file=True,
    )

    def transcribe(
        self,
        local_audio_path: Path,
        language: str | None = None,
    ) -> List[Utterance]:
        return self._transcribe(str(local_audio_path), language=language)

    def _transcribe(
        self,
        local_audio_path: str,
        *,
        language: str | None = None,
        api_key: str | None = None,
    ) -> List[Utterance]:
        resolved_key = api_key or _resolve_elevenlabs_api_key()
        log.info(
            "[Scribe] 开始识别 file=%s model=%s language=%s",
            os.path.basename(local_audio_path),
            self.model_id,
            language or "auto",
        )
        headers = {"xi-api-key": resolved_key}
        data: dict[str, str] = {
            "model_id": self.model_id or _DEFAULT_MODEL_ID,
            "timestamps_granularity": "word",
        }
        if language:
            data["language_code"] = language

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
            "[Scribe] 完成 language=%s confidence=%.2f duration=%.1fs",
            payload.get("language_code"),
            float(payload.get("language_probability") or 0.0),
            float(payload.get("audio_duration_secs") or 0.0),
        )
        segments = parse_scribe_response(payload)
        log.info("[Scribe] 聚合为 %d 段", len(segments))
        return segments


def parse_scribe_response(payload: dict) -> List[Utterance]:
    """聚合 Scribe word-level 输出为 sentence Utterance。

    断句规则：句末标点（. ! ? 。 ！ ？ …）或静音 gap > 0.6s。
    """
    words_raw = payload.get("words") or []

    if not words_raw:
        full_text = (payload.get("text") or "").strip()
        if not full_text:
            return []
        duration = float(payload.get("audio_duration_secs") or 0.0)
        return [
            {
                "text": full_text,
                "start_time": 0.0,
                "end_time": duration,
                "words": [],
            }
        ]

    word_items = [
        w
        for w in words_raw
        if (w.get("type") in (None, "word"))
        and (w.get("text") or "").strip()
    ]
    if not word_items:
        return []

    sentences: List[Utterance] = []
    current_words: list = []

    def _flush_current(*, force_break_after: bool = False) -> None:
        nonlocal current_words
        sent_text = " ".join(
            (w.get("text") or "").strip() for w in current_words
        ).strip()
        if sent_text:
            sentence: Utterance = {
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
            }
            if force_break_after:
                sentence["force_break_after"] = True
            sentences.append(sentence)
        current_words = []

    for i, word in enumerate(word_items):
        if current_words:
            current_start = float(current_words[0].get("start") or 0.0)
            prospective_end = float(word.get("end") or 0.0)
            if (
                len(current_words) > 1
                and prospective_end - current_start > _MAX_UNPUNCTUATED_SEGMENT_SEC
            ):
                _flush_current(force_break_after=True)
        current_words.append(word)
        text = (word.get("text") or "").strip()
        is_sentence_end = any(text.endswith(p) for p in _SENTENCE_END_PUNCT)
        is_last_word = i == len(word_items) - 1

        next_gap = 0.0
        if i + 1 < len(word_items):
            next_start = float(word_items[i + 1].get("start") or 0.0)
            cur_end = float(word.get("end") or 0.0)
            next_gap = next_start - cur_end

        current_start = float(current_words[0].get("start") or 0.0)
        current_end = float(word.get("end") or 0.0)
        is_too_long = (
            len(current_words) > 1
            and not is_sentence_end
            and not is_last_word
            and current_end - current_start >= _MAX_UNPUNCTUATED_SEGMENT_SEC
        )

        if is_sentence_end or is_last_word or next_gap > _SILENCE_GAP_SEC or is_too_long:
            _flush_current(force_break_after=is_too_long)

    return sentences
