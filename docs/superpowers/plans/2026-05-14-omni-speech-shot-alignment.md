# Omni Speech-Shot Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic post-TTS, pre-compose `语音镜头对齐` optimizer/card for Omni sentence-level tasks without changing TTS convergence or stacking silence gaps.

**Architecture:** Add a focused `pipeline/speech_shot_alignment.py` module that takes the existing compact sentence schedule and resolves one final inter-sentence gap per boundary using shot-cut anchors. Call it from `SentenceReconcileStrategy` immediately after `apply_compact_audio_schedule()` and before `_build_av_tts_segments()` / `_rebuild_tts_full_audio_from_segments()`. Surface the summary through task state and a new Omni-only workbench card; restart clears all fields.

**Tech Stack:** Python 3.12, Flask task state, existing `pipeline.audio_stitch.apply_compact_audio_schedule`, Jinja templates, vanilla JS, pytest.

---

## File Structure

- Create `pipeline/speech_shot_alignment.py`: deterministic cut normalization, gap resolution, sentence shifting, and summary building.
- Modify `appcore/tts_strategies/sentence_reconcile.py`: call the optimizer after TTS sentence convergence and compact scheduling, then persist summary.
- Modify `web/services/task_restart.py`: reset top-level speech-shot alignment and final compose summary fields during force restart.
- Modify `web/templates/_task_workbench.html`: add an Omni-only `语音镜头对齐` card after TTS and before subtitle/compose.
- Modify `web/templates/_task_workbench_scripts.html`: render card state from `currentTask.speech_shot_alignment` / `final_compose_summary`.
- Modify `web/templates/_task_workbench_styles.html`: add compact table/metric styles for the card.
- Test `tests/test_speech_shot_alignment.py`: pure optimizer behavior.
- Modify `tests/test_sentence_translate_runtime.py`: runtime integration and summary persistence.
- Modify `tests/test_task_restart.py`: restart clears stale card outputs.
- Modify `tests/test_translate_detail_shell_templates.py`: template/script/style card hooks.

## Task 1: Pure Optimizer Tests

**Files:**
- Create: `tests/test_speech_shot_alignment.py`
- Create in Task 2: `pipeline/speech_shot_alignment.py`

- [ ] **Step 1: Write failing optimizer tests**

Create `tests/test_speech_shot_alignment.py`:

```python
from __future__ import annotations

import inspect

import pytest

from pipeline import speech_shot_alignment


def _scheduled_sentences():
    return [
        {
            "asr_index": 0,
            "audio_start_time": 0.0,
            "audio_end_time": 2.0,
            "audio_gap_before": 0.0,
            "tts_duration": 2.0,
            "source_gap_before": 0.0,
            "text": "first",
        },
        {
            "asr_index": 1,
            "audio_start_time": 2.2,
            "audio_end_time": 4.2,
            "audio_gap_before": 0.2,
            "tts_duration": 2.0,
            "source_gap_before": 0.2,
            "text": "second",
        },
        {
            "asr_index": 2,
            "audio_start_time": 4.45,
            "audio_end_time": 5.45,
            "audio_gap_before": 0.25,
            "tts_duration": 1.0,
            "source_gap_before": 3.0,
            "text": "third",
        },
    ]


def test_aligns_nearby_cut_by_resolving_one_final_gap():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[
            {"start": 0.0, "end": 2.28},
            {"start": 2.28, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_gap_before"] == pytest.approx(0.28)
    assert sentences[1]["audio_start_time"] == pytest.approx(2.28)
    assert sentences[1]["audio_end_time"] == pytest.approx(4.28)
    assert sentences[2]["audio_start_time"] == pytest.approx(4.53)
    assert sentences[1]["base_compact_gap"] == pytest.approx(0.2)
    assert sentences[1]["shot_anchor_final_gap"] == pytest.approx(0.28)
    assert sentences[1]["shot_anchor_extra_silence"] == pytest.approx(0.08)
    assert summary["speech_shot_alignment_status"] == "optimized"
    assert summary["shot_anchor_extra_silence_total"] == pytest.approx(0.08)
    assert summary["shot_anchor_aligned_boundary_count"] == 1


def test_does_not_stack_gap_when_final_gap_would_exceed_cap():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[
            {"start": 0.0, "end": 4.54},
            {"start": 4.54, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[2]["audio_gap_before"] == pytest.approx(0.25)
    assert "shot_anchor_extra_silence" not in sentences[2]
    assert summary["speech_shot_alignment_status"] == "no_op"
    assert summary["shot_anchor_skip_reasons"]["would_exceed_final_gap_cap"] >= 1


def test_hook_protection_skips_large_early_shift():
    base = _scheduled_sentences()
    base[1]["audio_gap_before"] = 0.0
    base[1]["audio_start_time"] = 2.0
    base[1]["audio_end_time"] = 4.0
    base[2]["audio_start_time"] = 4.25
    base[2]["audio_end_time"] = 5.0
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        base,
        shots=[
            {"start": 0.0, "end": 2.14},
            {"start": 2.14, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_start_time"] == pytest.approx(2.0)
    assert summary["shot_anchor_skip_reasons"]["hook_protection"] >= 1


def test_no_anchors_records_skipped_state():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_gap_before"] == pytest.approx(0.2)
    assert summary["speech_shot_alignment_status"] == "skipped_no_anchors"
    assert summary["speech_shot_alignment_analyzed_boundaries"] == 2
    assert summary["shot_anchor_cut_count"] == 0


def test_optimizer_does_not_import_llm_clients():
    source = inspect.getsource(speech_shot_alignment)
    assert "llm" not in source.lower()
    assert "openrouter" not in source.lower()
    assert "gemini" not in source.lower()
```

