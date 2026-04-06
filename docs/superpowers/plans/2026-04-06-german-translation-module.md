# 视频翻译（德语）模块实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的"视频翻译（德语）"板块，支持中文/英文源视频翻译成德语。

**Architecture:** 独立板块，复用 pipeline 层（小幅参数化），新建 runtime_de + web 路由 + 模板。通过 PipelineRunner 子类覆写翻译/TTS/字幕步骤实现德语特化。

**Tech Stack:** Flask, ElevenLabs (eleven_multilingual_v2), Claude via OpenRouter, ffmpeg, PySceneDetect

---

## Task 1: 创建德语翻译 Prompt 模块

**Files:**
- Create: `pipeline/localization_de.py`

- [ ] **Step 1: 创建 pipeline/localization_de.py**

```python
"""German localization prompts and constants.

Reuses JSON schemas and validation from pipeline.localization.
Only defines German-specific prompts, weak starters, and message builders.
"""
from __future__ import annotations

import json

from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    validate_localized_translation,
    validate_tts_script,
    build_source_full_text_zh,
    build_tts_segments,
)

# Re-export for convenience
__all__ = [
    "LOCALIZED_TRANSLATION_RESPONSE_FORMAT",
    "TTS_SCRIPT_RESPONSE_FORMAT",
    "validate_localized_translation",
    "validate_tts_script",
    "build_source_full_text_zh",
    "build_tts_segments",
    "LOCALIZED_TRANSLATION_SYSTEM_PROMPT",
    "TTS_SCRIPT_SYSTEM_PROMPT",
    "WEAK_STARTERS_DE",
    "MAX_CHARS_PER_LINE",
    "DEFAULT_MALE_VOICE_ID",
    "DEFAULT_FEMALE_VOICE_ID",
    "TTS_MODEL_ID",
    "TTS_LANGUAGE_CODE",
    "build_localized_translation_messages",
    "build_tts_script_messages",
]

# ── 德语字幕参数 ──────────────────────────────────────
WEAK_STARTERS_DE = {
    "und", "oder", "der", "die", "das", "ein", "eine", "einem", "einen", "einer",
    "für", "mit", "von", "zu", "zum", "zur", "aber", "auch", "wenn", "dass",
    "den", "dem", "des", "auf", "aus", "bei", "bis", "nach", "über", "unter",
}
MAX_CHARS_PER_LINE = 38

# ── 德语 TTS 配置 ──────────────────────────────────────
TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "de"
DEFAULT_MALE_VOICE_ID = "eEmoQJhC4SAEQpCINUov"      # Toby
DEFAULT_FEMALE_VOICE_ID = "ViKqgJNeCiWZlYgHiAOO"    # Annika

# ── 翻译系统提示 ──────────────────────────────────────
LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a German short-video e-commerce content creator.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}
Translate the source text into natural, fluent German suitable for e-commerce short videos on TikTok and Instagram Reels.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices.
Keep each sentence concise for subtitles. Prefer 6-12 words and avoid long compound sentences (Schachtelsätze).
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.
Write authentically and factually (sachlich und authentisch). No exaggerated claims or artificial urgency.
Emphasize quality and practical value over discounts. German audiences react negatively to aggressive selling.
Use conversational German at B1 level, natural but not overly casual.
Capitalize all nouns as required by German grammar.
For numbers, use German conventions (e.g. use Komma for decimals: 2,5 not 2.5)."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing German text for ElevenLabs narration and subtitle display.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...], "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...]}
Use the localized German text as the only wording source.
blocks optimize speaking rhythm for German narration.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text.
Each subtitle chunk should usually be 4-8 words (German words tend to be longer than English).
Avoid 1-2 word fragments unless there is no natural way to merge them.
Prefer semantically complete chunks that still read naturally on screen.
Do not end subtitle_chunks with punctuation.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks."""


def build_localized_translation_messages(
    source_full_text: str,
    script_segments: list[dict],
    source_language: str = "zh",
    custom_system_prompt: str | None = None,
) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    prompt = custom_system_prompt or LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {lang_label} full text:\n"
                f"{source_full_text}\n\n"
                f"Source {lang_label} segments:\n"
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
```

- [ ] **Step 2: 提交**

```bash
git add pipeline/localization_de.py
git commit -m "feat: 新增德语翻译 Prompt 模块 localization_de.py"
```

---

## Task 2: 参数化 pipeline/tts.py 支持多语言

