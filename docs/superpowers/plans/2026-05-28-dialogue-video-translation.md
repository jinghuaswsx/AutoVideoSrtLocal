# Dialogue Video Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent dialogue video translation module that automatically detects Speaker A/B, matches and confirms two voices, then reuses the Omni V2 audio generation and ASR-window alignment flow with per-segment voice IDs.

**Architecture:** Add a new `dialogue_translate` project type and runner that subclasses the Omni V2 runner, inserts `speaker_detect` and `voice_match_ab` before the existing Omni voice-confirmation boundary, and keeps the original Omni translate, TTS duration loop, ASR-window scheduling, subtitle, compose, and export behavior. Speaker automation lives in focused `appcore/dialogue_translate/*` services, while routes/templates expose a separate UI and confirmation API. Per-segment voice support is implemented in the shared TTS engine layer so existing Omni/Multi paths remain unchanged when segments do not carry `voice_id`.

**Tech Stack:** Python 3.12, Flask blueprints, existing `appcore.runtime` pipeline runner, `task_state`, `translation_route_store`, ElevenLabs TTS via `pipeline.tts`, pytest, Flask test client, existing SocketIO runner adapter.

---

## Requirements Lock

- Work only inside the isolated worktree `G:/Code/AutoVideoSrtLocal/.worktrees/dialogue-video-translate-design`.
- Do not connect to Windows local MySQL `127.0.0.1:3306`.
- Keep `/omni-translate`, `/omni-translate-v2`, and `multi_translate` behavior unchanged.
- New pages and APIs must use `@login_required` and `@admin_required`; page entry should also use `@permission_required("dialogue_translate")`.
- Main flow is automatic speaker detection. Manual speaker edits are correction tools only.
- If ASR speaker labels are missing or unreliable, call the diarization fallback. If fallback is unavailable or fails, stop at `speaker_detect` with a clear failure; do not silently degrade to one voice.
- Final rendered subtitles do not include `A:`, `B:`, or `Speaker A:` prefixes. Detail/backend payloads do include speaker, confidence, selected voice, and review reasons.
- The Omni single-speaker TTS duration loop and ASR-window audio placement stay authoritative. Dialogue mode only attaches `voice_id` to each segment before synthesis.

## File Structure

- Create `appcore/dialogue_translate/__init__.py` to mark the focused domain package.
- Create `appcore/dialogue_translate/speaker_detection.py` for provider speaker normalization, diarization fallback decisions, diarization-to-ASR join, A/B reduction, review reasons, and summary payloads.
- Create `appcore/dialogue_translate/diarization.py` for the configurable diarization client interface and HTTP client.
- Create `appcore/dialogue_translate/voice_match.py` for speaker sample window selection and A/B candidate generation using existing embedding and speed-aware matching.
- Create `appcore/dialogue_translate/tts.py` for mapping speaker IDs and confirmed voices onto TTS segments.
- Create `appcore/runtime_dialogue.py` for the Dialogue runner subclass and pipeline step methods.
- Create `web/services/dialogue_pipeline_runner.py` for SocketIO runner start/resume registration.
- Create `web/routes/dialogue_translate.py` for list/detail/start/state/confirm/edit APIs.
- Create `web/templates/dialogue_translate.html` for the independent list/create page.
- Create `web/templates/dialogue_translate_detail.html` for the existing translate detail shell plus the A/B speaker panel.
- Create `web/static/js/dialogue_translate_detail.js` for speaker panel rendering, voice selection, and confirmation calls.
- Modify `pipeline/tts.py` to add per-segment voice synthesis helpers while keeping existing single-voice functions stable.
- Modify `appcore/tts_engines/base.py` and `appcore/tts_engines/elevenlabs.py` to expose per-segment voice synthesis through the engine layer.
- Modify `appcore/runtime/_pipeline_runner.py` to add a no-op TTS segment preparation hook and call it before engine synthesis and native speed regeneration.
- Modify `appcore/runner_dispatch.py` to register/start/resume dialogue runners.
- Modify `appcore/permissions.py` to add `dialogue_translate` permissions, default redirects, and translator defaults.
- Modify `web/app.py` to import/register/exempt/CSRF-guard the new blueprint.
- Add tests:
  - `tests/test_dialogue_speaker_detection.py`
  - `tests/test_dialogue_diarization.py`
  - `tests/test_dialogue_voice_match.py`
  - `tests/test_dialogue_tts.py`
  - `tests/test_dialogue_runtime.py`
  - `tests/test_dialogue_translate_routes.py`
  - `tests/test_dialogue_permissions.py`
  - `tests/test_dialogue_subtitles.py`

## Task 1: Speaker Detection Core

**Files:**
- Create: `appcore/dialogue_translate/__init__.py`
- Create: `appcore/dialogue_translate/speaker_detection.py`
- Test: `tests/test_dialogue_speaker_detection.py`

- [ ] **Step 1: Write failing tests for ASR speaker normalization**

Add `tests/test_dialogue_speaker_detection.py`:

```python
from __future__ import annotations

import pytest

from appcore.dialogue_translate.speaker_detection import (
    REVIEW_EXTRA_SPEAKER,
    REVIEW_LOW_CONFIDENCE,
    REVIEW_OVERLAP,
    build_dialogue_segments,
    join_diarization_to_utterances,
)


def test_provider_speaker_fields_normalize_to_a_b_when_reliable():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0, "speaker": "spk_7", "speaker_confidence": 0.93},
        {"text": "yes", "start_time": 1.2, "end_time": 2.0, "speaker": "spk_9", "speaker_confidence": 0.91},
        {"text": "thanks", "start_time": 2.3, "end_time": 3.0, "speaker": "spk_7", "speaker_confidence": 0.89},
    ]

    result = build_dialogue_segments(utterances)

    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B", "A"]
    assert result["speaker_summary"]["A"]["segment_count"] == 2
    assert result["speaker_summary"]["B"]["duration"] == pytest.approx(0.8)
    assert result["speaker_strategy"] == "asr_provider"
    assert result["review_required_segments"] == []


def test_provider_low_coverage_requests_diarization():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0, "speaker": "spk_1"},
        {"text": "missing", "start_time": 1.1, "end_time": 2.0},
        {"text": "also missing", "start_time": 2.1, "end_time": 3.0},
    ]

    result = build_dialogue_segments(utterances)

    assert result["speaker_strategy"] == "needs_diarization"
    assert result["dialogue_segments"] == []
    assert "asr_provider_speaker_coverage_below_threshold" in result["dialogue_warnings"]


def test_extra_speakers_keep_top_two_and_mark_rest_for_review():
    utterances = [
        {"text": "a1", "start_time": 0.0, "end_time": 5.0, "speaker": "one"},
        {"text": "b1", "start_time": 6.0, "end_time": 9.0, "speaker": "two"},
        {"text": "c1", "start_time": 10.0, "end_time": 11.0, "speaker": "three"},
    ]

    result = build_dialogue_segments(utterances)

    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B", "B"]
    assert result["dialogue_segments"][2]["review_required"] is True
    assert REVIEW_EXTRA_SPEAKER in result["dialogue_segments"][2]["review_reason"]
    assert result["review_required_segments"] == [{"index": 2, "reason": REVIEW_EXTRA_SPEAKER}]


def test_diarization_join_marks_low_overlap_for_review():
    utterances = [
        {"text": "hard to place", "start_time": 10.0, "end_time": 12.0},
    ]
    diarization_segments = [
        {"speaker": "x", "start_time": 10.0, "end_time": 10.8, "confidence": 0.95},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["speaker_id"] == "A"
    assert segment["speaker_source"] == "diarization"
    assert segment["review_required"] is True
    assert REVIEW_LOW_CONFIDENCE in segment["review_reason"]


def test_diarization_join_marks_overlapping_speech():
    utterances = [
        {"text": "two people", "start_time": 0.0, "end_time": 2.0},
    ]
    diarization_segments = [
        {"speaker": "x", "start_time": 0.0, "end_time": 1.5, "confidence": 0.91},
        {"speaker": "y", "start_time": 0.5, "end_time": 2.0, "confidence": 0.9},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["overlap"] is True
    assert segment["review_required"] is True
    assert REVIEW_OVERLAP in segment["review_reason"]
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_dialogue_speaker_detection.py -q
```

Expected: import failure for `appcore.dialogue_translate`.

- [ ] **Step 3: Add the speaker detection implementation**

Create `appcore/dialogue_translate/__init__.py`:

```python
"""Dialogue video translation helpers."""
```

Create `appcore/dialogue_translate/speaker_detection.py`:

