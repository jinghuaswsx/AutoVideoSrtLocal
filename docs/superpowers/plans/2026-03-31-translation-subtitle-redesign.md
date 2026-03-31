# Translation Subtitle Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `translated` text path with a staged Chinese source, localized English, ElevenLabs script, English ASR, and corrected subtitle pipeline that keeps copy, voice, and subtitles consistent while exposing each intermediate artifact in the web UI.

**Architecture:** Add a focused localization module that validates structured OpenRouter outputs for `localized_translation` and `tts_script`, then feed ElevenLabs from `tts_script.blocks` instead of the old translated segments. After TTS finishes, run English ASR on the generated audio and deterministically align the ASR timing to `tts_script.subtitle_chunks`, producing the final SRT without letting ASR rewrite the text. Integrate the new artifacts into task state, pipeline orchestration, manifest generation, and inline previews.

**Tech Stack:** Flask, Flask-SocketIO, OpenAI SDK against OpenRouter, ElevenLabs SDK, Volcengine ASR HTTP API, pytest, vanilla JavaScript

---

## File Structure

- Create: `pipeline/localization.py`
  Responsibility: assemble source Chinese text, define JSON schemas, validate structured model outputs, and expose helpers for downstream pipeline code.
- Create: `pipeline/subtitle_alignment.py`
  Responsibility: normalize English text, align `subtitle_chunks` to English ASR words, and return corrected subtitle chunks with timing.
- Modify: `pipeline/translate.py`
  Responsibility: call OpenRouter Claude Sonnet 4.5 for localized translation and ElevenLabs-specific script generation using structured outputs.
- Modify: `pipeline/tts.py`
  Responsibility: generate audio from `tts_script.blocks` and preserve block metadata needed for timeline compilation.
- Modify: `pipeline/asr.py`
  Responsibility: add a local-audio ASR helper that uploads generated audio, calls Volcengine ASR, and returns timestamped English utterances.
- Modify: `pipeline/timeline.py`
  Responsibility: build the manifest from TTS blocks plus source Chinese segment ranges instead of the old `translated` field.
- Modify: `pipeline/subtitle.py`
  Responsibility: keep timestamp formatting helpers and add SRT builders for corrected subtitle chunks.
- Modify: `web/store.py`
  Responsibility: initialize and persist the new text-artifact fields in task state.
- Modify: `web/services/pipeline_runner.py`
  Responsibility: orchestrate the new stages, save new JSON artifacts, and publish new preview payloads.
- Modify: `web/preview_artifacts.py`
  Responsibility: describe preview payloads for Chinese source text, localized translation, TTS script, English ASR, and corrected subtitles.
- Modify: `web/templates/index.html`
  Responsibility: render the new preview item types inside the existing step cards.
- Modify: `tests/test_pipeline_runner.py`
  Responsibility: verify the new task-state transitions and auto-run flow.
- Modify: `tests/test_subtitle.py`
  Responsibility: verify final SRT generation from corrected subtitle chunks.
- Modify: `tests/test_timeline.py`
  Responsibility: verify the manifest uses TTS block metadata and source segment windows.
- Modify: `tests/test_web_routes.py`
  Responsibility: verify task payloads expose the new artifacts and index page supports the new preview renderers.
- Create: `tests/test_localization.py`
  Responsibility: verify source-text assembly, structured-output validation, and OpenRouter wrapper behavior.
- Create: `tests/test_subtitle_alignment.py`
  Responsibility: verify deterministic ASR-to-subtitle alignment, normalization, and fallback timing behavior.

## Task 1: Add localization data model and validators

**Files:**
- Create: `pipeline/localization.py`
- Create: `tests/test_localization.py`

- [ ] **Step 1: Write the failing tests**