**Files:**
- Modify: `pipeline/tts.py:36-49` (generate_segment_audio)
- Modify: `pipeline/tts.py:52-95` (generate_full_audio)

- [ ] **Step 1: 修改 generate_segment_audio，增加 language_code 和 model_id 参数**

在 `pipeline/tts.py` 中，修改 `generate_segment_audio` 函数签名和调用：

```python
def generate_segment_audio(
    text: str,
    voice_id: str,
    output_path: str,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
) -> str:
    """生成单段音频，返回文件路径（mp3）"""
    client = _get_client(api_key=elevenlabs_api_key)
    kwargs = dict(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format="mp3_44100_128",
    )
    if language_code:
        kwargs["language_code"] = language_code
    audio = client.text_to_speech.convert(**kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return output_path
```

- [ ] **Step 2: 修改 generate_full_audio，透传 language_code 和 model_id**

```python
def generate_full_audio(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
) -> Dict:
```

在调用 `generate_segment_audio` 处透传：

```python
generate_segment_audio(
    text, voice_id, seg_path,
    elevenlabs_api_key=elevenlabs_api_key,
    model_id=model_id,
    language_code=language_code,
)
```

- [ ] **Step 3: 提交**

```bash
git add pipeline/tts.py
git commit -m "feat: tts.py 支持 language_code 和 model_id 参数"
```

---

## Task 3: 参数化 pipeline/translate.py 的 generate_tts_script

**Files:**
- Modify: `pipeline/translate.py:116-151` (generate_tts_script)

- [ ] **Step 1: 给 generate_tts_script 增加 messages_builder 和 validator 参数**

```python
def generate_tts_script(
    localized_translation: dict,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
) -> dict:
    client, model = resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    rf = response_format_override or TTS_SCRIPT_RESPONSE_FORMAT
    if provider != "doubao":
        extra_body["response_format"] = rf
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    builder = messages_builder or build_tts_script_messages
    response = client.chat.completions.create(
        model=model,
        messages=builder(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        **( {"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("tts_script raw response (provider=%s): %s", provider, raw_content[:2000])
    payload = parse_json_content(raw_content)
    log.info("tts_script parsed payload type=%s keys=%s", type(payload).__name__, list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    validate_fn = validator or validate_tts_script
    result = validate_fn(payload)
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
        log.info("tts_script token usage: input=%s, output=%s",
                 result["_usage"]["input_tokens"], result["_usage"]["output_tokens"])
    return result
```

注意：原有调用方式 `generate_tts_script(localized_translation, provider=..., user_id=...)` 完全兼容，不需要改任何现有调用点。

- [ ] **Step 2: 提交**

```bash
git add pipeline/translate.py
git commit -m "feat: generate_tts_script 支持自定义 messages_builder 和 validator"
```

---

## Task 4: 参数化 pipeline/subtitle.py 支持德语断行

**Files:**
- Modify: `pipeline/subtitle.py:37-58` (_choose_balanced_split)
- Modify: `pipeline/subtitle.py:61-72` (format_subtitle_chunk_text)

- [ ] **Step 1: 给 _choose_balanced_split 增加 weak_boundary_words 参数**

```python
def _choose_balanced_split(words: List[str], weak_boundary_words: set | None = None) -> int:
    if weak_boundary_words is None:
        weak_boundary_words = {"and", "or", "to", "of", "for", "with", "the", "a", "an"}
    best_index = max(1, len(words) // 2)
    best_score = None

    for index in range(2, len(words) - 1):
        left_count = index
        right_count = len(words) - index
        score = abs(left_count - right_count)

        if words[index - 1].strip(",").lower() in weak_boundary_words:
            score += 1.0
        if words[index].strip(",").lower() in weak_boundary_words:
            score += 1.0
        if words[index - 1].endswith(","):
            score -= 0.25

        if best_score is None or score < best_score:
            best_score = score
            best_index = index

    return best_index
```

- [ ] **Step 2: 给 format_subtitle_chunk_text 增加 weak_boundary_words 参数**

```python
def format_subtitle_chunk_text(text: str, weak_boundary_words: set | None = None) -> str:
    cleaned = capitalize_sentence(_strip_terminal_punctuation(text))
    words = cleaned.split()
    if len(words) <= 5:
        return cleaned

    split_index = _choose_balanced_split(words, weak_boundary_words=weak_boundary_words)
    line1 = " ".join(words[:split_index]).strip()
    line2 = " ".join(words[split_index:]).strip()
    if not line1 or not line2:
        return cleaned
    return f"{line1}\n{line2}"
```