```python
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

REVIEW_LOW_CONFIDENCE = "low_speaker_confidence"
REVIEW_OVERLAP = "speaker_overlap"
REVIEW_EXTRA_SPEAKER = "unsupported_extra_speaker"

MIN_PROVIDER_COVERAGE = 0.90
MIN_JOIN_OVERLAP_RATIO = 0.60
MIN_SPEAKER_CONFIDENCE = 0.70

_SPEAKER_KEYS = ("speaker_id", "speaker", "speaker_label", "channel_tag")
_CONFIDENCE_KEYS = ("speaker_confidence", "confidence", "speaker_score")


def _duration(item: dict) -> float:
    return max(0.0, float(item.get("end_time") or 0.0) - float(item.get("start_time") or 0.0))


def _speaker_label(item: dict) -> str:
    for key in _SPEAKER_KEYS:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _confidence(item: dict, default: float = 1.0) -> float:
    for key in _CONFIDENCE_KEYS:
        if item.get(key) is None:
            continue
        try:
            return float(item[key])
        except (TypeError, ValueError):
            return default
    return default


def _speaker_rank(labels_by_segment: list[str], utterances: list[dict]) -> list[str]:
    durations: dict[str, float] = defaultdict(float)
    first_seen: dict[str, int] = {}
    for index, label in enumerate(labels_by_segment):
        if not label:
            continue
        durations[label] += _duration(utterances[index])
        first_seen.setdefault(label, index)
    return sorted(durations.keys(), key=lambda label: (-durations[label], first_seen[label]))


def _speaker_map(labels_by_segment: list[str], utterances: list[dict]) -> dict[str, str]:
    ranked = _speaker_rank(labels_by_segment, utterances)
    mapping: dict[str, str] = {}
    if ranked:
        mapping[ranked[0]] = "A"
    if len(ranked) >= 2:
        mapping[ranked[1]] = "B"
    for label in ranked[2:]:
        mapping[label] = "B"
    return mapping


def _review_reason(*reasons: str) -> str:
    return ",".join(reason for reason in reasons if reason)


def _summary(segments: Iterable[dict]) -> dict:
    summary = {
        "A": {"segment_count": 0, "duration": 0.0},
        "B": {"segment_count": 0, "duration": 0.0},
    }
    for segment in segments:
        speaker = segment.get("speaker_id")
        if speaker not in summary:
            continue
        summary[speaker]["segment_count"] += 1
        summary[speaker]["duration"] = round(summary[speaker]["duration"] + _duration(segment), 3)
    return summary


def _review_index_payload(segments: list[dict]) -> list[dict]:
    return [
        {"index": segment["index"], "reason": segment["review_reason"]}
        for segment in segments
        if segment.get("review_required")
    ]


def build_dialogue_segments(utterances: list[dict]) -> dict:
    labels = [_speaker_label(item) for item in utterances]
    coverage = (sum(1 for label in labels if label) / max(1, len(labels))) if utterances else 0.0
    if coverage < MIN_PROVIDER_COVERAGE:
        return {
            "speaker_strategy": "needs_diarization",
            "dialogue_segments": [],
            "speaker_summary": {"A": {"segment_count": 0, "duration": 0.0}, "B": {"segment_count": 0, "duration": 0.0}},
            "review_required_segments": [],
            "dialogue_warnings": ["asr_provider_speaker_coverage_below_threshold"],
        }

    mapping = _speaker_map(labels, utterances)
    top_labels = {label for label, speaker in mapping.items() if speaker in {"A", "B"}}
    segments: list[dict] = []
    for index, utterance in enumerate(utterances):
        raw_label = labels[index]
        confidence = _confidence(utterance)
        extra_speaker = raw_label not in top_labels
        low_confidence = confidence < MIN_SPEAKER_CONFIDENCE
        reasons = []
        if extra_speaker:
            reasons.append(REVIEW_EXTRA_SPEAKER)
        if low_confidence:
            reasons.append(REVIEW_LOW_CONFIDENCE)
        segment = {
            "index": int(utterance.get("index", index)),
            "text": utterance.get("text", ""),
            "start_time": float(utterance.get("start_time") or 0.0),
            "end_time": float(utterance.get("end_time") or 0.0),
            "speaker_id": mapping.get(raw_label, "A"),
            "raw_speaker_id": raw_label,
            "speaker_confidence": confidence,
            "speaker_source": "asr_provider",
            "overlap": False,
            "review_required": bool(reasons),
            "review_reason": _review_reason(*reasons),
        }
        segments.append(segment)

    return {
        "speaker_strategy": "asr_provider",
        "dialogue_segments": segments,
        "speaker_summary": _summary(segments),
        "review_required_segments": _review_index_payload(segments),
        "dialogue_warnings": ["asr_provider_more_than_two_speakers"] if len(set(labels)) > 2 else [],
    }


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def join_diarization_to_utterances(utterances: list[dict], diarization_segments: list[dict]) -> dict:
    diar_labels = [_speaker_label(item) for item in diarization_segments]
    mapping = _speaker_map(diar_labels, diarization_segments)
    segments: list[dict] = []
    for index, utterance in enumerate(utterances):
        u_start = float(utterance.get("start_time") or 0.0)
        u_end = float(utterance.get("end_time") or 0.0)
        u_duration = max(0.001, u_end - u_start)
        by_label: dict[str, float] = defaultdict(float)
        best_confidence = 0.0
        for diar in diarization_segments:
            label = _speaker_label(diar)
            overlap = _overlap_seconds(u_start, u_end, float(diar.get("start_time") or 0.0), float(diar.get("end_time") or 0.0))
            if overlap <= 0:
                continue
            by_label[label] += overlap
            best_confidence = max(best_confidence, _confidence(diar, default=0.0))
        ranked = sorted(by_label.items(), key=lambda item: item[1], reverse=True)
        raw_label = ranked[0][0] if ranked else ""
        overlap_ratio = (ranked[0][1] / u_duration) if ranked else 0.0
        overlap = len([item for item in ranked if item[1] > 0]) > 1
        reasons = []
        if overlap_ratio < MIN_JOIN_OVERLAP_RATIO or best_confidence < MIN_SPEAKER_CONFIDENCE:
            reasons.append(REVIEW_LOW_CONFIDENCE)
        if overlap:
            reasons.append(REVIEW_OVERLAP)
        segment = {
            "index": int(utterance.get("index", index)),
            "text": utterance.get("text", ""),
            "start_time": u_start,
            "end_time": u_end,
            "speaker_id": mapping.get(raw_label, "A"),
            "raw_speaker_id": raw_label,
            "speaker_confidence": round(max(0.0, min(1.0, overlap_ratio * best_confidence)), 4),
            "speaker_source": "diarization",
            "overlap": overlap,
            "review_required": bool(reasons),
            "review_reason": _review_reason(*reasons),
        }
        segments.append(segment)
    return {
        "speaker_strategy": "diarization",
        "dialogue_segments": segments,
        "speaker_summary": _summary(segments),
        "review_required_segments": _review_index_payload(segments),
        "dialogue_warnings": [],
    }
```

- [ ] **Step 4: Run the speaker tests**

Run:

```powershell
pytest tests/test_dialogue_speaker_detection.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit speaker detection core**

Run:

```powershell
git add appcore/dialogue_translate/__init__.py appcore/dialogue_translate/speaker_detection.py tests/test_dialogue_speaker_detection.py
git commit -m "feat: add dialogue speaker detection core"
```

## Task 2: Diarization Fallback Client

**Files:**
- Create: `appcore/dialogue_translate/diarization.py`
- Modify: `appcore/dialogue_translate/speaker_detection.py`
- Test: `tests/test_dialogue_diarization.py`

- [ ] **Step 1: Write failing tests for fallback behavior**

Add `tests/test_dialogue_diarization.py`:

```python
from __future__ import annotations

import pytest

from appcore.dialogue_translate.diarization import (
    DiarizationUnavailable,
    HttpDiarizationClient,
    resolve_diarization_client,
)
from appcore.dialogue_translate.speaker_detection import detect_dialogue_segments


class FakeDiarizationClient:
    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        assert audio_path == "input.mp4"
        assert task_id == "task-1"
        return [
            {"speaker": "s1", "start_time": 0.0, "end_time": 1.0, "confidence": 0.95},
            {"speaker": "s2", "start_time": 1.2, "end_time": 2.0, "confidence": 0.93},
        ]


def test_detect_uses_diarization_when_provider_is_unreliable():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0},
        {"text": "yes", "start_time": 1.2, "end_time": 2.0},
    ]

    result = detect_dialogue_segments(
        utterances=utterances,
        audio_path="input.mp4",
        task_id="task-1",
        diarization_client=FakeDiarizationClient(),
    )

    assert result["speaker_strategy"] == "diarization"
    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B"]