- [ ] **Step 2: Run tests to confirm missing module failure**

Run:

```bash
pytest tests/test_speech_shot_alignment.py -q
```

Expected: collection fails with `ImportError` for `pipeline.speech_shot_alignment`.

## Task 2: Optimizer Implementation

**Files:**
- Create: `pipeline/speech_shot_alignment.py`
- Test: `tests/test_speech_shot_alignment.py`

- [ ] **Step 1: Implement deterministic optimizer**

Create `pipeline/speech_shot_alignment.py`:

```python
"""Deterministic post-TTS speech/shot alignment.

Docs-anchor: docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md
"""
from __future__ import annotations

from collections import Counter
from typing import Any


DEFAULT_HARD_FINAL_GAP = 0.30
DEFAULT_HOOK_SECONDS = 3.0
DEFAULT_HOOK_EXTRA_CAP = 0.12
_DEDUP_TOLERANCE = 0.05


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float) -> float:
    return round(float(value), 3)


def normalize_shot_cut_anchors(
    *,
    shots: list[dict] | None,
    scene_cuts: list[Any] | None,
    video_duration: float | None,
) -> list[float]:
    duration = _float_value(video_duration, 0.0)
    raw: list[float] = []
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        for key in ("start", "end"):
            value = _float_value(shot.get(key), 0.0)
            if value > 0:
                raw.append(value)
    for cut in scene_cuts or []:
        if isinstance(cut, dict):
            value = _float_value(cut.get("time", cut.get("at", cut.get("start"))), 0.0)
        else:
            value = _float_value(cut, 0.0)
        if value > 0:
            raw.append(value)

    out: list[float] = []
    upper = duration - 0.2 if duration > 0 else None
    for value in sorted(raw):
        if value <= 0.2:
            continue
        if upper is not None and value >= upper:
            continue
        if out and abs(value - out[-1]) <= _DEDUP_TOLERANCE:
            continue
        out.append(_round(value))
    return out


def _content_end(sentences: list[dict]) -> float:
    return max(
        (
            _float_value(sentence.get("audio_end_time"), 0.0)
            for sentence in sentences
            if isinstance(sentence, dict)
        ),
        default=0.0,
    )


def _shift_following(sentences: list[dict], start_index: int, delta: float) -> None:
    for index in range(start_index, len(sentences)):
        sentence = sentences[index]
        sentence["audio_start_time"] = _round(_float_value(sentence.get("audio_start_time"), 0.0) + delta)
        sentence["audio_end_time"] = _round(_float_value(sentence.get("audio_end_time"), 0.0) + delta)


def _base_summary(
    *,
    status: str,
    enabled: bool,
    anchors: list[float],
    boundary_count: int,
    budget: float,
    decisions: list[dict] | None = None,
    skip_reasons: Counter | None = None,
) -> dict:
    decisions = decisions or []
    applied = [row for row in decisions if row.get("decision") == "applied"]
    extra_total = sum(_float_value(row.get("extra_silence"), 0.0) for row in applied)
    return {
        "speech_shot_alignment_enabled": bool(enabled),
        "speech_shot_alignment_applied": bool(applied),
        "speech_shot_alignment_status": status,
        "speech_shot_alignment_analyzed_boundaries": int(boundary_count),
        "speech_shot_alignment_decisions": decisions,
        "shot_anchor_cut_count": len(anchors),
        "shot_anchor_extra_silence_total": _round(extra_total),
        "shot_anchor_aligned_boundary_count": len(applied),
        "shot_anchor_extra_silence_budget": _round(budget),
        "shot_anchor_skip_reasons": dict(skip_reasons or {}),
    }


def apply_speech_shot_alignment(
    sentences: list[dict],
    *,
    shots: list[dict] | None,
    scene_cuts: list[Any] | None,
    video_duration: float | None,
    hard_final_gap_cap: float = DEFAULT_HARD_FINAL_GAP,
    hook_seconds: float = DEFAULT_HOOK_SECONDS,
    hook_extra_cap: float = DEFAULT_HOOK_EXTRA_CAP,
) -> tuple[list[dict], dict]:
    scheduled = [dict(sentence) for sentence in sentences or [] if isinstance(sentence, dict)]
    boundary_count = max(0, len(scheduled) - 1)
    duration = _float_value(video_duration, 0.0)
    extra_budget = min(1.5, duration * 0.05) if duration > 0 else 1.5
    if duration > 0 and duration < 20.0:
        extra_budget = min(extra_budget, 1.0)
    anchors = normalize_shot_cut_anchors(
        shots=shots,
        scene_cuts=scene_cuts,
        video_duration=duration,
    )
    if not scheduled:
        return scheduled, _base_summary(
            status="skipped_no_sentences",
            enabled=False,
            anchors=anchors,
            boundary_count=0,
            budget=extra_budget,
        )
    if duration > 0 and _content_end(scheduled) > duration + 0.001:
        return scheduled, _base_summary(
            status="skipped_content_over_video",
            enabled=False,
            anchors=anchors,
            boundary_count=boundary_count,
            budget=extra_budget,
            skip_reasons=Counter({"final_speech_exceeds_video": 1}),
        )
    if not anchors:
        return scheduled, _base_summary(
            status="skipped_no_anchors",
            enabled=False,
            anchors=[],
            boundary_count=boundary_count,
            budget=extra_budget,
        )

    decisions: list[dict] = []
    skip_reasons: Counter = Counter()
    used_extra = 0.0

    for index in range(1, len(scheduled)):
        prev = scheduled[index - 1]
        sentence = scheduled[index]
        prev_end = _float_value(prev.get("audio_end_time"), 0.0)
        current_start = _float_value(sentence.get("audio_start_time"), 0.0)
        base_gap = max(0.0, _float_value(sentence.get("audio_gap_before"), 0.0))
        candidates = [
            cut for cut in anchors
            if current_start < cut <= current_start + hard_final_gap_cap
        ]
        if not candidates:
            skip_reasons["too_far_from_cut"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "too_far_from_cut",
                "base_compact_gap": _round(base_gap),
                "final_gap": _round(base_gap),
            })
            continue
        cut = min(candidates, key=lambda value: (value - current_start, abs(value - current_start)))
        target_gap = max(0.0, cut - prev_end)
        extra = target_gap - base_gap
        if extra <= 0.001:
            skip_reasons["no_extra_needed"] += 1
            continue
        if target_gap > hard_final_gap_cap + 0.0005:
            skip_reasons["would_exceed_final_gap_cap"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "would_exceed_final_gap_cap",
                "cut_time": _round(cut),
                "base_compact_gap": _round(base_gap),
                "required_final_gap": _round(target_gap),
            })
            continue
        if current_start < hook_seconds and extra > hook_extra_cap + 0.0005:
            skip_reasons["hook_protection"] += 1
            decisions.append({
                "sentence_index": index,
                "asr_index": sentence.get("asr_index", sentence.get("index", index)),
                "decision": "skipped",
                "reason": "hook_protection",
                "cut_time": _round(cut),
                "base_compact_gap": _round(base_gap),
                "required_final_gap": _round(target_gap),
                "extra_silence": _round(extra),
            })
            continue
        if used_extra + extra > extra_budget + 0.0005:
            skip_reasons["over_budget"] += 1
            continue
        if duration > 0 and _content_end(scheduled) + extra > duration + 0.001:
            skip_reasons["would_overrun_video"] += 1
            continue

        before_start = current_start
        sentence["base_compact_gap"] = _round(base_gap)
        sentence["audio_gap_before"] = _round(target_gap)
        sentence["shot_anchor_final_gap"] = _round(target_gap)
        sentence["shot_anchor_extra_silence"] = _round(extra)
        sentence["shot_anchor_cut_time"] = _round(cut)
        sentence["shot_anchor_before_start"] = _round(before_start)
        sentence["shot_anchor_after_start"] = _round(cut)
        sentence["shot_anchor_reason"] = "nearby_cut_soft_snap"
        _shift_following(scheduled, index, extra)
        used_extra += extra
        decisions.append({
            "sentence_index": index,
            "asr_index": sentence.get("asr_index", sentence.get("index", index)),
            "decision": "applied",
            "reason": "nearby_cut_soft_snap",
            "cut_time": _round(cut),
            "base_compact_gap": _round(base_gap),
            "final_gap": _round(target_gap),
            "extra_silence": _round(extra),
            "before_start": _round(before_start),
            "after_start": _round(cut),
        })

    status = "optimized" if any(row.get("decision") == "applied" for row in decisions) else "no_op"
    summary = _base_summary(
        status=status,
        enabled=True,
        anchors=anchors,
        boundary_count=boundary_count,
        budget=extra_budget,
        decisions=decisions,
        skip_reasons=skip_reasons,
    )
    summary["speech_shot_alignment_max_final_gap"] = _round(
        max((_float_value(row.get("audio_gap_before"), 0.0) for row in scheduled), default=0.0)
    )
    return scheduled, summary
```