- [ ] **Step 3: 给 build_srt_from_chunks 增加 weak_boundary_words 参数**

```python
def build_srt_from_chunks(chunks: List[Dict], weak_boundary_words: set | None = None) -> str:
    srt_lines = []
    for i, chunk in enumerate(chunks, 1):
        srt_lines.append(str(i))
        srt_lines.append(
            f"{format_timestamp(float(chunk['start_time']))} --> {format_timestamp(float(chunk['end_time']))}"
        )
        srt_lines.append(format_subtitle_chunk_text(chunk["text"], weak_boundary_words=weak_boundary_words))
        srt_lines.append("")

    return "\n".join(srt_lines)
```

- [ ] **Step 4: 提交**

```bash
git add pipeline/subtitle.py
git commit -m "feat: subtitle.py 支持自定义弱边界词集合参数"
```

---

## Task 5: 创建德语流水线编排 runtime_de.py

**Files:**
- Create: `appcore/runtime_de.py`

- [ ] **Step 1: 创建 appcore/runtime_de.py**

```python
"""German translation pipeline runner.

Subclasses PipelineRunner, overriding translate/tts/subtitle steps
for German-specific prompts, TTS model, and subtitle rules.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

log = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.events import (
    EVT_ALIGNMENT_READY,
    EVT_ASR_RESULT,
    EVT_CAPCUT_READY,
    EVT_ENGLISH_ASR_RESULT,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    EventBus,
)
from appcore.runtime import PipelineRunner, _build_review_segments, _save_json, _resolve_translate_provider
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


class DeTranslateRunner(PipelineRunner):
    """German-specific pipeline runner."""

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"
        self._set_step(task_id, "translate", "running", f"正在将{lang_label}翻译为德语...")

        from pipeline.localization_de import (
            build_localized_translation_messages as build_de_messages,
            build_source_full_text_zh,
            validate_localized_translation,
        )
        from pipeline.translate import (
            generate_localized_translation,
            get_model_display_name,
        )

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")

        from pipeline.localization_de import LOCALIZED_TRANSLATION_SYSTEM_PROMPT as DE_PROMPT
        system_prompt = custom_prompt or DE_PROMPT

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant=variant,
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text,
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text))
        task_state.set_artifact(task_id, "translate", build_translate_artifact(source_full_text, localized_translation))

        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        from appcore.usage_log import record as _log_usage
        _translate_usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                   model_name=get_model_display_name(provider, self.user_id),
                   success=True,
                   input_tokens=_translate_usage.get("input_tokens"),
                   output_tokens=_translate_usage.get("output_tokens"))

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "德语翻译结果已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "德语本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "tts", "running", "正在生成德语配音...")
        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration
        from pipeline.localization_de import (
            TTS_LANGUAGE_CODE,
            TTS_MODEL_ID,
            DEFAULT_MALE_VOICE_ID,
            DEFAULT_FEMALE_VOICE_ID,
            build_tts_script_messages as build_de_tts_messages,
            build_tts_segments,
            validate_tts_script,
        )
        from pipeline.timeline import build_timeline_manifest
        from pipeline.translate import generate_tts_script, get_model_display_name
        from pipeline.tts import generate_full_audio, get_voice_by_id

        provider = _resolve_translate_provider(self.user_id)
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")

        # Voice resolution: user-selected > default German voice
        voice = None
        if task.get("voice_id"):
            voice = get_voice_by_id(task["voice_id"], self.user_id)
        if not voice:
            gender = task.get("voice_gender", "male")
            de_voice_id = DEFAULT_MALE_VOICE_ID if gender == "male" else DEFAULT_FEMALE_VOICE_ID
            voice = {
                "id": None,
                "elevenlabs_voice_id": de_voice_id,
                "name": "Toby" if gender == "male" else "Annika",
            }

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        localized_translation = variant_state.get("localized_translation", {})
        video_duration = get_video_duration(task["video_path"])

        tts_script = generate_tts_script(
            localized_translation,
            provider=provider,
            user_id=self.user_id,
            messages_builder=build_de_tts_messages,
            validator=validate_tts_script,
        )
        tts_segments = build_tts_segments(tts_script, task.get("script_segments", []))
        result = generate_full_audio(
            tts_segments,
            voice["elevenlabs_voice_id"],
            task_dir,
            variant=variant,
            elevenlabs_api_key=elevenlabs_api_key,
            model_id=TTS_MODEL_ID,
            language_code=TTS_LANGUAGE_CODE,
        )
        timeline_manifest = build_timeline_manifest(result["segments"], video_duration=video_duration)

        variant_state.update({
            "segments": result["segments"],
            "tts_script": tts_script,
            "tts_audio_path": result["full_audio_path"],
            "timeline_manifest": timeline_manifest,
            "voice_id": voice.get("id"),
        })
        variants[variant] = variant_state
        task_state.set_preview_file(task_id, "tts_full_audio", result["full_audio_path"])
        _save_json(task_dir, "tts_script.normal.json", tts_script)
        _save_json(task_dir, "tts_result.normal.json", result["segments"])
        _save_json(task_dir, "timeline_manifest.normal.json", timeline_manifest)

        task_state.update(
            task_id,
            variants=variants,
            segments=result["segments"],
            tts_script=tts_script,
            tts_audio_path=result["full_audio_path"],
            voice_id=voice.get("id"),
            timeline_manifest=timeline_manifest,
        )

        task_state.set_artifact(task_id, "tts", build_tts_artifact(tts_script, result["segments"]))
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": tts_script})
        self._set_step(task_id, "tts", "done", "德语配音生成完成")
        from appcore.usage_log import record as _log_usage
        _tts_script_usage = tts_script.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                   model_name=get_model_display_name(provider, self.user_id),
                   success=True,
                   input_tokens=_tts_script_usage.get("input_tokens"),
                   output_tokens=_tts_script_usage.get("output_tokens"))
        _log_usage(self.user_id, task_id, "elevenlabs", success=True)

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据德语音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio
        from pipeline.localization_de import MAX_CHARS_PER_LINE, WEAK_STARTERS_DE
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        de_utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal", volc_api_key=volc_api_key
        )
        de_asr_result = {
            "full_text": " ".join(
                u.get("text", "").strip() for u in de_utterances if u.get("text")
            ).strip(),
            "utterances": de_utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        from pipeline.tts import _get_audio_duration
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            de_asr_result,
            total_duration=total_duration,
        )
        srt_content = build_srt_from_chunks(corrected_chunks, weak_boundary_words=WEAK_STARTERS_DE)
        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": de_asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants[variant] = variant_state

        task_state.update(
            task_id,
            variants=variants,
            english_asr_result=de_asr_result,
            corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(de_asr_result, corrected_chunks, srt_content))

        _save_json(task_dir, "de_asr_result.normal.json", de_asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": de_asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "德语字幕生成完成")
```