```python
from pipeline.localization import (
    build_source_full_text_zh,
    validate_localized_translation,
    validate_tts_script,
)


def test_build_source_full_text_zh_joins_confirmed_segments_with_newlines():
    segments = [
        {"index": 0, "text": "第一句中文"},
        {"index": 1, "text": "第二句中文"},
    ]

    assert build_source_full_text_zh(segments) == "第一句中文\n第二句中文"


def test_validate_localized_translation_requires_full_text_and_source_segment_indices():
    payload = {
        "full_text": "Hook line. Closing line.",
        "sentences": [
            {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
        ],
    }

    validated = validate_localized_translation(payload)

    assert validated["full_text"] == "Hook line. Closing line."
    assert validated["sentences"][1]["source_segment_indices"] == [1]


def test_validate_tts_script_rejects_chunk_text_that_does_not_match_full_text():
    payload = {
        "full_text": "Say it smooth. Keep it fun.",
        "blocks": [
            {"index": 0, "text": "Say it smooth.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Keep it fun.", "sentence_indices": [1], "source_segment_indices": [1]},
        ],
        "subtitle_chunks": [
            {"index": 0, "text": "Say it smooth.", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Keep this different.", "block_indices": [1], "sentence_indices": [1], "source_segment_indices": [1]},
        ],
    }

    try:
        validate_tts_script(payload)
    except ValueError as exc:
        assert "subtitle_chunks" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_localization.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing symbol errors because `pipeline/localization.py` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# pipeline/localization.py
from __future__ import annotations


def build_source_full_text_zh(script_segments: list[dict]) -> str:
    return "\n".join((segment.get("text") or "").strip() for segment in script_segments if (segment.get("text") or "").strip())


def _concat_items(items: list[dict], key: str) -> str:
    return " ".join((item.get(key) or "").strip() for item in items if (item.get(key) or "").strip()).strip()


def validate_localized_translation(payload: dict) -> dict:
    sentences = payload.get("sentences") or []
    if not payload.get("full_text") or not sentences:
        raise ValueError("localized_translation requires full_text and sentences")
    for sentence in sentences:
        if not isinstance(sentence.get("source_segment_indices"), list) or not sentence["source_segment_indices"]:
            raise ValueError("localized_translation sentence missing source_segment_indices")
    rebuilt = _concat_items(sentences, "text")
    if rebuilt != (payload.get("full_text") or "").strip():
        raise ValueError("localized_translation full_text does not match sentences")
    return {"full_text": payload["full_text"].strip(), "sentences": sentences}


def validate_tts_script(payload: dict) -> dict:
    blocks = payload.get("blocks") or []
    subtitle_chunks = payload.get("subtitle_chunks") or []
    full_text = (payload.get("full_text") or "").strip()
    if not full_text or not blocks or not subtitle_chunks:
        raise ValueError("tts_script requires full_text, blocks, and subtitle_chunks")
    if _concat_items(blocks, "text") != full_text:
        raise ValueError("tts_script blocks do not match full_text")
    if _concat_items(subtitle_chunks, "text") != full_text:
        raise ValueError("tts_script subtitle_chunks do not match full_text")
    return {"full_text": full_text, "blocks": blocks, "subtitle_chunks": subtitle_chunks}
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_localization.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization.py tests/test_localization.py
git commit -m "feat: add localization data validators"
```

## Task 2: Implement structured OpenRouter translation and TTS script generation

**Files:**
- Modify: `pipeline/translate.py`
- Modify: `pipeline/localization.py`
- Modify: `tests/test_localization.py`

- [ ] **Step 1: Write the failing tests**