- [ ] **Step 2: Run pure optimizer tests**

Run:

```bash
pytest tests/test_speech_shot_alignment.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit pure optimizer**

Run:

```bash
git add pipeline/speech_shot_alignment.py tests/test_speech_shot_alignment.py
git commit -m "feat(omni): add deterministic speech shot alignment optimizer" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md"
```

## Task 3: Runtime Integration

**Files:**
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Modify: `tests/test_sentence_translate_runtime.py`

- [ ] **Step 1: Write failing runtime integration test**

Append to `tests/test_sentence_translate_runtime.py`:

```python
def test_omni_tts_step_applies_speech_shot_alignment_before_rebuild(tmp_path, monkeypatch):
    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")

    task_id = _make_task(
        tmp_path,
        type="omni_translate",
        plugin_config={
            "shot_decompose": True,
            "translate_algo": "shot_char_limit",
            "tts_strategy": "sentence_reconcile",
            "subtitle": "sentence_units",
        },
        video_duration=6.0,
        shots=[
            {"index": 1, "start": 0.0, "end": 2.28, "description": "a"},
            {"index": 2, "start": 2.28, "end": 6.0, "description": "b"},
        ],
    )

    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.apply_compact_audio_schedule",
        lambda sentences, max_gap: [
            {
                **sentences[0],
                "audio_start_time": 0.0,
                "audio_end_time": 2.0,
                "audio_gap_before": 0.0,
                "tts_duration": 2.0,
            },
            {
                **sentences[1],
                "audio_start_time": 2.2,
                "audio_end_time": 4.2,
                "audio_gap_before": 0.2,
                "tts_duration": 2.0,
            },
        ],
    )

    rebuilt_segments = []

    def fake_rebuild(task_dir, segments, variant="av"):
        rebuilt_segments.extend([dict(segment) for segment in segments])
        return str(final_audio)

    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        fake_rebuild,
    )

    _runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    summary = saved["speech_shot_alignment"]
    assert summary["speech_shot_alignment_status"] == "optimized"
    assert summary["shot_anchor_extra_silence_total"] == pytest.approx(0.08)
    assert saved["final_compose_summary"]["speech_shot_alignment_status"] == "optimized"
    assert rebuilt_segments[1]["audio_gap_before"] == pytest.approx(0.28)
    assert rebuilt_segments[1]["audio_start_time"] == pytest.approx(2.28)