- [ ] **Step 2: 提交**

```bash
git add appcore/runtime_de.py
git commit -m "feat: 新增德语流水线编排 DeTranslateRunner"
```

---

## Task 6: 创建德语 SocketIO 适配器

**Files:**
- Create: `web/services/de_pipeline_runner.py`

- [ ] **Step 1: 创建 web/services/de_pipeline_runner.py**

```python
"""German pipeline SocketIO adapter — mirrors pipeline_runner.py for DeTranslateRunner."""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.runtime_de import DeTranslateRunner
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runner.start, args=(task_id,), daemon=True)
    thread.start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runner.resume, args=(task_id, start_step), daemon=True)
    thread.start()
```

- [ ] **Step 2: 提交**

```bash
git add web/services/de_pipeline_runner.py
git commit -m "feat: 新增德语流水线 SocketIO 适配器"
```

---

## Task 7: 参数化 workbench scripts 的 API base URL

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html:5-9`

- [ ] **Step 1: 在 TASK_WORKBENCH_CONFIG 中加入 apiBase**

修改 `_task_workbench_scripts.html` 第 5-9 行，在 `TASK_WORKBENCH_CONFIG` 对象中加入 `apiBase`：

```javascript
  const TASK_WORKBENCH_CONFIG = {
    taskId: {{ task_id|tojson }},
    initialTask: {{ initial_task|tojson }},
    allowUpload: {{ 'true' if allow_upload else 'false' }},
    detailUrlTemplate: {{ url_for_detail|default(url_for('projects.detail', task_id='__TASK_ID__'))|tojson }},
    apiBase: {{ api_base|default('/api/tasks')|tojson }},
  };
```

- [ ] **Step 2: 加入 _apiUrl 辅助函数并替换所有硬编码路径**

在 TASK_WORKBENCH_CONFIG 定义之后立即加入：

```javascript
  function _apiUrl(path) { return `${TASK_WORKBENCH_CONFIG.apiBase}/${taskId}${path || ''}`; }