```python
import types

from pipeline.translate import generate_localized_translation, generate_tts_script


def test_generate_localized_translation_parses_structured_output(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content='{"full_text":"Hook line. Closing line.","sentences":[{"index":0,"text":"Hook line.","source_segment_indices":[0]},{"index":1,"text":"Closing line.","source_segment_indices":[1]}]}'
                        )
                    )
                ]
            )

    monkeypatch.setattr("pipeline.translate.client.chat.completions", FakeCompletions())

    payload = generate_localized_translation(
        source_full_text_zh="第一句中文\n第二句中文",
        script_segments=[
            {"index": 0, "text": "第一句中文"},
            {"index": 1, "text": "第二句中文"},
        ],
    )

    assert payload["sentences"][0]["text"] == "Hook line."
    assert captured["model"] == "anthropic/claude-sonnet-4.5"


def test_generate_tts_script_returns_validated_blocks(monkeypatch):
    class FakeCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content='{"full_text":"Say it smooth. Keep it fun.","blocks":[{"index":0,"text":"Say it smooth.","sentence_indices":[0],"source_segment_indices":[0]},{"index":1,"text":"Keep it fun.","sentence_indices":[1],"source_segment_indices":[1]}],"subtitle_chunks":[{"index":0,"text":"Say it smooth.","block_indices":[0],"sentence_indices":[0],"source_segment_indices":[0]},{"index":1,"text":"Keep it fun.","block_indices":[1],"sentence_indices":[1],"source_segment_indices":[1]}]}'
                        )
                    )
                ]
            )

    monkeypatch.setattr("pipeline.translate.client.chat.completions", FakeCompletions())

    payload = generate_tts_script(
        localized_translation={
            "full_text": "Say it smooth. Keep it fun.",
            "sentences": [
                {"index": 0, "text": "Say it smooth.", "source_segment_indices": [0]},
                {"index": 1, "text": "Keep it fun.", "source_segment_indices": [1]},
            ],
        }
    )

    assert payload["subtitle_chunks"][1]["text"] == "Keep it fun."
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_localization.py -q`
Expected: FAIL because `generate_localized_translation` and `generate_tts_script` do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# pipeline/localization.py
import json

LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a US TikTok commerce copywriter.
Return valid JSON only.
Translate the Chinese source into natural, native, sales-capable American English.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing text for ElevenLabs narration and subtitle display.
Return valid JSON only.
Use the localized English as the only wording source.
blocks optimize speaking rhythm.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text."""


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
                            "source_segment_indices": {"type": "array", "items": {"type": "integer"}},
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
                            "sentence_indices": {"type": "array", "items": {"type": "integer"}},
                            "source_segment_indices": {"type": "array", "items": {"type": "integer"}},
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
                            "block_indices": {"type": "array", "items": {"type": "integer"}},
                            "sentence_indices": {"type": "array", "items": {"type": "integer"}},
                            "source_segment_indices": {"type": "array", "items": {"type": "integer"}},
                        },
                        "required": ["index", "text", "block_indices", "sentence_indices", "source_segment_indices"],
                    },
                },
            },
            "required": ["full_text", "blocks", "subtitle_chunks"],
        },
    },
}


def build_localized_translation_messages(source_full_text_zh: str, script_segments: list[dict]) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    return [
        {"role": "system", "content": LOCALIZED_TRANSLATION_SYSTEM_PROMPT},
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
```

```python
# pipeline/translate.py
import json

from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)


def generate_localized_translation(source_full_text_zh: str, script_segments: list[dict]) -> dict:
    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=build_localized_translation_messages(source_full_text_zh, script_segments),
        temperature=0.2,
        max_tokens=4096,
        extra_body={
            "response_format": LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
            "plugins": [{"id": "response-healing"}],
        },
    )
    payload = json.loads(response.choices[0].message.content)
    return validate_localized_translation(payload)


def generate_tts_script(localized_translation: dict) -> dict:
    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=build_tts_script_messages(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        extra_body={
            "response_format": TTS_SCRIPT_RESPONSE_FORMAT,
            "plugins": [{"id": "response-healing"}],
        },
    )
    payload = json.loads(response.choices[0].message.content)
    return validate_tts_script(payload)
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_localization.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/translate.py pipeline/localization.py tests/test_localization.py
git commit -m "feat: add structured translation and tts script generation"
```

## Task 3: Drive TTS and timeline generation from ElevenLabs script blocks

**Files:**
- Modify: `pipeline/localization.py`
- Modify: `pipeline/tts.py`
- Modify: `pipeline/timeline.py`
- Modify: `tests/test_timeline.py`
- Create: `tests/test_tts_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
from pipeline.localization import build_tts_segments
from pipeline.timeline import build_timeline_manifest


def test_build_tts_segments_projects_block_ranges_back_to_source_segments():
    script_segments = [
        {"index": 0, "text": "第一句中文", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "第二句中文", "start_time": 1.0, "end_time": 2.5},
    ]
    tts_script = {
        "full_text": "Hook line. Closing line.",
        "blocks": [
            {"index": 0, "text": "Hook line.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "sentence_indices": [1], "source_segment_indices": [0, 1]},
        ],
        "subtitle_chunks": [],
    }

    segments = build_tts_segments(tts_script, script_segments)

    assert segments[1]["start_time"] == 0.0
    assert segments[1]["end_time"] == 2.5
    assert segments[1]["translated"] == "Closing line."


def test_build_timeline_manifest_uses_tts_block_text_and_source_window():
    manifest = build_timeline_manifest(
        [
            {
                "index": 0,
                "text": "第一句中文",
                "translated": "Hook line.",
                "source_segment_indices": [0],
                "start_time": 0.0,
                "end_time": 1.0,
                "tts_duration": 1.5,
            }
        ],
        video_duration=10.0,
    )

    assert manifest["segments"][0]["translated"] == "Hook line."
    assert manifest["segments"][0]["source_window"]["end"] == 1.0
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_timeline.py tests/test_tts_pipeline.py -q`
Expected: FAIL because `build_tts_segments` does not exist and the timeline manifest does not yet preserve `source_segment_indices`.

- [ ] **Step 3: Write the minimal implementation**

```python
# pipeline/localization.py
def build_tts_segments(tts_script: dict, script_segments: list[dict]) -> list[dict]:
    result = []
    for block in tts_script.get("blocks", []):
        indices = block["source_segment_indices"]
        covered = [segment for segment in script_segments if segment["index"] in indices]
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
```

```python
# pipeline/tts.py
def generate_full_audio(segments: list[dict], voice_id: str, output_dir: str) -> dict:
    seg_dir = os.path.join(output_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)
    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
            generate_segment_audio(text, voice_id, seg_path)
            duration = _get_audio_duration(seg_path)

            seg_copy = dict(seg)
            seg_copy["tts_path"] = seg_path
            seg_copy["tts_duration"] = duration
            updated_segments.append(seg_copy)
            concat_f.write(f"file '{os.path.abspath(seg_path)}'\n")

    full_audio_path = os.path.join(output_dir, "tts_full.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        check=True,
        capture_output=True,
        text=True,
    )
    return {"full_audio_path": full_audio_path, "segments": updated_segments}
```

```python
# pipeline/timeline.py
timeline_segments.append(
    {
        "index": index,
        "text": segment.get("text", ""),
        "translated": segment.get("translated", ""),
        "source_segment_indices": segment.get("source_segment_indices", []),
        "source_window": {
            "start": round(float(segment.get("start_time", 0.0)), 3),
            "end": round(float(segment.get("end_time", 0.0)), 3),
        },
        "tts_path": segment.get("tts_path"),
        "tts_duration": duration,
        "timeline_start": round(timeline_start, 3),
        "timeline_end": round(timeline_end, 3),
        "video_ranges": video_ranges,
        "video_truncated": actual_video_duration + 1e-6 < duration,
    }
)
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_timeline.py tests/test_tts_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization.py pipeline/tts.py pipeline/timeline.py tests/test_timeline.py tests/test_tts_pipeline.py
git commit -m "feat: drive tts and timeline from script blocks"
```

## Task 4: Add English audio ASR and deterministic subtitle correction

**Files:**
- Modify: `pipeline/asr.py`
- Modify: `pipeline/subtitle.py`
- Create: `pipeline/subtitle_alignment.py`
- Modify: `tests/test_subtitle.py`
- Create: `tests/test_subtitle_alignment.py`

- [ ] **Step 1: Write the failing tests**

```python
from pipeline.subtitle import build_srt_from_chunks
from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr


def test_align_subtitle_chunks_to_asr_preserves_target_text_and_uses_word_timing():
    subtitle_chunks = [
        {"index": 0, "text": "Say it smooth."},
        {"index": 1, "text": "Keep it fun."},
    ]
    asr_result = {
        "utterances": [
            {
                "text": "say it smooth keep it fun",
                "start_time": 0.0,
                "end_time": 2.0,
                "words": [
                    {"text": "say", "start_time": 0.0, "end_time": 0.2},
                    {"text": "it", "start_time": 0.2, "end_time": 0.35},
                    {"text": "smooth", "start_time": 0.35, "end_time": 0.8},
                    {"text": "keep", "start_time": 1.0, "end_time": 1.2},
                    {"text": "it", "start_time": 1.2, "end_time": 1.35},
                    {"text": "fun", "start_time": 1.35, "end_time": 1.8},
                ],
            }
        ]
    }

    aligned = align_subtitle_chunks_to_asr(subtitle_chunks, asr_result, total_duration=2.0)

    assert aligned[0]["text"] == "Say it smooth."
    assert aligned[0]["start_time"] == 0.0
    assert aligned[1]["end_time"] == 1.8


def test_build_srt_from_chunks_uses_corrected_text_and_timing():
    srt = build_srt_from_chunks(
        [
            {"index": 0, "text": "Say it smooth.", "start_time": 0.0, "end_time": 0.8},
            {"index": 1, "text": "Keep it fun.", "start_time": 0.8, "end_time": 1.8},
        ]
    )

    assert "00:00:00,000 --> 00:00:00,800" in srt
    assert "Say it smooth." in srt
    assert "Keep it fun." in srt
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_subtitle.py tests/test_subtitle_alignment.py -q`
Expected: FAIL because the aligner and `build_srt_from_chunks` do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# pipeline/asr.py
def transcribe_local_audio(local_audio_path: str, prefix: str) -> list[dict]:
    from pipeline.storage import delete_file, upload_file

    object_key = f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
    audio_url = upload_file(local_audio_path, object_key)
    try:
        return transcribe(audio_url)
    finally:
        try:
            delete_file(object_key)
        except Exception:
            pass
```

```python
# pipeline/subtitle_alignment.py
import re


def _normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def align_subtitle_chunks_to_asr(subtitle_chunks: list[dict], asr_result: dict, total_duration: float) -> list[dict]:
    words = []
    for utterance in asr_result.get("utterances", []):
        for word in utterance.get("words", []):
            normalized = _normalize_token(word.get("text", ""))
            if normalized:
                words.append({**word, "normalized": normalized})

    aligned = []
    cursor = 0
    for index, chunk in enumerate(subtitle_chunks):
        target_tokens = [_normalize_token(token) for token in chunk["text"].split()]
        target_tokens = [token for token in target_tokens if token]
        matched = []
        for token in target_tokens:
            while cursor < len(words) and words[cursor]["normalized"] != token:
                cursor += 1
            if cursor < len(words):
                matched.append(words[cursor])
                cursor += 1
        if matched:
            start_time = matched[0]["start_time"]
            end_time = matched[-1]["end_time"]
        else:
            start_time = round((index / max(len(subtitle_chunks), 1)) * total_duration, 3)
            end_time = round(((index + 1) / max(len(subtitle_chunks), 1)) * total_duration, 3)
        aligned.append({**chunk, "start_time": start_time, "end_time": end_time, "source_asr_text": " ".join(word["text"] for word in matched)})
    return aligned
```

```python
# pipeline/subtitle.py
def build_srt_from_chunks(chunks: list[dict]) -> str:
    srt_lines = []
    for i, chunk in enumerate(chunks, 1):
        srt_lines.append(str(i))
        srt_lines.append(f"{format_timestamp(chunk['start_time'])} --> {format_timestamp(chunk['end_time'])}")
        srt_lines.append(chunk["text"])
        srt_lines.append("")
    return "\n".join(srt_lines)
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_subtitle.py tests/test_subtitle_alignment.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/asr.py pipeline/subtitle.py pipeline/subtitle_alignment.py tests/test_subtitle.py tests/test_subtitle_alignment.py
git commit -m "feat: align english asr timing to subtitle chunks"
```

## Task 5: Integrate the new text pipeline into task state, previews, and web flow

**Files:**
- Modify: `web/store.py`
- Modify: `web/services/pipeline_runner.py`
- Modify: `web/preview_artifacts.py`
- Modify: `web/templates/index.html`
- Modify: `tests/test_pipeline_runner.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
from web.app import create_app
from web import store
from web.services import pipeline_runner


def test_step_translate_persists_source_text_and_localized_translation(tmp_path, monkeypatch):
    task = store.create("task-localized", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"index": 0, "text": "第一句中文", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "第二句中文", "start_time": 1.0, "end_time": 2.0},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "第一句中文\n第二句中文")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments: {
            "full_text": "Hook line. Closing line.",
            "sentences": [
                {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
                {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
            ],
        },
    )

    pipeline_runner._step_translate("task-localized")

    saved = store.get("task-localized")
    assert saved["source_full_text_zh"] == "第一句中文\n第二句中文"
    assert saved["localized_translation"]["full_text"] == "Hook line. Closing line."


def test_task_payload_exposes_tts_script_and_corrected_subtitle():
    app = create_app()
    client = app.test_client()
    store.create("task-payload", "video.mp4", "output/task-payload")
    store.update(
        "task-payload",
        tts_script={"full_text": "Say it smooth.", "blocks": [], "subtitle_chunks": []},
        corrected_subtitle={"chunks": [], "srt_content": "1\n00:00:00,000 --> 00:00:01,000\nSay it smooth.\n"},
    )

    response = client.get("/api/tasks/task-payload")

    payload = response.get_json()
    assert payload["tts_script"]["full_text"] == "Say it smooth."
    assert "Say it smooth." in payload["corrected_subtitle"]["srt_content"]
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_pipeline_runner.py tests/test_web_routes.py -q`
Expected: FAIL because the new task-state fields and preview builders are not implemented yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# web/store.py
task = {
    "id": task_id,
    "status": "uploaded",
    "video_path": video_path,
    "task_dir": task_dir,
    "steps": {
        "extract": "pending",
        "asr": "pending",
        "alignment": "pending",
        "translate": "pending",
        "tts": "pending",
        "subtitle": "pending",
        "compose": "pending",
        "export": "pending",
    },
    "utterances": [],
    "scene_cuts": [],
    "alignment": {},
    "script_segments": [],
    "segments": [],
    "source_full_text_zh": "",
    "localized_translation": {},
    "tts_script": {},
    "english_asr_result": {},
    "corrected_subtitle": {},
    "voice_gender": "male",
    "voice_id": None,
    "recommended_voice_id": None,
    "subtitle_position": "bottom",
    "interactive_review": False,
    "result": {},
    "exports": {},
    "artifacts": {},
    "preview_files": {},
}
```

```python
# web/services/pipeline_runner.py
def _step_translate(task_id: str):
    task = store.get(task_id)
    task_dir = task["task_dir"]
    source_full_text_zh = build_source_full_text_zh(task["script_segments"])
    localized_translation = generate_localized_translation(source_full_text_zh, task["script_segments"])
    store.update(task_id, source_full_text_zh=source_full_text_zh, localized_translation=localized_translation)
    _save_json(task_dir, "localized_translation.json", localized_translation)
    store.set_artifact(task_id, "translate", build_translate_artifact(source_full_text_zh, localized_translation))
    set_step(task_id, "translate", "done", "本土化翻译完成")


def _step_tts(task_id: str, task_dir: str):
    task = store.get(task_id)
    voice = get_voice_by_id(task["voice_id"]) or get_voice_by_id(task.get("recommended_voice_id")) or get_default_voice(task.get("voice_gender", "male"))
    tts_script = generate_tts_script(task["localized_translation"])
    tts_segments = build_tts_segments(tts_script, task["script_segments"])
    result = generate_full_audio(tts_segments, voice["elevenlabs_voice_id"], task_dir)
    timeline_manifest = build_timeline_manifest(result["segments"], video_duration=get_video_duration(task["video_path"]))
    store.update(task_id, tts_script=tts_script, segments=result["segments"], timeline_manifest=timeline_manifest)
    _save_json(task_dir, "tts_script.json", tts_script)
    store.set_artifact(task_id, "tts", build_tts_artifact(tts_script, result["segments"]))


def _step_subtitle(task_id: str, task_dir: str):
    task = store.get(task_id)
    english_asr_result = transcribe_local_audio(task["tts_audio_path"], prefix=f"tts-asr/{task_id}")
    corrected_chunks = align_subtitle_chunks_to_asr(
        task["tts_script"]["subtitle_chunks"],
        english_asr_result,
        total_duration=_get_audio_duration(task["tts_audio_path"]),
    )
    srt_content = build_srt_from_chunks(corrected_chunks)
    store.update(task_id, english_asr_result=english_asr_result, corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content})
    store.set_artifact(task_id, "subtitle", build_subtitle_artifact(english_asr_result, corrected_chunks, srt_content))
    save_srt(srt_content, os.path.join(task_dir, "subtitle.srt"))
```

```python
# web/preview_artifacts.py
def build_translate_artifact(source_full_text_zh: str, localized_translation: dict) -> dict:
    return {
        "title": "翻译本土化",
        "items": [
            text_item("整段中文", source_full_text_zh),
            text_item("整段本土化英文", localized_translation.get("full_text", "")),
            {"type": "sentences", "label": "英文句子映射", "sentences": localized_translation.get("sentences", [])},
        ],
    }


def build_tts_artifact(tts_script: dict, segments: list[dict]) -> dict:
    return {
        "title": "语音生成",
        "items": [
            text_item("ElevenLabs 文案", tts_script.get("full_text", "")),
            {"type": "tts_blocks", "label": "朗读块", "blocks": tts_script.get("blocks", [])},
            {"type": "subtitle_chunks", "label": "字幕块", "chunks": tts_script.get("subtitle_chunks", [])},
            media_item("audio", "整段配音", "tts_full_audio"),
        ],
    }
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_pipeline_runner.py tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/store.py web/services/pipeline_runner.py web/preview_artifacts.py web/templates/index.html tests/test_pipeline_runner.py tests/test_web_routes.py
git commit -m "feat: expose localization and subtitle artifacts in pipeline ui"
```

## Task 6: Run full regression verification

**Files:**
- Modify: `docs/superpowers/plans/2026-03-31-translation-subtitle-redesign.md`
- Modify: whichever production/test files changed during Tasks 1-5

- [ ] **Step 1: Run the new focused suites**

Run: `pytest tests/test_localization.py tests/test_tts_pipeline.py tests/test_subtitle_alignment.py -q`
Expected: PASS

- [ ] **Step 2: Run the pipeline/web regression suites**

Run: `pytest tests/test_pipeline_runner.py tests/test_web_routes.py tests/test_subtitle.py tests/test_timeline.py -q`
Expected: PASS

- [ ] **Step 3: Run the full project suite**

Run: `pytest tests -q`
Expected: PASS

- [ ] **Step 4: Run compile verification**

Run: `python -m compileall -q pipeline web main.py config.py`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add pipeline web tests docs/superpowers/plans/2026-03-31-translation-subtitle-redesign.md
git commit -m "feat: redesign translation subtitle and tts pipeline"
```