```

- [ ] **Step 2: Run runtime test to confirm failure**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_omni_tts_step_applies_speech_shot_alignment_before_rebuild -q
```

Expected: fails because speech-shot alignment is not imported/called.

- [ ] **Step 3: Integrate optimizer after compact scheduling**

Modify `appcore/tts_strategies/sentence_reconcile.py`:

```python
from pipeline.audio_stitch import apply_compact_audio_schedule
from pipeline.speech_shot_alignment import apply_speech_shot_alignment


def _should_run_speech_shot_alignment(task: dict) -> bool:
    cfg = task.get("plugin_config") if isinstance(task.get("plugin_config"), dict) else {}
    return (
        task.get("type") == "omni_translate"
        and bool(cfg.get("shot_decompose"))
        and cfg.get("tts_strategy") == "sentence_reconcile"
    )
```

Inside `SentenceReconcileStrategy.run`, replace the local import/call block:

```python
from pipeline.audio_stitch import apply_compact_audio_schedule
final_sentences = apply_compact_audio_schedule(final_sentences, max_gap=0.25)
```

with:

```python
final_sentences = apply_compact_audio_schedule(final_sentences, max_gap=0.25)
alignment_summary = {
    "speech_shot_alignment_enabled": False,
    "speech_shot_alignment_applied": False,
    "speech_shot_alignment_status": "skipped_not_omni_sentence_reconcile",
    "speech_shot_alignment_analyzed_boundaries": max(0, len(final_sentences) - 1),
    "speech_shot_alignment_decisions": [],
    "shot_anchor_cut_count": 0,
    "shot_anchor_extra_silence_total": 0.0,
    "shot_anchor_aligned_boundary_count": 0,
    "shot_anchor_extra_silence_budget": 0.0,
    "shot_anchor_skip_reasons": {},
}
task_for_alignment = task_state.get(task_id) or task
if _should_run_speech_shot_alignment(task_for_alignment):
    final_sentences, alignment_summary = apply_speech_shot_alignment(
        final_sentences,
        shots=list(task_for_alignment.get("shots") or []),
        scene_cuts=list(task_for_alignment.get("scene_cuts") or []),
        video_duration=task_for_alignment.get("video_duration"),
    )
```