```

然后对文件做全局替换（约 15 处）：

| 旧代码 | 新代码 |
|-------|--------|
| `` `/api/tasks/${taskId}/start` `` | `` _apiUrl('/start') `` |
| `` `/api/tasks/${taskId}` `` | `` _apiUrl() `` |
| `` `/api/tasks/${taskId}/alignment` `` | `` _apiUrl('/alignment') `` |
| `` `/api/tasks/${taskId}/segments` `` | `` _apiUrl('/segments') `` |
| `` `/api/tasks/${taskId}/resume` `` | `` _apiUrl('/resume') `` |
| `` `/api/tasks/${taskId}/download/hard` `` | `` _apiUrl('/download/hard') `` |
| `` `/api/tasks/${taskId}/download/soft` `` | `` _apiUrl('/download/soft') `` |
| `` `/api/tasks/${taskId}/download/srt` `` | `` _apiUrl('/download/srt') `` |
| `` `/api/tasks/${taskId}/download/capcut` `` | `` _apiUrl('/download/capcut') `` |
| `` `/api/tasks/${taskId}/artifact/` `` | `` _apiUrl('/artifact/') `` |
| `` `/api/tasks/${taskId}/start-translate` `` | `` _apiUrl('/start-translate') `` |
| `` `/api/tasks/${taskId}/retranslate` `` | `` _apiUrl('/retranslate') `` |
| `` `/api/tasks/${taskId}/select-translation` `` | `` _apiUrl('/select-translation') `` |

注意：`/api/voices` 和 `/api/tos-upload/*` 是共享路由，不需要替换。

- [ ] **Step 3: 验证现有英文流水线不受影响**

现有 `project_detail.html` 不传 `api_base`，默认值为 `'/api/tasks'`，行为不变。

- [ ] **Step 4: 提交**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "refactor: workbench scripts 支持可配置 apiBase"
```

---

## Task 8: 创建德语翻译路由蓝图

**Files:**
- Create: `web/routes/de_translate.py`

- [ ] **Step 1: 创建 web/routes/de_translate.py**

```python
"""德语视频翻译蓝图：页面路由 + API。"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, send_file, abort, redirect, Response, make_response
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import tos_clients
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from pipeline.alignment import build_script_segments
from web import store
from web.services import de_pipeline_runner
from web.preview_artifacts import build_alignment_artifact, build_translate_artifact

log = logging.getLogger(__name__)

bp = Blueprint("de_translate", __name__)

SOURCE_LANGUAGES = [("zh", "中文"), ("en", "英文")]

from pipeline.ffutil import extract_thumbnail as _extract_thumbnail


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str) -> str:
    base = desired_name
    candidate = base
    n = 2
    while True:
        row = db_query_one(
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
            (user_id, candidate),
        )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/de-translate")
@login_required
def index():
    rows = db_query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND type = 'de_translate' AND deleted_at IS NULL
           ORDER BY created_at DESC""",
        (current_user.id,),
    )
    return render_template("de_translate_list.html", projects=rows, now=datetime.now())


@bp.route("/de-translate/<task_id>")
@login_required
def detail(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    from appcore.api_keys import get_key
    translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    return render_template(
        "de_translate_detail.html",
        project=row,
        state=state,
        translate_pref=translate_pref,
    )


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/de-translate/start", methods=["POST"])
@login_required
def upload_and_start():
    """上传视频 + 选择源语言，创建德语翻译任务。"""
    if "video" not in request.files:
        return jsonify({"error": "No video file"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    from web.upload_util import validate_video_extension
    if not validate_video_extension(file.filename):
        return jsonify({"error": "不支持的视频格式"}), 400

    source_language = request.form.get("source_language", "zh")
    if source_language not in ("zh", "en"):
        return jsonify({"error": "source_language must be 'zh' or 'en'"}), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    ext = os.path.splitext(file.filename)[1].lower()
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    file.save(video_path)

    user_id = current_user.id
    store.create(task_id, video_path, task_dir,
                 original_filename=os.path.basename(file.filename),
                 user_id=user_id)

    # Set project type to de_translate
    db_execute("UPDATE projects SET type = 'de_translate' WHERE id = %s", (task_id,))

    display_name = _resolve_name_conflict(user_id, _default_display_name(os.path.basename(file.filename)))
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
    store.update(task_id, display_name=display_name, source_language=source_language)

    thumb = _extract_thumbnail(video_path, task_dir)
    if thumb:
        db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))

    return jsonify({"task_id": task_id}), 201


@bp.route("/api/de-translate/<task_id>/start", methods=["POST"])
@login_required
def start(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    store.update(
        task_id,
        voice_gender=body.get("voice_gender", "male"),
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        subtitle_position=body.get("subtitle_position", "bottom"),
        interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
    )

    de_pipeline_runner.start(task_id, user_id=current_user.id)
    updated_task = store.get(task_id) or task
    return jsonify({"status": "started", "task": updated_task})


@bp.route("/api/de-translate/<task_id>/confirm-alignment", methods=["POST"])
@login_required
def confirm_alignment(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    segments_data = body.get("segments")
    if segments_data:
        utterances = task.get("utterances", [])
        scene_cuts = task.get("scene_cuts", [])
        script_segments = build_script_segments(utterances, segments_data, scene_cuts=scene_cuts)
        store.update(task_id, script_segments=script_segments, segments=script_segments)

    store.confirm_alignment(task_id)
    de_pipeline_runner.resume(task_id, "translate", user_id=current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/api/de-translate/<task_id>/confirm-segments", methods=["POST"])
@login_required
def confirm_segments(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    store.confirm_segments(task_id)
    return jsonify({"status": "ok"})


@bp.route("/api/de-translate/<task_id>/confirm-translate", methods=["POST"])
@login_required
def confirm_translate(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    segments = body.get("segments")
    if segments:
        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        localized_translation = dict(variant_state.get("localized_translation", {}))
        localized_translation["sentences"] = [
            {"index": seg.get("index", i), "text": seg.get("translated", ""),
             "source_segment_indices": seg.get("source_segment_indices", [i])}
            for i, seg in enumerate(segments)
        ]
        localized_translation["full_text"] = " ".join(
            s["text"] for s in localized_translation["sentences"]
        )
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        store.update(task_id, variants=variants, localized_translation=localized_translation, _segments_confirmed=True)

    store.set_current_review_step(task_id, "")
    de_pipeline_runner.resume(task_id, "tts", user_id=current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/api/de-translate/<task_id>/export", methods=["POST"])
@login_required
def export(task_id):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    de_pipeline_runner.resume(task_id, "compose", user_id=current_user.id)
    return jsonify({"status": "started"})


@bp.route("/api/de-translate/<task_id>/resume/<step>", methods=["POST"])
@login_required
def resume(task_id, step):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404
    allowed = {"extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"}
    if step not in allowed:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    de_pipeline_runner.resume(task_id, step, user_id=current_user.id)
    return jsonify({"status": "resumed"})


@bp.route("/api/de-translate/<task_id>/download/<file_type>")
@login_required
def download(task_id, file_type):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    task_dir = task.get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    variant = request.args.get("variant", "normal")
    variant_state = (task.get("variants") or {}).get(variant, {})

    path_map = {
        "soft": variant_state.get("result", {}).get("soft_video"),
        "hard": variant_state.get("result", {}).get("hard_video"),
        "srt": variant_state.get("srt_path"),
        "capcut": variant_state.get("exports", {}).get("capcut_archive"),
    }
    path = path_map.get(file_type)
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(os.path.abspath(path), as_attachment=True)


@bp.route("/api/de-translate/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id, name):
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    task_dir = task.get("task_dir") or os.path.join(OUTPUT_DIR, task_id)
    variant = request.args.get("variant")

    preview_files = (
        ((task.get("variants") or {}).get(variant, {}).get("preview_files", {}))
        if variant
        else (task.get("preview_files") or {})
    )
    path = preview_files.get(name)
    if path and os.path.exists(path):
        return send_file(os.path.abspath(path))
    return jsonify({"error": "Artifact not found"}), 404
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/de_translate.py
git commit -m "feat: 新增德语翻译路由蓝图 de_translate"
```

---

## Task 9: 创建德语翻译列表页

**Files:**
- Create: `web/templates/de_translate_list.html`

- [ ] **Step 1: 创建 web/templates/de_translate_list.html**

基于 `projects.html` 结构，添加源语言选择器和上传表单：

```html
{% extends "layout.html" %}
{% block title %}视频翻译（德语） - AutoVideoSrt{% endblock %}
{% block page_title %}视频翻译（德语）{% endblock %}
{% block extra_style %}
<style>
  .upload-form { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 24px; }
  .upload-form label { font-size: 13px; font-weight: 600; margin-bottom: 4px; display: block; }
  .upload-form select, .upload-form input[type="file"] {
    padding: 8px 12px; border: 1px solid var(--border-main); border-radius: 8px;
    background: var(--bg-card); color: var(--text-main); font-size: 14px;
  }
  .upload-form button {
    padding: 8px 20px; border: none; border-radius: 8px;
    background: var(--primary-gradient); color: #fff; font-weight: 600;
    cursor: pointer; font-size: 14px;
  }
  .upload-form button:disabled { opacity: 0.5; cursor: not-allowed; }
  .project-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
  .project-card {
    background: var(--bg-card); border-radius: 12px; overflow: hidden;
    border: 1px solid var(--border-main); transition: box-shadow 0.15s;
    text-decoration: none; color: inherit; display: block;
  }
  .project-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
  .project-thumb { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #e5e7eb; display: block; }
  .project-info { padding: 12px 14px; }
  .project-name { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
  .project-meta { font-size: 12px; color: #9ca3af; }
  .project-status { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
  .project-status.done { background: #dcfce7; color: #16a34a; }
  .project-status.error { background: #fee2e2; color: #dc2626; }
  .project-status.running { background: #dbeafe; color: #2563eb; }
  .empty-state { text-align: center; padding: 60px 20px; color: #9ca3af; }
</style>
{% endblock %}
{% block content %}
<p class="page-subtitle">上传中文或英文视频，一键翻译为德语本地化视频。</p>

<form class="upload-form card" id="uploadForm" enctype="multipart/form-data">
  <div>
    <label for="sourceLanguage">源语言</label>
    <select id="sourceLanguage" name="source_language">
      <option value="zh" selected>中文</option>
      <option value="en">英文</option>
    </select>
  </div>
  <div>
    <label for="videoFile">选择视频</label>
    <input type="file" id="videoFile" name="video" accept="video/*" required />
  </div>
  <button type="submit" id="uploadBtn">上传并创建任务</button>
</form>

{% if projects %}
<div class="project-grid">
  {% for p in projects %}
  <a class="project-card" href="/de-translate/{{ p.id }}">
    {% if p.thumbnail_path %}
    <img class="project-thumb" src="/api/tasks/{{ p.id }}/thumbnail" alt="" />
    {% else %}
    <div class="project-thumb" style="display:flex;align-items:center;justify-content:center;font-size:32px;">🇩🇪</div>
    {% endif %}
    <div class="project-info">
      <div class="project-name">{{ p.display_name or p.original_filename or p.id }}</div>
      <div class="project-meta">
        <span class="project-status {{ p.status or '' }}">{{ p.status or 'uploaded' }}</span>
        · {{ p.created_at.strftime('%m/%d %H:%M') if p.created_at else '' }}
      </div>
    </div>
  </a>
  {% endfor %}
</div>
{% else %}
<div class="empty-state">
  <p style="font-size:32px;margin-bottom:12px;">🇩🇪</p>
  <p>还没有德语翻译项目，上传视频开始吧。</p>
</div>
{% endif %}
{% endblock %}
{% block scripts %}
<script>
document.getElementById('uploadForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const btn = document.getElementById('uploadBtn');
  const fileInput = document.getElementById('videoFile');
  const langSelect = document.getElementById('sourceLanguage');
  if (!fileInput.files[0]) return;

  btn.disabled = true;
  btn.textContent = '上传中...';

  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const formData = new FormData();
  formData.append('video', fileInput.files[0]);
  formData.append('source_language', langSelect.value);

  try {
    const res = await fetch('/api/de-translate/start', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken },
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '上传失败');
    window.location.href = '/de-translate/' + data.task_id;
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.textContent = '上传并创建任务';
  }
});
</script>
{% endblock %}
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/de_translate_list.html
git commit -m "feat: 新增德语翻译列表页模板"
```

---

## Task 10: 创建德语翻译工作台页

**Files:**
- Create: `web/templates/de_translate_detail.html`

- [ ] **Step 1: 创建 web/templates/de_translate_detail.html**

复用 `_task_workbench.html` 和 `_task_workbench_scripts.html`，配置德语 apiBase 和返回链接。

```html
{% extends "layout.html" %}
{% if not project.deleted_at %}
{% set allow_upload = false %}
{% set show_back_link = true %}
{% set task_id = project.id %}
{% set initial_task = state %}
{% set api_base = '/api/de-translate' %}
{% set url_for_detail = '/de-translate/__TASK_ID__' %}
{% endif %}
{% block title %}{{ project.display_name or project.original_filename or project.id }} - 德语翻译{% endblock %}
{% block page_title %}{{ project.display_name or project.original_filename or project.id }} (德语){% endblock %}
{% block extra_style %}
{% include "_task_workbench_styles.html" %}
{% endblock %}
{% block content %}
{% if project.deleted_at %}
<a class="back-link" href="/de-translate">← 返回德语翻译列表</a>
<div class="expired-notice">
  <p style="font-size: 32px; margin-bottom: 12px;">任务已过期</p>
  <p>该项目对应的文件已经被清理，无法继续查看或处理。</p>
</div>
{% else %}
<!-- Override back link for German module -->
<a class="back-link" href="/de-translate">← 返回德语翻译列表</a>
<p class="page-subtitle">中文/英文 → 德语本土化翻译。每一步都会把关键中间产物留在页面里。</p>
{% include "_task_workbench.html" %}
{% endif %}
{% endblock %}
{% block scripts %}
{% if not project.deleted_at %}
{% include "_task_workbench_scripts.html" %}
{% endif %}
{% endblock %}
```

注意：`_task_workbench.html` 包含 `show_back_link` 条件渲染返回链接，但指向 `projects.index`。由于我们在 block content 中手动放了德语返回链接，需要在此模板中设 `show_back_link = false` 或者让第一个链接覆盖。实际上 `_task_workbench.html` 的返回链接在最前面，我们这里先手动放了一个，所以设 `show_back_link = false`。

修正：

```html
{% set show_back_link = false %}
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/de_translate_detail.html
git commit -m "feat: 新增德语翻译工作台页模板"
```

---

## Task 11: 注册蓝图 + 侧边栏导航

**Files:**
- Modify: `web/app.py:36-75`
- Modify: `web/templates/layout.html:292-310`

- [ ] **Step 1: 在 web/app.py 中注册德语翻译蓝图**

在 imports 区域加入：

```python
from web.routes.de_translate import bp as de_translate_bp
```

在 `create_app()` 的蓝图注册区域加入：

```python
    app.register_blueprint(de_translate_bp)
```

同时在 socketio 事件区域加入 `join_de_translate_task`：

```python
    @socketio.on("join_de_translate_task")
    def on_join_de_translate(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            from web import store
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)
```

- [ ] **Step 2: 在 layout.html 侧边栏添加德语翻译导航**

在"视频翻译"链接之后添加：

```html
      <a href="/de-translate" {% if request.path.startswith('/de-translate') %}class="active"{% endif %}>
        <span class="nav-icon">🇩🇪</span> 视频翻译（德语）
      </a>
```

- [ ] **Step 3: 提交**

```bash
git add web/app.py web/templates/layout.html
git commit -m "feat: 注册德语翻译蓝图，侧边栏添加导航入口"
```

---

## Task 12: 数据库 type 枚举扩展

**Files:**
- Modify: `db/schema.sql` (更新 enum 定义)
- Run: migration SQL

- [ ] **Step 1: 执行 ALTER TABLE 添加 de_translate 到 type 枚举**

需要执行的 SQL：

```sql
ALTER TABLE projects MODIFY COLUMN type
  ENUM('translation','copywriting','video_creation','video_review','text_translate','de_translate')
  NOT NULL DEFAULT 'translation';
```

- [ ] **Step 2: 更新 db/schema.sql 中的 type 定义**

将 schema.sql 中的 type 枚举更新为包含 `de_translate`。

- [ ] **Step 3: 提交**

```bash
git add db/schema.sql
git commit -m "feat: projects.type 枚举新增 de_translate"
```

---

## Task 13: 现有英文流水线项目列表过滤

**Files:**
- Modify: `web/routes/projects.py:13-18`

- [ ] **Step 1: 在 projects 列表查询中过滤 type**

在 `index()` 路由中，将查询改为只显示 `translation` 类型的项目（排除其他模块的项目）：

```python
    rows = query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND type = 'translation' AND deleted_at IS NULL ORDER BY created_at DESC""",
        (current_user.id,),
    )
```

注意：如果之前的项目没有设置 type（默认为 'translation'），这个过滤不会丢失数据。

- [ ] **Step 2: 提交**

```bash
git add web/routes/projects.py
git commit -m "fix: 英文翻译列表仅显示 translation 类型项目"
```