def test_detect_raises_when_diarization_required_but_unavailable():
    utterances = [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    with pytest.raises(DiarizationUnavailable) as exc:
        detect_dialogue_segments(
            utterances=utterances,
            audio_path="input.mp4",
            task_id="task-2",
            diarization_client=None,
        )

    assert "diarization fallback is required" in str(exc.value)


def test_resolve_diarization_client_requires_endpoint(monkeypatch):
    monkeypatch.delenv("DIALOGUE_DIARIZATION_URL", raising=False)

    with pytest.raises(DiarizationUnavailable):
        resolve_diarization_client()


def test_http_diarization_client_posts_audio(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"segments": [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]}

    def fake_post(url, files, data, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        files["audio"].close()
        return FakeResponse()

    audio = monkeypatch.context()
    with audio:
        import tempfile

        path = tempfile.NamedTemporaryFile(delete=False)
        path.write(b"audio")
        path.close()
        monkeypatch.setattr("requests.post", fake_post)

        client = HttpDiarizationClient(endpoint="http://diarizer.local/run", timeout_seconds=12)
        segments = client.run(audio_path=path.name, task_id="task-http")

    assert segments == [{"speaker": "x", "start_time": 0.0, "end_time": 1.0}]
    assert captured["url"] == "http://diarizer.local/run"
    assert captured["data"] == {"task_id": "task-http"}
    assert captured["timeout"] == 12
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_dialogue_diarization.py -q
```

Expected: import failure for `appcore.dialogue_translate.diarization` and missing `detect_dialogue_segments`.

- [ ] **Step 3: Implement the diarization client and detection wrapper**

Create `appcore/dialogue_translate/diarization.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


class DiarizationUnavailable(RuntimeError):
    """Raised when automatic diarization is required but cannot run."""


@dataclass(frozen=True)
class HttpDiarizationClient:
    endpoint: str
    timeout_seconds: int = 120

    def run(self, *, audio_path: str, task_id: str) -> list[dict]:
        import requests

        if not os.path.exists(audio_path):
            raise DiarizationUnavailable(f"audio_path does not exist: {audio_path}")
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                self.endpoint,
                files={"audio": audio_file},
                data={"task_id": task_id},
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json() or {}
        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise DiarizationUnavailable("diarization response missing segments")
        return segments


def resolve_diarization_client() -> HttpDiarizationClient:
    endpoint = (os.getenv("DIALOGUE_DIARIZATION_URL") or "").strip()
    if not endpoint:
        raise DiarizationUnavailable("DIALOGUE_DIARIZATION_URL is not configured")
    timeout = int(os.getenv("DIALOGUE_DIARIZATION_TIMEOUT", "120") or "120")
    return HttpDiarizationClient(endpoint=endpoint, timeout_seconds=timeout)
```

Modify `appcore/dialogue_translate/speaker_detection.py` to add:

```python
from appcore.dialogue_translate.diarization import DiarizationUnavailable, resolve_diarization_client


def detect_dialogue_segments(
    *,
    utterances: list[dict],
    audio_path: str,
    task_id: str,
    diarization_client=None,
) -> dict:
    provider_result = build_dialogue_segments(utterances)
    if provider_result["speaker_strategy"] != "needs_diarization":
        return provider_result
    client = diarization_client
    if client is None:
        try:
            client = resolve_diarization_client()
        except DiarizationUnavailable as exc:
            raise DiarizationUnavailable(
                f"diarization fallback is required for task {task_id}: {exc}"
            ) from exc
    diarization_segments = client.run(audio_path=audio_path, task_id=task_id)
    result = join_diarization_to_utterances(utterances, diarization_segments)
    warnings = list(provider_result.get("dialogue_warnings") or [])
    result["dialogue_warnings"] = warnings + list(result.get("dialogue_warnings") or [])
    return result
```

- [ ] **Step 4: Run diarization tests**

Run:

```powershell
pytest tests/test_dialogue_diarization.py tests/test_dialogue_speaker_detection.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit diarization fallback**

Run:

```powershell
git add appcore/dialogue_translate/diarization.py appcore/dialogue_translate/speaker_detection.py tests/test_dialogue_diarization.py tests/test_dialogue_speaker_detection.py
git commit -m "feat: add dialogue diarization fallback"
```

## Task 3: A/B Voice Matching

**Files:**
- Create: `appcore/dialogue_translate/voice_match.py`
- Test: `tests/test_dialogue_voice_match.py`

- [ ] **Step 1: Write failing tests for A/B sample selection and matching**

Add `tests/test_dialogue_voice_match.py`:

```python
from __future__ import annotations

import base64

from appcore.dialogue_translate.voice_match import (
    INSUFFICIENT_SAMPLE_REASON,
    build_speaker_sample_windows,
    match_voices_for_speakers,
)


def test_sample_windows_skip_review_and_overlap_segments():
    segments = [
        {"index": 0, "speaker_id": "A", "start_time": 0.0, "end_time": 2.0, "review_required": False, "overlap": False},
        {"index": 1, "speaker_id": "A", "start_time": 2.1, "end_time": 3.0, "review_required": True, "overlap": False},
        {"index": 2, "speaker_id": "B", "start_time": 3.2, "end_time": 7.2, "review_required": False, "overlap": False},
        {"index": 3, "speaker_id": "A", "start_time": 8.0, "end_time": 13.0, "review_required": False, "overlap": False},
    ]

    result = build_speaker_sample_windows(segments, min_duration=3.0, target_duration=8.0)

    assert result["A"]["sample_windows"] == [[8.0, 13.0], [0.0, 2.0]]
    assert result["A"]["match_warnings"] == []
    assert result["B"]["sample_windows"] == [[3.2, 7.2]]
    assert result["B"]["match_warnings"] == []


def test_sample_windows_warn_when_speaker_has_too_little_audio():
    segments = [
        {"speaker_id": "A", "start_time": 0.0, "end_time": 1.0, "review_required": False, "overlap": False},
    ]

    result = build_speaker_sample_windows(segments, min_duration=3.0, target_duration=8.0)

    assert result["A"]["sample_windows"] == [[0.0, 1.0]]
    assert result["A"]["match_warnings"] == [INSUFFICIENT_SAMPLE_REASON]
    assert result["B"]["sample_windows"] == []
    assert result["B"]["match_warnings"] == [INSUFFICIENT_SAMPLE_REASON]


def test_match_voices_for_speakers_uses_existing_embedding_and_speed_match(monkeypatch, tmp_path):
    sample_specs = {
        "A": {"sample_windows": [[0.0, 5.0]], "match_warnings": []},
        "B": {"sample_windows": [[5.2, 10.0]], "match_warnings": []},
    }
    calls = []

    def fake_extract(video_path, windows, out_path):
        calls.append(("extract", video_path, windows, out_path.name))
        out_path.write_bytes(b"wav")
        return str(out_path)

    def fake_embed(path):
        return f"vec:{path}"

    def fake_serialize(vec):
        return vec.encode("utf-8")

    def fake_match(vec, **kwargs):
        calls.append(("match", vec, kwargs["language"], kwargs["source_utterances"]))
        suffix = "a" if "speaker_A" in vec else "b"
        return [{"voice_id": f"voice-{suffix}", "name": f"Voice {suffix.upper()}", "similarity": 0.91}]

    monkeypatch.setattr("appcore.dialogue_translate.voice_match.extract_sample_for_windows", fake_extract)
    monkeypatch.setattr("pipeline.voice_embedding.embed_audio_file", fake_embed)
    monkeypatch.setattr("pipeline.voice_embedding.serialize_embedding", fake_serialize)
    monkeypatch.setattr("pipeline.voice_match_speed.match_candidates_speed_aware", fake_match)
    monkeypatch.setattr("appcore.dialogue_translate.voice_match.resolve_default_voice", lambda lang, user_id=None: "default-voice")

    profiles = match_voices_for_speakers(
        video_path="video.mp4",
        task_dir=str(tmp_path),
        target_lang="en",
        dialogue_segments=[{"text": "hi"}, {"text": "yes"}],
        sample_specs=sample_specs,
        user_id=7,
    )

    assert profiles["A"]["candidates"][0]["voice_id"] == "voice-a"
    assert profiles["B"]["candidates"][0]["voice_id"] == "voice-b"
    assert base64.b64decode(profiles["A"]["query_embedding"]).startswith(b"vec:")
    assert calls[0][0] == "extract"
    assert calls[1][0] == "match"
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_dialogue_voice_match.py -q
```

Expected: import failure for `appcore.dialogue_translate.voice_match`.

- [ ] **Step 3: Implement A/B voice matching helpers**

Create `appcore/dialogue_translate/voice_match.py`:

```python
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from appcore.video_translate_defaults import resolve_default_voice

INSUFFICIENT_SAMPLE_REASON = "insufficient_speaker_sample"


def _duration(segment: dict) -> float:
    return max(0.0, float(segment.get("end_time") or 0.0) - float(segment.get("start_time") or 0.0))


def build_speaker_sample_windows(
    dialogue_segments: list[dict],
    *,
    min_duration: float = 3.0,
    target_duration: float = 10.0,
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for speaker_id in ("A", "B"):
        usable = [
            segment for segment in dialogue_segments
            if segment.get("speaker_id") == speaker_id
            and not segment.get("review_required")
            and not segment.get("overlap")
            and _duration(segment) > 0
        ]
        usable.sort(key=_duration, reverse=True)
        windows: list[list[float]] = []
        total = 0.0
        for segment in usable:
            windows.append([float(segment["start_time"]), float(segment["end_time"])])
            total += _duration(segment)
            if total >= target_duration:
                break
        result[speaker_id] = {
            "sample_windows": windows,
            "sample_duration": round(total, 3),
            "match_warnings": [] if total >= min_duration else [INSUFFICIENT_SAMPLE_REASON],
        }
    return result


def extract_sample_for_windows(video_path: str, windows: list[list[float]], out_path: Path) -> str:
    if not windows:
        raise ValueError("sample windows required")
    list_path = out_path.with_suffix(".concat.txt")
    temp_files: list[Path] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(list_path, "w", encoding="utf-8") as concat_file:
            for index, (start_time, end_time) in enumerate(windows):
                clip_path = out_path.with_name(f"{out_path.stem}.{index}.wav")
                temp_files.append(clip_path)
                duration = max(0.001, float(end_time) - float(start_time))
                cut = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        str(start_time),
                        "-t",
                        str(duration),
                        "-i",
                        video_path,
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(clip_path),
                    ],
                    capture_output=True,
                    text=True,
                )
                if cut.returncode != 0:
                    raise RuntimeError(f"speaker sample extraction failed: {cut.stderr}")
                concat_file.write(f"file '{os.path.abspath(clip_path)}'\n")
        concat = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)],
            capture_output=True,
            text=True,
        )
        if concat.returncode != 0:
            raise RuntimeError(f"speaker sample concat failed: {concat.stderr}")
        return str(out_path)
    finally:
        for path in temp_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            list_path.unlink(missing_ok=True)
        except OSError:
            pass


def match_voices_for_speakers(
    *,
    video_path: str,
    task_dir: str,
    target_lang: str,
    dialogue_segments: list[dict],
    sample_specs: dict[str, dict],
    user_id: int | None = None,
) -> dict[str, dict]:
    from pipeline.voice_embedding import embed_audio_file, serialize_embedding
    from pipeline import voice_match_speed

    default_voice_id = resolve_default_voice(target_lang, user_id=user_id)
    profiles: dict[str, dict] = {}
    for speaker_id in ("A", "B"):
        spec = sample_specs.get(speaker_id) or {}
        sample_windows = list(spec.get("sample_windows") or [])
        sample_path = ""
        candidates: list[dict] = []
        query_embedding = None
        if sample_windows:
            out_path = Path(task_dir) / f"speaker_{speaker_id}_sample.wav"
            sample_path = extract_sample_for_windows(video_path, sample_windows, out_path)
            vec = embed_audio_file(sample_path)
            query_embedding = base64.b64encode(serialize_embedding(vec)).decode("ascii")
            candidates = voice_match_speed.match_candidates_speed_aware(
                vec,
                language=target_lang,
                source_utterances=[
                    segment for segment in dialogue_segments
                    if segment.get("speaker_id") == speaker_id
                ],
                candidate_pool_size=20,
                top_k=20,
                exclude_voice_ids={default_voice_id} if default_voice_id else None,
            ) or []
            for candidate in candidates:
                candidate["similarity"] = float(candidate.get("similarity", 0.0) or 0.0)
        profiles[speaker_id] = {
            "sample_path": sample_path,
            "sample_windows": sample_windows,
            "sample_duration": spec.get("sample_duration", 0.0),
            "query_embedding": query_embedding,
            "candidates": candidates,
            "selected_voice": None,
            "match_warnings": list(spec.get("match_warnings") or []),
        }
    return profiles
```

- [ ] **Step 4: Run voice match tests**

Run:

```powershell
pytest tests/test_dialogue_voice_match.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit A/B voice matching**

Run:

```powershell
git add appcore/dialogue_translate/voice_match.py tests/test_dialogue_voice_match.py
git commit -m "feat: match dialogue speaker voices"
```

## Task 4: Per-Segment Voice TTS Support

**Files:**
- Modify: `pipeline/tts.py`
- Modify: `appcore/tts_engines/base.py`
- Modify: `appcore/tts_engines/elevenlabs.py`
- Create: `appcore/dialogue_translate/tts.py`
- Test: `tests/test_dialogue_tts.py`

- [ ] **Step 1: Write failing tests for per-segment voice generation and mapping**

Add `tests/test_dialogue_tts.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments


def test_apply_speaker_voices_to_tts_segments_maps_by_index():
    tts_segments = [
        {"index": 0, "tts_text": "hello"},
        {"index": 1, "tts_text": "yes"},
    ]
    dialogue_segments = [
        {"index": 0, "speaker_id": "A"},
        {"index": 1, "speaker_id": "B"},
    ]
    selected = {
        "A": {"voice_id": "voice-a", "name": "A Voice"},
        "B": {"voice_id": "voice-b", "name": "B Voice"},
    }

    mapped = apply_speaker_voices_to_tts_segments(tts_segments, dialogue_segments, selected)

    assert mapped[0]["speaker_id"] == "A"
    assert mapped[0]["voice_id"] == "voice-a"
    assert mapped[1]["speaker_id"] == "B"
    assert mapped[1]["voice_id"] == "voice-b"


def test_generate_full_audio_with_segment_voices_uses_each_segment_voice(monkeypatch, tmp_path):
    calls = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        calls.append((text, voice_id, output_path))
        with open(output_path, "wb") as handle:
            handle.write(b"audio-data" * 200)
        return output_path

    def fake_duration(path):
        return 1.25

    def fake_run(args, capture_output=True, text=True):
        output_path = args[-1]
        with open(output_path, "wb") as handle:
            handle.write(b"full-audio")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pipeline.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", fake_duration)
    monkeypatch.setattr("pipeline.tts.subprocess.run", fake_run)

    from pipeline.tts import generate_full_audio_with_segment_voices

    result = generate_full_audio_with_segment_voices(
        [
            {"tts_text": "one", "voice_id": "voice-a"},
            {"tts_text": "two", "voice_id": "voice-b"},
        ],
        default_voice_id="fallback",
        output_dir=str(tmp_path),
        variant="round_1",
    )

    assert [call[1] for call in calls] == ["voice-a", "voice-b"]
    assert result["segments"][0]["tts_duration"] == 1.25
    assert result["full_audio_path"].endswith("tts_full.round_1.mp3")


def test_elevenlabs_engine_uses_segment_voice_helper_when_present(monkeypatch, tmp_path):
    captured = {}

    def fake_segment_voice_helper(segments, default_voice_id, output_dir, **kwargs):
        captured["segments"] = segments
        captured["default_voice_id"] = default_voice_id
        captured["kwargs"] = kwargs
        return {"full_audio_path": "full.mp3", "segments": segments}

    monkeypatch.setattr("pipeline.tts.generate_full_audio_with_segment_voices", fake_segment_voice_helper)

    from appcore.tts_engines.elevenlabs import ElevenLabsEngine

    engine = ElevenLabsEngine()
    result = engine.synthesize_full(
        [{"tts_text": "hello", "voice_id": "voice-a"}],
        "default-voice",
        str(tmp_path),
        variant="normal",
    )

    assert result["full_audio_path"] == "full.mp3"
    assert captured["default_voice_id"] == "default-voice"
    assert captured["kwargs"]["variant"] == "normal"
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
pytest tests/test_dialogue_tts.py -q
```

Expected: missing `appcore.dialogue_translate.tts` and `generate_full_audio_with_segment_voices`.

- [ ] **Step 3: Add speaker-to-voice mapping helper**

Create `appcore/dialogue_translate/tts.py`:

```python
from __future__ import annotations


def _segment_index(segment: dict, fallback: int) -> int:
    for key in ("index", "source_index", "segment_index"):
        if segment.get(key) is None:
            continue
        try:
            return int(segment[key])
        except (TypeError, ValueError):
            continue
    return fallback


def apply_speaker_voices_to_tts_segments(
    tts_segments: list[dict],
    dialogue_segments: list[dict],
    selected_voice_by_speaker: dict,
) -> list[dict]:
    dialogue_by_index = {
        int(segment.get("index", index)): segment
        for index, segment in enumerate(dialogue_segments)
    }
    mapped: list[dict] = []
    for fallback_index, segment in enumerate(tts_segments):
        next_segment = dict(segment)
        dialogue_segment = dialogue_by_index.get(_segment_index(next_segment, fallback_index), {})
        speaker_id = dialogue_segment.get("speaker_id") or next_segment.get("speaker_id")
        if speaker_id:
            selected = selected_voice_by_speaker.get(speaker_id) or {}
            next_segment["speaker_id"] = speaker_id
            next_segment["voice_id"] = selected.get("voice_id") or selected.get("elevenlabs_voice_id") or next_segment.get("voice_id")
            next_segment["voice_name"] = selected.get("name") or selected.get("voice_name") or next_segment.get("voice_name")
        mapped.append(next_segment)
    return mapped
```

- [ ] **Step 4: Add shared per-segment TTS helpers**

Modify `pipeline/tts.py` by extracting the common body from `generate_full_audio()` into a helper that resolves voice per segment. Keep `generate_full_audio()` as the existing single-voice wrapper:

```python
def _generate_full_audio_impl(
    segments: List[Dict],
    output_dir: str,
    *,
    voice_id_for_segment: Callable[[Dict], str],
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    speed: float | None = None,
    stability: float | None = None,
    similarity_boost: float | None = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    seg_dir = (
        os.path.join(output_dir, "tts_segments", variant)
        if variant else os.path.join(output_dir, "tts_segments")
    )
    os.makedirs(seg_dir, exist_ok=True)
    total = len(segments)
    pool = _get_tts_pool()
    state = {"total": total, "active": 0, "queued": total, "done": 0}
    state_lock = threading.Lock()
    updated_segments: list[dict] = [dict(segment) for segment in segments]
    futures = {}

    def _submit(index: int, segment: dict):
        text = segment.get("tts_text") or segment.get("text") or ""
        voice_id = voice_id_for_segment(segment)
        if not voice_id:
            raise ValueError(f"voice_id missing for tts segment {index}")
        output_path = os.path.join(seg_dir, f"segment_{index:04d}.mp3")
        return pool.submit(
            generate_segment_audio,
            text,
            voice_id,
            output_path,
            elevenlabs_api_key=elevenlabs_api_key,
            model_id=model_id,
            language_code=language_code,
            speed=speed,
            stability=stability,
            similarity_boost=similarity_boost,
        )

    for index, segment in enumerate(updated_segments):
        futures[_submit(index, segment)] = index
        _emit_progress(on_progress, state_lock, state, "submitted", {"index": index})

    for future in as_completed(futures):
        index = futures[future]
        path = future.result()
        duration = _get_audio_duration(path)
        updated_segments[index]["tts_path"] = path
        updated_segments[index]["tts_duration"] = duration
        updated_segments[index]["voice_id"] = voice_id_for_segment(updated_segments[index])
        if on_segment_done:
            on_segment_done(index + 1, total, updated_segments[index])
        _emit_progress(on_progress, state_lock, state, "completed", {"index": index})

    concat_list_path = os.path.join(seg_dir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_file:
        for segment in updated_segments:
            concat_file.write(f"file '{os.path.abspath(segment['tts_path'])}'\n")
    suffix = f".{variant}" if variant else ""
    full_audio_path = os.path.join(output_dir, f"tts_full{suffix}.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")
    return {"full_audio_path": full_audio_path, "segments": updated_segments}


def generate_full_audio_with_segment_voices(
    segments: List[Dict],
    default_voice_id: str,
    output_dir: str,
    *,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    return _generate_full_audio_impl(
        segments,
        output_dir,
        voice_id_for_segment=lambda segment: str(segment.get("voice_id") or default_voice_id or ""),
        variant=variant,
        elevenlabs_api_key=elevenlabs_api_key,
        model_id=model_id,
        language_code=language_code,
        on_progress=on_progress,
        on_segment_done=on_segment_done,
    )
```

Update the existing `generate_full_audio()` wrapper so it calls `_generate_full_audio_impl(..., voice_id_for_segment=lambda segment: voice_id, ...)` and leaves its public signature unchanged.

Add the speed wrapper:

```python
def regenerate_full_audio_with_segment_voices_speed(
    segments: List[Dict],
    default_voice_id: str,
    output_dir: str,
    *,
    variant: str,
    speed: float,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    stability: float | None = None,
    similarity_boost: float | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    return _generate_full_audio_impl(
        segments,
        output_dir,
        voice_id_for_segment=lambda segment: str(segment.get("voice_id") or default_voice_id or ""),
        variant=variant,
        elevenlabs_api_key=elevenlabs_api_key,
        model_id=model_id,
        language_code=language_code,
        speed=speed,
        stability=stability,
        similarity_boost=similarity_boost,
        on_segment_done=on_segment_done,
    )
```

- [ ] **Step 5: Route the engine through per-segment helpers**

Modify `appcore/tts_engines/base.py` to add a non-breaking flag:

```python
    supports_segment_voice_override: bool = False
```

Modify `appcore/tts_engines/elevenlabs.py`:

```python
    supports_segment_voice_override = True

    def _has_segment_voice_override(self, segments: list[dict], voice_id: str) -> bool:
        return any((segment.get("voice_id") or voice_id) != voice_id for segment in segments)
```

In `synthesize_full()`, call the new helper when `_has_segment_voice_override()` returns true:

```python
        if self._has_segment_voice_override(segments, voice_id):
            from pipeline.tts import generate_full_audio_with_segment_voices

            return generate_full_audio_with_segment_voices(
                segments,
                default_voice_id=voice_id,
                output_dir=output_dir,
                **kwargs,
            )
```

In `regenerate_with_speed()`, call `regenerate_full_audio_with_segment_voices_speed()` when any segment carries a different `voice_id`.

- [ ] **Step 6: Run TTS tests and existing TTS regression**

Run:

```powershell
pytest tests/test_dialogue_tts.py -q
pytest tests/test_omni_av_sync_audit.py -q
```

Expected: all tests pass. If `tests/test_omni_av_sync_audit.py` uses no local MySQL, keep it; if it attempts `127.0.0.1:3306`, stop that command and record the project-rule restriction.

- [ ] **Step 7: Commit per-segment TTS support**

Run:

```powershell
git add pipeline/tts.py appcore/tts_engines/base.py appcore/tts_engines/elevenlabs.py appcore/dialogue_translate/tts.py tests/test_dialogue_tts.py
git commit -m "feat: support per-segment dialogue TTS voices"
```

## Task 5: Runtime Hook and Dialogue Runner

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Create: `appcore/runtime_dialogue.py`
- Modify: `appcore/runner_dispatch.py`
- Create: `web/services/dialogue_pipeline_runner.py`
- Test: `tests/test_dialogue_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Add `tests/test_dialogue_runtime.py`:

```python
from __future__ import annotations

from appcore.runtime_dialogue import DialogueTranslateRunner


def test_dialogue_pipeline_replaces_voice_match_with_speaker_steps():
    names = DialogueTranslateRunner.pipeline_step_names_for_config(
        {
            "asr_post": "asr_normalize",
            "shot_decompose": False,
            "translate_algo": "standard",
            "source_anchored": True,
            "tts_strategy": "five_round_rewrite",
            "subtitle": "asr_realign",
            "voice_separation": True,
            "loudness_match": True,
            "av_sync_audit": "off",
        }
    )

    assert "voice_match" not in names
    assert names.index("speaker_detect") < names.index("voice_match_ab")
    assert names.index("voice_match_ab") < names.index("alignment")
    assert names.index("alignment") < names.index("translate")


def test_prepare_tts_segments_attaches_dialogue_voice_ids():
    runner = DialogueTranslateRunner(bus=None, user_id=1)
    task = {
        "dialogue_segments": [
            {"index": 0, "speaker_id": "A"},
            {"index": 1, "speaker_id": "B"},
        ],
        "selected_voice_by_speaker": {
            "A": {"voice_id": "voice-a", "name": "A Voice"},
            "B": {"voice_id": "voice-b", "name": "B Voice"},
        },
    }

    segments = runner._prepare_tts_segments_for_audio_gen(
        task,
        [{"index": 0, "tts_text": "one"}, {"index": 1, "tts_text": "two"}],
    )

    assert [segment["voice_id"] for segment in segments] == ["voice-a", "voice-b"]


def test_voice_match_ab_waits_for_confirmation(monkeypatch, tmp_path):
    updates = {}
    step_updates = []

    monkeypatch.setattr("appcore.runtime_dialogue.task_state.get", lambda task_id: {
        "task_dir": str(tmp_path),
        "video_path": "video.mp4",
        "target_lang": "en",
        "dialogue_segments": [
            {"speaker_id": "A", "start_time": 0.0, "end_time": 4.0, "review_required": False, "overlap": False},
            {"speaker_id": "B", "start_time": 4.2, "end_time": 8.0, "review_required": False, "overlap": False},
        ],
    })
    monkeypatch.setattr("appcore.runtime_dialogue.task_state.update", lambda task_id, **kwargs: updates.update(kwargs))
    monkeypatch.setattr("appcore.runtime_dialogue.task_state.set_current_review_step", lambda task_id, step: updates.update(current_review_step=step))

    def fake_set_step(task_id, step, status, message="", **kwargs):
        step_updates.append((step, status, message))

    monkeypatch.setattr(DialogueTranslateRunner, "_set_step", fake_set_step)
    monkeypatch.setattr("appcore.dialogue_translate.voice_match.build_speaker_sample_windows", lambda segments: {
        "A": {"sample_windows": [[0.0, 4.0]], "match_warnings": []},
        "B": {"sample_windows": [[4.2, 8.0]], "match_warnings": []},
    })
    monkeypatch.setattr("appcore.dialogue_translate.voice_match.match_voices_for_speakers", lambda **kwargs: {
        "A": {"candidates": [{"voice_id": "voice-a"}]},
        "B": {"candidates": [{"voice_id": "voice-b"}]},
    })

    runner = DialogueTranslateRunner(bus=None, user_id=1)
    runner._step_voice_match_ab("task-1")

    assert updates["speaker_profiles"]["A"]["candidates"][0]["voice_id"] == "voice-a"
    assert updates["current_review_step"] == "voice_match_ab"
    assert step_updates[-1][0:2] == ("voice_match_ab", "waiting")
```

- [ ] **Step 2: Run the failing runtime tests**

Run:

```powershell
pytest tests/test_dialogue_runtime.py -q
```

Expected: import failure for `appcore.runtime_dialogue`.

- [ ] **Step 3: Add a TTS preparation hook to the shared runner**

Modify `appcore/runtime/_pipeline_runner.py`:

```python
    def _prepare_tts_segments_for_audio_gen(self, task: dict, tts_segments: list[dict]) -> list[dict]:
        return tts_segments
```

Call it immediately after `tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)`:

```python
            tts_segments = self._prepare_tts_segments_for_audio_gen(
                task_state.get(task_id) or {},
                tts_segments,
            )
```

This call must happen before `tts_engine.synthesize_full(...)` so the engine sees `segment["voice_id"]`.

- [ ] **Step 4: Implement the Dialogue runner**

Create `appcore/runtime_dialogue.py`:

```python
from __future__ import annotations

import logging

from appcore import task_state
from appcore.runtime_omni_v2 import OmniV2TranslateRunner

log = logging.getLogger(__name__)


class DialogueTranslateRunner(OmniV2TranslateRunner):
    project_type: str = "dialogue_translate"
    profile_code: str = "omni_v2"

    @staticmethod
    def pipeline_step_names_for_config(config: dict, *, include_analysis: bool = False) -> list[str]:
        names = OmniV2TranslateRunner.pipeline_step_names_for_config(
            config,
            include_analysis=include_analysis,
        )
        if "voice_match" not in names:
            return names
        idx = names.index("voice_match")
        return names[:idx] + ["speaker_detect", "voice_match_ab"] + names[idx + 1:]

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
        rewritten = []
        for name, fn in steps:
            if name == "voice_match":
                rewritten.append(("speaker_detect", lambda task_id=task_id: self._step_speaker_detect(task_id)))
                rewritten.append(("voice_match_ab", lambda task_id=task_id: self._step_voice_match_ab(task_id)))
            else:
                rewritten.append((name, fn))
        return rewritten

    def _step_speaker_detect(self, task_id: str) -> None:
        from appcore.dialogue_translate.diarization import DiarizationUnavailable
        from appcore.dialogue_translate.speaker_detection import detect_dialogue_segments

        task = task_state.get(task_id) or {}
        self._set_step(task_id, "speaker_detect", "running", "正在自动识别 Speaker A/B...")
        utterances = task.get("utterances_en") or task.get("utterances") or []
        try:
            result = detect_dialogue_segments(
                utterances=utterances,
                audio_path=task.get("video_path") or "",
                task_id=task_id,
            )
        except DiarizationUnavailable as exc:
            message = str(exc)
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "speaker_detect", "failed", message)
            return
        task_state.update(task_id, **result)
        self._set_step(task_id, "speaker_detect", "done", "Speaker A/B 自动识别完成")

    def _step_voice_match_ab(self, task_id: str) -> None:
        from appcore.dialogue_translate.voice_match import (
            build_speaker_sample_windows,
            match_voices_for_speakers,
        )

        task = task_state.get(task_id) or {}
        dialogue_segments = task.get("dialogue_segments") or []
        if not dialogue_segments:
            message = "dialogue_segments is required before voice_match_ab"
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "voice_match_ab", "failed", message)
            return
        self._set_step(task_id, "voice_match_ab", "running", "正在为 Speaker A/B 匹配音色...")
        sample_specs = build_speaker_sample_windows(dialogue_segments)
        speaker_profiles = match_voices_for_speakers(
            video_path=task.get("video_path") or "",
            task_dir=task.get("task_dir") or "",
            target_lang=task.get("target_lang") or "en",
            dialogue_segments=dialogue_segments,
            sample_specs=sample_specs,
            user_id=self.user_id,
        )
        task_state.update(
            task_id,
            speaker_profiles=speaker_profiles,
            selected_voice_by_speaker=task.get("selected_voice_by_speaker") or {},
        )
        task_state.set_current_review_step(task_id, "voice_match_ab")
        self._set_step(task_id, "voice_match_ab", "waiting", "Speaker A/B 音色候选已就绪，请确认两个音色")

    def _prepare_tts_segments_for_audio_gen(self, task: dict, tts_segments: list[dict]) -> list[dict]:
        from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

        return apply_speaker_voices_to_tts_segments(
            tts_segments,
            task.get("dialogue_segments") or [],
            task.get("selected_voice_by_speaker") or {},
        )

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        selected = task.get("selected_voice_by_speaker") or {}
        for speaker_id in ("A", "B"):
            voice = selected.get(speaker_id) or {}
            voice_id = voice.get("voice_id")
            if voice_id:
                return {
                    "id": None,
                    "elevenlabs_voice_id": voice_id,
                    "name": voice.get("name") or voice_id,
                }
        return super()._resolve_voice(task, loc_mod)
```

- [ ] **Step 5: Register the dialogue runner**

Modify `appcore/runner_dispatch.py` by adding dialogue function types, globals, register/start/resume functions:

```python
DialogueStartFunc = Callable[[str, int | None], object]
DialogueResumeFunc = Callable[[str, str, int | None], object]

_dialogue_translate_start: DialogueStartFunc | None = None
_dialogue_translate_resume: DialogueResumeFunc | None = None


def register_dialogue_translate_runner(
    *,
    start: DialogueStartFunc,
    resume: DialogueResumeFunc | None = None,
) -> None:
    global _dialogue_translate_start, _dialogue_translate_resume
    _dialogue_translate_start = start
    _dialogue_translate_resume = resume


def start_dialogue_translate_runner(task_id: str, user_id: int | None = None) -> object:
    if _dialogue_translate_start is None:
        raise RuntimeError("dialogue_translate runner is not registered")
    return _dialogue_translate_start(task_id, user_id)


def resume_dialogue_translate_runner(
    task_id: str,
    start_step: str,
    user_id: int | None = None,
) -> object:
    if _dialogue_translate_resume is None:
        raise RuntimeError("dialogue_translate resume runner is not registered")
    return _dialogue_translate_resume(task_id, start_step, user_id)
```

Update `clear_runner_registry()` to reset both dialogue globals.

Create `web/services/dialogue_pipeline_runner.py`:

```python
"""DialogueTranslateRunner SocketIO adapter."""
from __future__ import annotations

from appcore.events import EventBus
from appcore import runner_dispatch
from appcore.runner_lifecycle import start_tracked_thread
from web.extensions import socketio


def _handler(task_id: str):
    def fn(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return fn


def _run(runner, task_id: str, start_step: str | None = None):
    if start_step is None:
        runner.start(task_id)
    else:
        runner.resume(task_id, start_step)


def start(task_id: str, user_id: int | None = None) -> bool:
    from appcore.runtime_dialogue import DialogueTranslateRunner

    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = DialogueTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id),
        daemon=False,
    )


def resume(task_id: str, start_step: str, user_id: int | None = None) -> bool:
    from appcore.runtime_dialogue import DialogueTranslateRunner

    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = DialogueTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id, start_step),
        daemon=False,
    )


runner_dispatch.register_dialogue_translate_runner(
    start=lambda task_id, user_id=None: start(task_id, user_id=user_id),
    resume=lambda task_id, start_step, user_id=None: resume(task_id, start_step, user_id=user_id),
)
```

- [ ] **Step 6: Run runtime tests**

Run:

```powershell
pytest tests/test_dialogue_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit runtime integration**

Run:

```powershell
git add appcore/runtime/_pipeline_runner.py appcore/runtime_dialogue.py appcore/runner_dispatch.py web/services/dialogue_pipeline_runner.py tests/test_dialogue_runtime.py
git commit -m "feat: add dialogue translate runtime"
```

## Task 6: Routes, Task Creation, and Voice Confirmation APIs

**Files:**
- Create: `web/routes/dialogue_translate.py`
- Create: `web/templates/dialogue_translate.html`
- Create: `web/templates/dialogue_translate_detail.html`
- Create: `web/static/js/dialogue_translate_detail.js`
- Test: `tests/test_dialogue_translate_routes.py`

- [ ] **Step 1: Write failing route tests**

Add `tests/test_dialogue_translate_routes.py`:

```python
from __future__ import annotations

import io
import json
from unittest.mock import patch


def test_dialogue_page_requires_login(client):
    resp = client.get("/dialogue-translate")
    assert resp.status_code == 302


def test_dialogue_detail_requires_login(client):
    resp = client.get("/dialogue-translate/task-1")
    assert resp.status_code == 302


def test_start_creates_dialogue_task_and_starts_runner(authed_client_no_db, monkeypatch, tmp_path):
    created = {}

    monkeypatch.setattr("web.routes.dialogue_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.dialogue_translate.OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(
        "web.routes.dialogue_translate.store.create",
        lambda task_id, video_path, task_dir, original_filename=None, user_id=None: created.update(
            task_id=task_id,
            video_path=video_path,
            task_dir=task_dir,
            original_filename=original_filename,
            user_id=user_id,
        ),
    )
    monkeypatch.setattr("web.routes.dialogue_translate.store.update", lambda task_id, **kwargs: created.setdefault("updates", []).append(kwargs))
    monkeypatch.setattr("web.routes.dialogue_translate.store.set_preview_file", lambda task_id, key, path: created.update(preview=(task_id, key, path)))
    monkeypatch.setattr("web.routes.dialogue_translate.save_uploaded_video", lambda file, upload_dir, task_id, original_filename: (str(tmp_path / original_filename), 5, "video/mp4"))
    monkeypatch.setattr("web.routes.dialogue_translate.validate_video_extension", lambda filename: True)
    monkeypatch.setattr("web.routes.dialogue_translate.client_filename_basename", lambda filename: filename)
    monkeypatch.setattr("web.routes.dialogue_translate.build_source_object_info", lambda **kwargs: kwargs)
    monkeypatch.setattr("web.services.dialogue_pipeline_runner.start", lambda task_id, user_id=None: created.update(started=(task_id, user_id)) or True)

    resp = authed_client_no_db.post(
        "/api/dialogue-translate/start",
        data={
            "source_language": "en",
            "target_lang": "de",
            "video": (io.BytesIO(b"video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert created["updates"][-1]["type"] == "dialogue_translate"
    assert created["updates"][-1]["target_lang"] == "de"
    assert created["started"][0] == payload["task_id"]


def test_confirm_voices_requires_a_and_b(authed_client_no_db, monkeypatch):
    state = {
        "target_lang": "en",
        "steps": {"voice_match_ab": "waiting"},
        "speaker_profiles": {"A": {}, "B": {}},
    }
    monkeypatch.setattr(
        "web.routes.dialogue_translate._query_viewable_project",
        lambda task_id, columns: {"state_json": json.dumps(state), "user_id": 1},
    )

    resp = authed_client_no_db.post(
        "/api/dialogue-translate/task-1/confirm-voices",
        json={"selected_voice_by_speaker": {"A": {"voice_id": "voice-a", "name": "Voice A"}}},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "voice_id required for speaker B"


def test_confirm_voices_persists_and_resumes_next_step(authed_client_no_db, monkeypatch):
    state = {
        "target_lang": "en",
        "steps": {"voice_match_ab": "waiting", "alignment": "pending"},
        "speaker_profiles": {"A": {}, "B": {}},
    }
    saved = {}
    updated = {}

    monkeypatch.setattr(
        "web.routes.dialogue_translate._query_viewable_project",
        lambda task_id, columns: {"state_json": json.dumps(state), "user_id": 99},
    )
    monkeypatch.setattr("web.routes.dialogue_translate.save_project_state", lambda task_id, payload, execute_func=None: saved.update(payload))
    monkeypatch.setattr("web.routes.dialogue_translate.task_state.update", lambda task_id, **kwargs: updated.update(kwargs))
    monkeypatch.setattr("web.routes.dialogue_translate.task_state.set_step", lambda task_id, step, status: updated.update(step=(step, status)))
    monkeypatch.setattr("web.routes.dialogue_translate.task_state.set_current_review_step", lambda task_id, step: updated.update(review_step=step))
    monkeypatch.setattr("web.services.dialogue_pipeline_runner.resume", lambda task_id, start_step, user_id=None: updated.update(resume=(task_id, start_step, user_id)) or True)

    resp = authed_client_no_db.post(
        "/api/dialogue-translate/task-1/confirm-voices",
        json={
            "selected_voice_by_speaker": {
                "A": {"voice_id": "voice-a", "name": "Voice A"},
                "B": {"voice_id": "voice-b", "name": "Voice B"},
            }
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert resp.status_code == 200
    assert saved["selected_voice_by_speaker"]["A"]["voice_id"] == "voice-a"
    assert updated["step"] == ("voice_match_ab", "done")
    assert updated["resume"] == ("task-1", "alignment", 99)
```

- [ ] **Step 2: Run failing route tests**

Run:

```powershell
pytest tests/test_dialogue_translate_routes.py -q
```

Expected: 404/import failures because the blueprint is not registered yet.

- [ ] **Step 3: Implement the route module**

Create `web/routes/dialogue_translate.py`:

```python
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import task_state, translation_route_store
from appcore.project_state import save_project_state
from appcore.runtime_dialogue import DialogueTranslateRunner
from pipeline.languages.registry import SOURCE_LANGS, SUPPORTED_LANGS, normalize_enabled_target_langs
from web import store
from web.auth import admin_required, permission_required
from web.upload_util import build_source_object_info, client_filename_basename, save_uploaded_video, validate_video_extension
from web.services.translate_route_responses import (
    build_translate_route_payload_response,
    translate_route_flask_response,
)

bp = Blueprint("dialogue_translate", __name__)

db_query = translation_route_store.query
db_query_one = translation_route_store.query_one
db_execute = translation_route_store.execute


def _json_response(payload: dict, status_code: int = 200):
    return translate_route_flask_response(
        build_translate_route_payload_response(payload, status_code)
    )


def _query_viewable_project(task_id: str, columns: str):
    return db_query_one(
        f"SELECT {columns} FROM translation_projects WHERE id = %s AND project_type = %s",
        (task_id, "dialogue_translate"),
    )


def _state_from_row(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    if row.get("user_id") is not None:
        state["_user_id"] = row["user_id"]
    return state


def _enabled_target_langs() -> tuple[str, ...]:
    try:
        from appcore import medias

        return normalize_enabled_target_langs(medias.list_enabled_language_codes())
    except Exception:
        return SUPPORTED_LANGS


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename or "")[0]
    return name[:20] or "未命名对话视频"


def _dialogue_steps() -> dict:
    from appcore.omni_v2_config import current_fixed_plugin_config

    names = DialogueTranslateRunner.pipeline_step_names_for_config(current_fixed_plugin_config())
    return {name: "pending" for name in names}


@bp.route("/dialogue-translate")
@login_required
@admin_required
@permission_required("dialogue_translate")
def index():
    return render_template(
        "dialogue_translate.html",
        source_langs=SOURCE_LANGS,
        target_langs=_enabled_target_langs(),
    )


@bp.route("/dialogue-translate/<task_id>")
@login_required
@admin_required
@permission_required("dialogue_translate")
def detail(task_id: str):
    row = _query_viewable_project(task_id, "id, original_filename, display_name, deleted_at, state_json, user_id")
    if not row:
        abort(404)
    state = _state_from_row(row)
    project = {
        "id": task_id,
        "original_filename": row.get("original_filename") or state.get("original_filename"),
        "display_name": row.get("display_name") or state.get("display_name"),
        "deleted_at": row.get("deleted_at"),
    }
    return render_template(
        "dialogue_translate_detail.html",
        task_id=task_id,
        project=project,
        state=state,
        target_lang=state.get("target_lang") or "en",
        api_base="/api/dialogue-translate",
        dialogue_api_base=f"/api/dialogue-translate/{task_id}",
        pipeline_kind="dialogue_translate",
    )


@bp.route("/api/dialogue-translate/start", methods=["POST"])
@login_required
@admin_required
def start_dialogue_translate():
    upload = request.files.get("video")
    if not upload or not upload.filename:
        return _json_response({"error": "video is required"}, 400)
    source_language = (request.form.get("source_language") or "en").strip()
    target_lang = (request.form.get("target_lang") or "en").strip()
    if source_language not in SOURCE_LANGS:
        return _json_response({"error": "invalid source_language"}, 400)
    if target_lang not in SUPPORTED_LANGS:
        return _json_response({"error": "invalid target_lang"}, 400)

    original_filename = client_filename_basename(upload.filename)
    if not validate_video_extension(original_filename):
        return _json_response({"error": "invalid video extension"}, 400)

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    video_path, file_size, content_type = save_uploaded_video(upload, UPLOAD_DIR, task_id, original_filename)

    now = datetime.utcnow().isoformat()
    store.create(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=current_user.id,
    )
    update_kwargs = {
        "display_name": _default_display_name(original_filename),
        "type": "dialogue_translate",
        "source_language": source_language,
        "user_specified_source_language": True,
        "target_lang": target_lang,
        "steps": _dialogue_steps(),
        "status": "running",
        "created_at": now,
        "dialogue_segments": [],
        "speaker_profiles": {},
        "selected_voice_by_speaker": {},
        "source_object_info": build_source_object_info(
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            storage_backend="local",
            uploaded_at=now,
        ),
    }
    store.update(task_id, **update_kwargs)
    store.set_preview_file(task_id, "source_video", video_path)

    from web.services import dialogue_pipeline_runner

    dialogue_pipeline_runner.start(task_id, user_id=current_user.id)
    return _json_response({"ok": True, "task_id": task_id, "detail_url": f"/dialogue-translate/{task_id}"})


@bp.route("/api/dialogue-translate/<task_id>", methods=["GET"])
@login_required
@admin_required
def get_task(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = _state_from_row(row)
    return _json_response({"ok": True, "task": state})


def _normalize_selected_voice_payload(body: dict) -> dict:
    selected = body.get("selected_voice_by_speaker") or {}
    normalized = {}
    for speaker_id in ("A", "B"):
        voice = selected.get(speaker_id) or {}
        voice_id = str(voice.get("voice_id") or "").strip()
        if not voice_id:
            raise ValueError(f"voice_id required for speaker {speaker_id}")
        normalized[speaker_id] = {
            "voice_id": voice_id,
            "name": str(voice.get("name") or voice.get("voice_name") or voice_id).strip(),
        }
    return normalized


@bp.route("/api/dialogue-translate/<task_id>/confirm-voices", methods=["POST"])
@login_required
@admin_required
def confirm_voices(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    owner_id = row.get("user_id") or current_user.id
    state = _state_from_row(row)
    try:
        selected = _normalize_selected_voice_payload(request.get_json() or {})
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    profiles = dict(state.get("speaker_profiles") or {})
    for speaker_id, voice in selected.items():
        profile = dict(profiles.get(speaker_id) or {})
        profile["selected_voice"] = voice
        profiles[speaker_id] = profile
    state["speaker_profiles"] = profiles
    state["selected_voice_by_speaker"] = selected
    save_project_state(task_id, state, execute_func=db_execute)
    task_state.update(task_id, speaker_profiles=profiles, selected_voice_by_speaker=selected)
    task_state.set_step(task_id, "voice_match_ab", "done")
    task_state.set_current_review_step(task_id, "")

    from web.services import dialogue_pipeline_runner

    dialogue_pipeline_runner.resume(task_id, "alignment", user_id=owner_id)
    return _json_response({"ok": True, "selected_voice_by_speaker": selected})
```

- [ ] **Step 4: Add basic templates and JS**

Create `web/templates/dialogue_translate.html`:

```html
{% extends "layout.html" %}
{% block content %}
<main class="container py-4">
  <h1 class="h4 mb-3">对话式视频翻译</h1>
  <form id="dialogue-create-form" method="post" enctype="multipart/form-data" action="/api/dialogue-translate/start">
    <div class="mb-3">
      <label class="form-label" for="dialogue-video">视频文件</label>
      <input class="form-control" id="dialogue-video" name="video" type="file" accept="video/*" required>
    </div>
    <div class="row g-3">
      <div class="col-md-6">
        <label class="form-label" for="dialogue-source-language">源语言</label>
        <select class="form-select" id="dialogue-source-language" name="source_language">
          {% for code in source_langs %}
          <option value="{{ code }}">{{ code }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="col-md-6">
        <label class="form-label" for="dialogue-target-lang">目标语言</label>
        <select class="form-select" id="dialogue-target-lang" name="target_lang">
          {% for code in target_langs %}
          <option value="{{ code }}">{{ code }}</option>
          {% endfor %}
        </select>
      </div>
    </div>
    <button class="btn btn-primary mt-3" type="submit">创建任务</button>
  </form>
</main>
<script>
document.getElementById("dialogue-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const response = await fetch(form.action, {
    method: "POST",
    body: new FormData(form),
    headers: {"X-Requested-With": "XMLHttpRequest"}
  });
  const payload = await response.json();
  if (payload.detail_url) {
    window.location.href = payload.detail_url;
  } else {
    alert(payload.error || "创建失败");
  }
});
</script>
{% endblock %}
```

Create `web/templates/dialogue_translate_detail.html`:

```html
{% extends "_translate_detail_shell.html" %}
{% set detail_mode = 'multi' %}
{% set pipeline_kind = 'dialogue_translate' %}
{% set api_base = '/api/dialogue-translate' %}
{% set url_for_detail = '/dialogue-translate/__TASK_ID__' %}
{% set voice_language = target_lang %}
{% set default_source_language = state.source_language or 'en' %}
{% block detail_extra %}
{% include "_separation_card.html" %}
<section id="dialogue-speaker-panel" class="translate-detail-panel" data-api-base="{{ dialogue_api_base }}">
  <h2 class="h5">说话人音色</h2>
  <div id="dialogue-speakers"></div>
  <button id="dialogue-confirm-voices" class="btn btn-primary" type="button" disabled>确认 A/B 音色</button>
</section>
<script src="{{ url_for('static', filename='js/dialogue_translate_detail.js') }}"></script>
{% endblock %}
```

Create `web/static/js/dialogue_translate_detail.js`:

```javascript
(function () {
  const panel = document.getElementById("dialogue-speaker-panel");
  if (!panel) return;
  const apiBase = panel.dataset.apiBase;
  const speakersEl = document.getElementById("dialogue-speakers");
  const confirmButton = document.getElementById("dialogue-confirm-voices");
  const selected = {};

  function renderSpeakerCard(id, profile) {
    const card = document.createElement("div");
    card.className = "speaker-card";
    const title = document.createElement("h3");
    title.className = "h6";
    title.textContent = `Speaker ${id}`;
    card.appendChild(title);
    const sample = profile.sample_path ? document.createElement("audio") : null;
    if (sample) {
      sample.controls = true;
      sample.src = `${apiBase}/artifact-path?path=${encodeURIComponent(profile.sample_path)}`;
      card.appendChild(sample);
    }
    const select = document.createElement("select");
    select.className = "form-select";
    select.dataset.speakerId = id;
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "选择音色";
    select.appendChild(empty);
    (profile.candidates || []).forEach((candidate) => {
      const option = document.createElement("option");
      option.value = candidate.voice_id;
      option.textContent = candidate.name || candidate.voice_id;
      option.dataset.name = candidate.name || candidate.voice_id;
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      const option = select.selectedOptions[0];
      selected[id] = option && option.value ? {voice_id: option.value, name: option.dataset.name || option.value} : null;
      confirmButton.disabled = !(selected.A && selected.B);
    });
    card.appendChild(select);
    return card;
  }

  async function refresh() {
    const response = await fetch(apiBase, {headers: {"X-Requested-With": "XMLHttpRequest"}});
    const payload = await response.json();
    const task = payload.task || {};
    speakersEl.innerHTML = "";
    ["A", "B"].forEach((id) => {
      speakersEl.appendChild(renderSpeakerCard(id, (task.speaker_profiles || {})[id] || {}));
    });
  }

  confirmButton.addEventListener("click", async () => {
    const response = await fetch(`${apiBase}/confirm-voices`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
      body: JSON.stringify({selected_voice_by_speaker: selected})
    });
    const payload = await response.json();
    if (!response.ok) {
      alert(payload.error || "确认失败");
      return;
    }
    confirmButton.disabled = true;
    await refresh();
  });

  refresh();
})();
```

- [ ] **Step 5: Run route tests and continue to registration**

Run:

```powershell
pytest tests/test_dialogue_translate_routes.py -q
```

Expected: the route module tests that import helpers pass, and app-level requests may still return 404 until Task 7 registers the blueprint. Do not commit this task yet; keep these files staged only after Task 7 passes the combined route and permission suite.

- [ ] **Step 6: Carry route files into Task 7**

Continue directly to Task 7 so the route tests are committed only after blueprint registration, permissions, and CSRF guard are complete.

## Task 7: App Registration, Permissions, and CSRF Guard

**Files:**
- Modify: `web/app.py`
- Modify: `appcore/permissions.py`
- Test: `tests/test_dialogue_permissions.py`
- Test: `tests/test_dialogue_translate_routes.py`

- [ ] **Step 1: Write permission tests**

Add `tests/test_dialogue_permissions.py`:

```python
from __future__ import annotations


def test_dialogue_permission_is_registered():
    from appcore.permissions import PERMISSION_CODES, PERMISSION_META

    assert "dialogue_translate" in PERMISSION_CODES
    assert PERMISSION_META["dialogue_translate"]["label"] == "对话式视频翻译"


def test_translator_role_gets_dialogue_translate_permission():
    from appcore.permissions import ROLE_TRANSLATOR, default_permissions_for_role

    permissions = default_permissions_for_role(ROLE_TRANSLATOR)

    assert permissions["dialogue_translate"] is True
```

- [ ] **Step 2: Run failing permission and route registration tests**

Run:

```powershell
pytest tests/test_dialogue_permissions.py tests/test_dialogue_translate_routes.py -q
```

Expected: permission test fails and route tests still see 404 until registration is complete.

- [ ] **Step 3: Register permissions**

Modify `appcore/permissions.py`:

```python
    ("dialogue_translate",    GROUP_BUSINESS,   "对话式视频翻译",     True,  True),
```

Place it near `omni_translate_v2`.

Add redirect order entry:

```python
    ("dialogue_translate", "/dialogue-translate"),
```

Add translator default:

```python
    "dialogue_translate",
```

- [ ] **Step 4: Register the blueprint and CSRF behavior**

Modify imports in `web/app.py`:

```python
from web.routes.dialogue_translate import bp as dialogue_translate_bp
```

Add `"dialogue_translate"` to `_COOKIE_API_CSRF_GUARDED_BLUEPRINTS` so unsafe cookie-auth API methods require `X-CSRFToken` unless the request is AJAX-test compatible.

Register and exempt the blueprint next to Omni V2:

```python
    csrf.exempt(dialogue_translate_bp)
    app.register_blueprint(dialogue_translate_bp)
```

Import `web.services.dialogue_pipeline_runner` in app startup if existing runner adapters are imported for side-effect registration. Use the same location as `omni_v2_pipeline_runner` side-effect imports.

- [ ] **Step 5: Run registration tests**

Run:

```powershell
pytest tests/test_dialogue_permissions.py tests/test_dialogue_translate_routes.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit registration**

Run:

```powershell
git add web/app.py appcore/permissions.py web/routes/dialogue_translate.py web/templates/dialogue_translate.html web/templates/dialogue_translate_detail.html web/static/js/dialogue_translate_detail.js tests/test_dialogue_permissions.py tests/test_dialogue_translate_routes.py
git commit -m "feat: add dialogue translate web module"
```

## Task 8: Speaker Review Edits and Downstream Reset

**Files:**
- Modify: `web/routes/dialogue_translate.py`
- Test: `tests/test_dialogue_translate_routes.py`

- [ ] **Step 1: Add failing test for sentence-level correction**

Append to `tests/test_dialogue_translate_routes.py`:

```python
def test_update_speaker_segment_resets_voice_and_downstream_steps(authed_client_no_db, monkeypatch):
    state = {
        "dialogue_segments": [
            {"index": 0, "speaker_id": "A", "review_required": False, "review_reason": ""},
        ],
        "steps": {
            "speaker_detect": "done",
            "voice_match_ab": "done",
            "alignment": "done",
            "translate": "done",
            "tts": "done",
            "subtitle": "done",
            "compose": "done",
            "export": "done",
        },
        "speaker_profiles": {"A": {"selected_voice": {"voice_id": "voice-a"}}, "B": {}},
        "selected_voice_by_speaker": {"A": {"voice_id": "voice-a"}, "B": {"voice_id": "voice-b"}},
    }
    saved = {}
    step_changes = []

    monkeypatch.setattr(
        "web.routes.dialogue_translate._query_viewable_project",
        lambda task_id, columns: {"state_json": json.dumps(state), "user_id": 1},
    )
    monkeypatch.setattr("web.routes.dialogue_translate.save_project_state", lambda task_id, payload, execute_func=None: saved.update(payload))
    monkeypatch.setattr("web.routes.dialogue_translate.task_state.update", lambda task_id, **kwargs: saved.update(kwargs))
    monkeypatch.setattr("web.routes.dialogue_translate.task_state.set_step", lambda task_id, step, status: step_changes.append((step, status)))

    resp = authed_client_no_db.put(
        "/api/dialogue-translate/task-1/speaker-segments/0",
        json={"speaker_id": "B"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert resp.status_code == 200
    assert saved["dialogue_segments"][0]["speaker_id"] == "B"
    assert saved["selected_voice_by_speaker"] == {}
    assert ("voice_match_ab", "pending") in step_changes
    assert ("tts", "pending") in step_changes
```

- [ ] **Step 2: Run the failing correction test**

Run:

```powershell
pytest tests/test_dialogue_translate_routes.py::test_update_speaker_segment_resets_voice_and_downstream_steps -q
```

Expected: 404 for the new endpoint.

- [ ] **Step 3: Implement speaker correction endpoint**

Add to `web/routes/dialogue_translate.py`:

```python
def _reset_steps_from(task_id: str, state: dict, start_step: str) -> None:
    names = list((state.get("steps") or {}).keys())
    started = False
    for name in names:
        if name == start_step:
            started = True
        if started:
            task_state.set_step(task_id, name, "pending")


@bp.route("/api/dialogue-translate/<task_id>/speaker-segments/<int:index>", methods=["PUT"])
@login_required
@admin_required
def update_speaker_segment(task_id: str, index: int):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = _state_from_row(row)
    speaker_id = str((request.get_json() or {}).get("speaker_id") or "").strip().upper()
    if speaker_id not in {"A", "B"}:
        return _json_response({"error": "speaker_id must be A or B"}, 400)
    segments = [dict(segment) for segment in (state.get("dialogue_segments") or [])]
    target = next((segment for segment in segments if int(segment.get("index", -1)) == index), None)
    if target is None:
        abort(404)
    target["speaker_id"] = speaker_id
    target["review_required"] = True
    target["review_reason"] = "manual_speaker_changed"
    state["dialogue_segments"] = segments
    state["speaker_profiles"] = {}
    state["selected_voice_by_speaker"] = {}
    save_project_state(task_id, state, execute_func=db_execute)
    task_state.update(
        task_id,
        dialogue_segments=segments,
        speaker_profiles={},
        selected_voice_by_speaker={},
    )
    _reset_steps_from(task_id, state, "voice_match_ab")
    return _json_response({"ok": True, "dialogue_segments": segments})
```

- [ ] **Step 4: Run correction route tests**

Run:

```powershell
pytest tests/test_dialogue_translate_routes.py -q
```

Expected: all route tests pass.

- [ ] **Step 5: Commit correction endpoint**

Run:

```powershell
git add web/routes/dialogue_translate.py tests/test_dialogue_translate_routes.py
git commit -m "feat: support dialogue speaker corrections"
```

## Task 9: Subtitle Contract and Detail Payload

**Files:**
- Modify: `web/routes/dialogue_translate.py`
- Test: `tests/test_dialogue_subtitles.py`

- [ ] **Step 1: Write subtitle contract tests**

Add `tests/test_dialogue_subtitles.py`:

```python
from __future__ import annotations

from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments


def test_dialogue_tts_metadata_keeps_speaker_without_subtitle_prefix():
    tts_segments = [
        {"index": 0, "tts_text": "Hallo", "subtitle_text": "Hallo"},
        {"index": 1, "tts_text": "Ja", "subtitle_text": "Ja"},
    ]
    dialogue_segments = [
        {"index": 0, "speaker_id": "A"},
        {"index": 1, "speaker_id": "B"},
    ]
    selected = {
        "A": {"voice_id": "voice-a", "name": "Voice A"},
        "B": {"voice_id": "voice-b", "name": "Voice B"},
    }

    result = apply_speaker_voices_to_tts_segments(tts_segments, dialogue_segments, selected)

    assert result[0]["speaker_id"] == "A"
    assert result[1]["speaker_id"] == "B"
    assert result[0]["subtitle_text"] == "Hallo"
    assert not result[0]["subtitle_text"].startswith("A:")
    assert not result[1]["subtitle_text"].startswith("Speaker B:")
```

- [ ] **Step 2: Run subtitle contract test**

Run:

```powershell
pytest tests/test_dialogue_subtitles.py -q
```

Expected: pass if Task 4 mapping helper is correct.

- [ ] **Step 3: Ensure the task GET payload exposes backend speaker metadata**

In `web/routes/dialogue_translate.py`, keep `get_task()` returning the full stored state. Confirm the payload includes:

```python
{
    "dialogue_segments": state.get("dialogue_segments") or [],
    "speaker_profiles": state.get("speaker_profiles") or {},
    "selected_voice_by_speaker": state.get("selected_voice_by_speaker") or {},
    "review_required_segments": state.get("review_required_segments") or [],
    "dialogue_warnings": state.get("dialogue_warnings") or [],
}
```

If `get_task()` currently returns full state, add a test assertion instead of duplicating fields.

- [ ] **Step 4: Run subtitle and route payload tests**

Run:

```powershell
pytest tests/test_dialogue_subtitles.py tests/test_dialogue_translate_routes.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit subtitle contract**

Run:

```powershell
git add appcore/dialogue_translate/tts.py web/routes/dialogue_translate.py tests/test_dialogue_subtitles.py tests/test_dialogue_translate_routes.py
git commit -m "test: lock dialogue subtitle speaker contract"
```

## Task 10: Focused Regression Suite and Browser Smoke

**Files:**
- No source files unless a test failure requires a targeted fix.

- [ ] **Step 1: Run focused unit and route tests**

Run:

```powershell
pytest tests/test_dialogue_speaker_detection.py tests/test_dialogue_diarization.py tests/test_dialogue_voice_match.py tests/test_dialogue_tts.py tests/test_dialogue_runtime.py tests/test_dialogue_translate_routes.py tests/test_dialogue_permissions.py tests/test_dialogue_subtitles.py -q
```

Expected: all pass.

- [ ] **Step 2: Run existing Omni/V2 route regressions**

Run:

```powershell
pytest tests/test_omni_translate_routes.py -q
```

Expected: pass. If the command attempts Windows local MySQL `127.0.0.1:3306`, stop and report that project rules prohibit local MySQL verification.

- [ ] **Step 3: Check formatting whitespace**

Run:

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Start a local dev server on an idle port**

Run:

```powershell
$env:FLASK_ENV='development'
$env:WTF_CSRF_ENABLED='0'
python -m web.app
```

Use an idle port if the default is occupied. Do not restart systemd services.

- [ ] **Step 5: Browser smoke the new pages**

Use the Browser plugin to open the local server:

```text
http://127.0.0.1:<port>/dialogue-translate
```

Expected:
- Logged-out access redirects to login or shows the existing auth flow.
- After login with the documented test account, `/dialogue-translate` returns 200.
- The create form is visible.
- A seeded or mocked detail task shows the Speaker A/B panel.
- The final subtitle UI does not show speaker prefixes as part of subtitle text.

- [ ] **Step 6: Commit final fixes**

If Task 10 required source or test edits, run:

```powershell
git add <changed-files>
git commit -m "fix: stabilize dialogue translate verification"
```

Do not create an empty commit when Task 10 only verifies previous commits.

## Execution Notes

- Run all commands from `G:/Code/AutoVideoSrtLocal/.worktrees/dialogue-video-translate-design`.
- Keep commits small and in the order shown above.
- Avoid broad refactors in `appcore/runtime/_pipeline_runner.py`; the only shared-runner change should be the no-op TTS segment preparation hook and its call site.
- Preserve the existing `synthesize_full(segments, voice_id, output_dir, ...)` signature for all existing engines.
- Dialogue-specific state keys are:
  - `dialogue_segments`
  - `speaker_summary`
  - `speaker_profiles`
  - `selected_voice_by_speaker`
  - `review_required_segments`
  - `dialogue_warnings`
- Review reasons are:
  - `low_speaker_confidence`
  - `speaker_overlap`
  - `unsupported_extra_speaker`
  - `insufficient_speaker_sample`
  - `tts_overflow_window`
  - `manual_speaker_changed`
- The confirmation API resumes from `alignment`, not directly from `translate`, because Dialogue mode must keep Omni's alignment-before-translation pipeline shape.