After the `_build_final_compose_summary` call, merge:

```python
final_compose_summary.update(alignment_summary)
if alignment_summary.get("speech_shot_alignment_applied"):
    final_compose_summary.setdefault("notes", []).append(
        "语音镜头对齐：优化 "
        f"{alignment_summary.get('shot_anchor_aligned_boundary_count', 0)} 个断点，"
        f"额外静音 {alignment_summary.get('shot_anchor_extra_silence_total', 0):.2f}s。"
    )
```

Persist `alignment_summary` in both the `variant_state.update` payload and the `task_state.update` payload:

```python
"speech_shot_alignment": alignment_summary,
```

- [ ] **Step 4: Run runtime tests**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_omni_tts_step_applies_speech_shot_alignment_before_rebuild tests/test_sentence_translate_runtime.py::test_final_compose_summary_spells_out_tail_padding_without_truncation -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit runtime integration**

Run:

```bash
git add appcore/tts_strategies/sentence_reconcile.py tests/test_sentence_translate_runtime.py
git commit -m "feat(omni): apply speech shot alignment after tts convergence" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md"
```

## Task 4: Force Restart Cleanup

**Files:**
- Modify: `web/services/task_restart.py`
- Modify: `tests/test_task_restart.py`

- [ ] **Step 1: Write failing restart cleanup test**

Append to `tests/test_task_restart.py`:

```python
def test_restart_clears_speech_shot_alignment_state(done_task):
    store.update(
        done_task["task_id"],
        final_compose_summary={
            "speech_shot_alignment_status": "optimized",
            "shot_anchor_extra_silence_total": 0.24,
        },
        speech_shot_alignment={
            "speech_shot_alignment_status": "optimized",
            "speech_shot_alignment_decisions": [{"decision": "applied"}],
        },
        variants={
            "av": {
                "label": "av",
                "sentences": [
                    {
                        "asr_index": 1,
                        "base_compact_gap": 0.2,
                        "shot_anchor_final_gap": 0.28,
                        "shot_anchor_extra_silence": 0.08,
                        "shot_anchor_cut_time": 2.28,
                    }
                ],
                "final_compose_summary": {
                    "speech_shot_alignment_status": "optimized",
                },
                "av_debug": {
                    "final_compose_summary": {
                        "shot_anchor_extra_silence_total": 0.08,
                    }
                },
            }
        },
    )

    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None,
        voice_gender="male",
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
        subtitle_position="bottom",
        interactive_review=False,
        user_id=1,
        runner=_Runner(),
    )

    task = store.get(done_task["task_id"])
    assert task["speech_shot_alignment"] == {}
    assert task["final_compose_summary"] == {}
    assert "av" not in task["variants"]
```

- [ ] **Step 2: Run cleanup test to confirm failure**

Run:

```bash
pytest tests/test_task_restart.py::test_restart_clears_speech_shot_alignment_state -q
```

Expected: fails because `speech_shot_alignment` and `final_compose_summary` remain from old state.

- [ ] **Step 3: Reset fields in restart payload**

Modify `_build_reset_fields()` in `web/services/task_restart.py`:

```python
"final_compose_summary": {},
"speech_shot_alignment": {},
```

Place these next to `av_debug` and `tts_generation_summary` so restart cleanup stays with other derived TTS/AV outputs.

- [ ] **Step 4: Run restart tests**

Run:

```bash
pytest tests/test_task_restart.py::test_restart_clears_speech_shot_alignment_state tests/test_task_restart.py::test_restart_resets_state_and_persists_new_config -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit restart cleanup**

Run:

```bash
git add web/services/task_restart.py tests/test_task_restart.py
git commit -m "fix(omni): clear speech shot alignment on restart" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md"
```

## Task 5: Frontend Card Rendering

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Modify: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write failing template assertions**

Append to `tests/test_translate_detail_shell_templates.py`:

```python
def test_omni_speech_shot_alignment_card_is_rendered_between_tts_and_subtitle():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_task_workbench.html").read_text(encoding="utf-8")
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert 'id="speechShotAlignmentCard"' in template
    assert "语音镜头对齐" in template
    assert template.index('id="step-tts"') < template.index('id="speechShotAlignmentCard"') < template.index('id="step-subtitle"')
    assert "renderSpeechShotAlignmentCard" in script
    assert "speech_shot_alignment_decisions" in script
    assert "没有使用大模型" in script
    assert "为什么没做" in script
    assert ".speech-shot-card" in styles
    assert ".speech-shot-decision-table" in styles
```

- [ ] **Step 2: Run template test to confirm failure**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_omni_speech_shot_alignment_card_is_rendered_between_tts_and_subtitle -q
```

Expected: fails because the card and renderer do not exist.

- [ ] **Step 3: Add Omni-only card markup**

In `web/templates/_task_workbench.html`, insert after `</div>` for `step-tts` and before `{% if show_loudness_step %}` / `step-subtitle`:

```html
    {% if api_base == '/api/omni-translate' %}
    <div class="step speech-shot-card" id="speechShotAlignmentCard">
      <div class="step-main">
        <div class="step-icon done" id="icon-speech-shot-alignment">↔</div>
        <div style="flex:1">
          <div class="step-name-row">
            <span class="step-name">语音镜头对齐</span>
          </div>
          <div class="step-msg" id="msg-speech-shot-alignment">语音生成后、视频合成前的确定性对齐检查</div>
        </div>
      </div>
      <div class="step-preview" id="speechShotAlignmentBody"></div>
    </div>
    {% endif %}
```

- [ ] **Step 4: Add renderer and call it**

In `web/templates/_task_workbench_scripts.html`, add `renderSpeechShotAlignmentCard();` inside `renderTask()` immediately after `renderTtsDurationLog();`:

```javascript
    renderTtsDurationLog();
    renderSpeechShotAlignmentCard();
```

Add renderer functions near other TTS/final-summary helpers:

```javascript
  function getSpeechShotAlignmentSummary() {
    const av = currentTask?.variants?.av || {};
    const avDebug = av.av_debug || currentTask?.av_debug || {};
    return currentTask?.speech_shot_alignment
      || currentTask?.final_compose_summary
      || av.final_compose_summary
      || avDebug.final_compose_summary
      || null;
  }

  function speechShotReasonLabel(reason) {
    return ({
      nearby_cut_soft_snap: "贴近镜头切点",
      too_far_from_cut: "附近没有可用镜头切点",
      would_exceed_final_gap_cap: "会超过单段最终静音上限",
      over_budget: "会超过全局额外静音预算",
      hook_protection: "开头 Hook 保护",
      would_overrun_video: "会导致最终音频超过视频",
      final_speech_exceeds_video: "当前语音已经超过视频时长",
      no_extra_needed: "原有紧凑静音已经足够",
    })[reason] || reason || "--";
  }

  function renderSpeechShotAlignmentCard() {
    const card = document.getElementById("speechShotAlignmentCard");
    const body = document.getElementById("speechShotAlignmentBody");
    const msg = document.getElementById("msg-speech-shot-alignment");
    if (!card || !body) return;
    const summary = getSpeechShotAlignmentSummary();
    if (!summary || summary.speech_shot_alignment_status == null) {
      card.className = "step speech-shot-card waiting";
      if (msg) msg.textContent = "等待语音生成完成后分析";
      body.innerHTML = '<div class="preview-placeholder">语音生成完成后会检查句间静音和镜头切点是否有对齐优化空间。</div>';
      return;
    }
    const status = summary.speech_shot_alignment_status || "skipped";
    const applied = Boolean(summary.speech_shot_alignment_applied);
    card.className = `step speech-shot-card ${applied ? "done" : "waiting"}`;
    if (msg) {
      msg.textContent = applied
        ? "已完成确定性对齐优化"
        : "已完成确定性检查，没有改动音频时间轴";
    }
    const decisions = Array.isArray(summary.speech_shot_alignment_decisions)
      ? summary.speech_shot_alignment_decisions
      : [];
    const rows = decisions.length ? decisions.slice(0, 12).map(row => `
      <tr>
        <td>${escapeHtml(row.sentence_index ?? row.asr_index ?? "--")}</td>
        <td>${row.cut_time != null ? Number(row.cut_time).toFixed(2) + "s" : "--"}</td>
        <td>${row.base_compact_gap != null ? Number(row.base_compact_gap).toFixed(2) + "s" : "--"}</td>
        <td>${row.final_gap != null ? Number(row.final_gap).toFixed(2) + "s" : (row.required_final_gap != null ? Number(row.required_final_gap).toFixed(2) + "s" : "--")}</td>
        <td>${row.extra_silence != null ? Number(row.extra_silence).toFixed(2) + "s" : "--"}</td>
        <td>${row.decision === "applied" ? "已优化" : "未改动"}</td>
        <td>${escapeHtml(speechShotReasonLabel(row.reason))}</td>
      </tr>
    `).join("") : "";
    const skipReasons = summary.shot_anchor_skip_reasons || {};
    const skipHtml = Object.keys(skipReasons).length
      ? Object.entries(skipReasons).map(([key, value]) => `<span>${escapeHtml(speechShotReasonLabel(key))} ${escapeHtml(value)}</span>`).join("")
      : "<span>没有跳过项</span>";
    body.innerHTML = `
      <div class="speech-shot-summary">
        <div class="speech-shot-metric"><span>分析边界</span><strong>${escapeHtml(summary.speech_shot_alignment_analyzed_boundaries ?? 0)}</strong></div>
        <div class="speech-shot-metric"><span>镜头锚点</span><strong>${escapeHtml(summary.shot_anchor_cut_count ?? 0)}</strong></div>
        <div class="speech-shot-metric"><span>优化断点</span><strong>${escapeHtml(summary.shot_anchor_aligned_boundary_count ?? 0)}</strong></div>
        <div class="speech-shot-metric"><span>额外静音</span><strong>${Number(summary.shot_anchor_extra_silence_total || 0).toFixed(2)}s</strong></div>
      </div>
      <div class="speech-shot-note">没有使用大模型；只根据语音段时间、原有紧凑静音、镜头切点和预算做确定性判断。有优化就做，没有优化就保留原时间轴。</div>
      ${rows ? `<table class="speech-shot-decision-table"><thead><tr><th>句</th><th>切点</th><th>原静音</th><th>最终静音</th><th>新增</th><th>决策</th><th>原因</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="preview-placeholder">没有可展示的单点改动。</div>'}
      <div class="speech-shot-skips"><strong>为什么没做：</strong>${skipHtml}</div>
    `;
  }
```

- [ ] **Step 5: Add styles**

Append to `web/templates/_task_workbench_styles.html`:

```css
.speech-shot-card .step-preview {
  gap: var(--space-3);
}
.speech-shot-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--space-3);
}
.speech-shot-metric {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--bg);
}
.speech-shot-metric span {
  display: block;
  color: var(--fg-muted);
  font-size: var(--text-xs);
}
.speech-shot-metric strong {
  display: block;
  margin-top: 4px;
  color: var(--fg);
  font-size: var(--text-lg);
}
.speech-shot-note,
.speech-shot-skips {
  color: var(--fg-muted);
  font-size: var(--text-sm);
  line-height: var(--leading);
}
.speech-shot-skips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.speech-shot-skips span {
  border-radius: var(--radius-full);
  background: var(--bg-muted);
  padding: 3px 8px;
  color: var(--fg-muted);
  font-size: var(--text-xs);
  font-weight: 700;
}
.speech-shot-decision-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}
.speech-shot-decision-table th,
.speech-shot-decision-table td {
  border-bottom: 1px solid var(--border);
  padding: 7px 8px;
  text-align: left;
  vertical-align: top;
}
.speech-shot-decision-table th {
  color: var(--fg-muted);
  font-weight: 800;
  background: var(--bg-subtle);
}
```

- [ ] **Step 6: Run frontend hook test**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_omni_speech_shot_alignment_card_is_rendered_between_tts_and_subtitle -q
```

Expected: test passes.

- [ ] **Step 7: Commit frontend card**

Run:

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html web/templates/_task_workbench_styles.html tests/test_translate_detail_shell_templates.py
git commit -m "feat(omni): show speech shot alignment card" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md"
```

## Task 6: Focused Regression Suite

**Files:**
- Existing files from previous tasks.

- [ ] **Step 1: Run focused backend and frontend tests**

Run:

```bash
pytest \
  tests/test_speech_shot_alignment.py \
  tests/test_sentence_translate_runtime.py \
  tests/test_task_restart.py \
  tests/test_translate_detail_shell_templates.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run broader related tests**

Run:

```bash
pytest \
  tests/test_audio_stitch.py \
  tests/test_runtime_omni_dispatch.py \
  tests/test_omni_translate_routes.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Check working tree and summarize**

Run:

```bash
git status --short
```

Expected: only unrelated pre-existing files remain modified/untracked, or a clean tree if no unrelated work exists.

## Self-Review

Spec coverage:

- TTS convergence unchanged: Task 3 calls alignment only after compact scheduling and before final rebuild.
- No LLM: Task 1 tests source text for LLM/provider terms; Task 2 module has no model calls.
- No stacked silence: Task 1 asserts one final gap; Task 2 replaces `audio_gap_before` instead of adding a second layer.
- Frontend card: Task 5 adds the card, metrics, decisions, and skipped reasons.
- Force restart cleanup: Task 4 resets stale outputs.

Placeholder scan:

- The plan contains no unresolved placeholder markers or open-ended validation steps.
- Each task names exact files and commands.

Type consistency:

- Backend summary field names use `speech_shot_alignment_*` and `shot_anchor_*` consistently.
- Sentence-level fields use `base_compact_gap`, `shot_anchor_final_gap`, `shot_anchor_extra_silence`, `shot_anchor_cut_time`, `shot_anchor_before_start`, `shot_anchor_after_start`, and `shot_anchor_reason`.
