# Omni Sentence Audio Final Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Omni `sentence_reconcile` final audio fallback: ffmpeg-align near misses in `0.9-1.1`, clip final overlong misses, give final short misses one extra expansion chance, and show these actions on the task detail page.

**Architecture:** Keep the behavior inside sentence-level TTS convergence. `pipeline.duration_reconcile` selects final fallback actions and emits progress metadata, `pipeline.audio_stitch` performs existing timeline clipping, `appcore.tts_strategies.sentence_reconcile` summarizes final output, and the shared workbench script renders the new metadata. No changes to the five-round whole-track duration loop.

**Tech Stack:** Python 3.12, pytest, ffmpeg/ffprobe subprocess calls, Flask/Jinja shared task workbench JavaScript.

---

## File Structure

- Modify `pipeline/duration_reconcile.py`: add ffmpeg tempo helper, near-miss fallback, final overlong/short fallback metadata, and progress phases.
- Modify `tests/test_duration_reconcile.py`: add red tests for near-miss ffmpeg alignment, overlong clip fallback metadata, extra expansion success, and extra expansion failure.
- Modify `appcore/tts_strategies/sentence_reconcile.py`: include intentional overlong fallback wording in final compose notes.
- Modify `tests/test_sentence_translate_runtime.py`: assert final compose summary includes overlong fallback note when a clipped segment came from `clip_overlong`.
- Modify `web/templates/_task_workbench_scripts.html`: add phase labels and sentence fallback badges/details.
- Modify `tests/test_translate_detail_shell_templates.py`: assert labels and phase names are present.

## Task 1: Duration Reconcile Near-Miss FFmpeg Alignment

**Files:**
- Modify: `tests/test_duration_reconcile.py`
- Modify: `pipeline/duration_reconcile.py`

- [ ] **Step 1: Write failing tests for near-miss long and short alignment**

Append these tests after `test_reconcile_duration_reverts_when_speed_adjustment_is_worse` in `tests/test_duration_reconcile.py`:

```python
def test_reconcile_duration_ffmpeg_aligns_near_miss_long_without_rewrite(monkeypatch):
    align_calls = []
    progress = []

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: pytest.fail("near-miss long audio should not rewrite"),
    )

    result = reconcile_duration(
        task={},
        av_output={"sentences": [{
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "target_duration": 5.0,
            "target_chars_range": (50, 60),
            "text": "Already close enough",
            "est_chars": 20,
        }]},
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 5.45}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        on_progress=progress.append,
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["tts_duration"] == pytest.approx(5.0)
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["tts_path"].endswith(".ffmpeg_tempo_r0_a1.mp3")
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["final_fallback_reason"] == "near_miss_ratio"
    assert sentence["ffmpeg_tempo_applied"] is True
    assert sentence["ffmpeg_tempo_ratio"] == pytest.approx(1.09)
    assert sentence["ffmpeg_tempo_pre_duration"] == pytest.approx(5.45)
    assert sentence["ffmpeg_tempo_post_duration"] == pytest.approx(5.0)
    assert sentence["text_rewrite_attempts"] == 0
    assert align_calls[0]["audio_path"] == "/tmp/seg0.mp3"
    assert any(event["phase"] == "ffmpeg_tempo_align" for event in progress)


def test_reconcile_duration_ffmpeg_aligns_near_miss_short_without_rewrite(monkeypatch):
    align_calls = []

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: pytest.fail("near-miss short audio should not rewrite"),
    )

    result = reconcile_duration(
        task={},
        av_output={"sentences": [{
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "target_duration": 5.0,
            "target_chars_range": (50, 60),
            "text": "Already close enough",
            "est_chars": 20,
        }]},
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.55}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["tts_duration"] == pytest.approx(5.0)
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["ffmpeg_tempo_ratio"] == pytest.approx(0.91)
    assert align_calls[0]["audio_duration"] == pytest.approx(4.55)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
pytest tests/test_duration_reconcile.py::test_reconcile_duration_ffmpeg_aligns_near_miss_long_without_rewrite tests/test_duration_reconcile.py::test_reconcile_duration_ffmpeg_aligns_near_miss_short_without_rewrite -q
```

Expected: both tests fail because `_apply_ffmpeg_tempo_alignment` is missing or near-miss audio still enters the old path.

- [ ] **Step 3: Implement minimal ffmpeg alignment helper and near-miss path**

In `pipeline/duration_reconcile.py`, add `subprocess` import and constants:

```python
import subprocess

MIN_FFMPEG_TEMPO_RATIO = 0.9
MAX_FFMPEG_TEMPO_RATIO = 1.1
```

Add helper functions after `_candidate_suffix`:

```python
def _ffmpeg_tempo_output_path(current: dict, *, round_number: int, attempt_number: int) -> str:
    output_path = current.get("tts_path") or f"av_seg_{current['asr_index']}.mp3"
    base, ext = os.path.splitext(output_path)
    return f"{base}.ffmpeg_tempo_r{round_number}_a{attempt_number}{ext or '.mp3'}"


def _apply_ffmpeg_tempo_alignment(
    *,
    audio_path: str,
    audio_duration: float,
    target_duration: float,
    output_path: str,
) -> dict | None:
    if not audio_path or audio_duration <= 0 or target_duration <= 0:
        return None
    ratio = audio_duration / target_duration
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return None
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-filter:a", f"atempo={ratio:.4f}",
        "-vn", "-acodec", "libmp3lame", "-q:a", "3",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:
        return {"failed_reason": _error_text(exc)}
    if result.returncode != 0:
        return {"failed_reason": (result.stderr or "ffmpeg tempo alignment failed")[:500]}
    post_duration = tts.get_audio_duration(output_path)
    if post_duration <= 0:
        return {"failed_reason": "ffprobe returned empty duration"}
    return {
        "ratio": round(ratio, 4),
        "pre_duration": round(audio_duration, 3),
        "post_duration": round(post_duration, 3),
        "new_audio_path": output_path,
    }
```

Add helper after `_try_speed_adjustment`:

```python
def _try_ffmpeg_tempo_alignment(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
    reason: str,
) -> bool:
    target_duration = float(current.get("target_duration", 0.0) or 0.0)
    audio_duration = float(current.get("tts_duration", 0.0) or 0.0)
    ratio = duration_ratio(target_duration, audio_duration)
    if not (MIN_FFMPEG_TEMPO_RATIO <= ratio <= MAX_FFMPEG_TEMPO_RATIO):
        return False
    current["speed_adjustment_attempts"] += 1
    round_number = int(current.get("selected_attempt_round", 0) or 0)
    output_path = _ffmpeg_tempo_output_path(
        current,
        round_number=round_number,
        attempt_number=current["speed_adjustment_attempts"],
    )
    result = _apply_ffmpeg_tempo_alignment(
        audio_path=str(current.get("tts_path") or ""),
        audio_duration=audio_duration,
        target_duration=target_duration,
        output_path=output_path,
    )
    current["final_fallback_action"] = "ffmpeg_tempo_align"
    current["final_fallback_reason"] = reason
    if not result or result.get("failed_reason"):
        current["ffmpeg_tempo_applied"] = False
        current["ffmpeg_tempo_failed_reason"] = (result or {}).get("failed_reason") or "ffmpeg tempo alignment skipped"
        _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
        return False
    current["tts_path"] = result["new_audio_path"]
    current["tts_duration"] = float(result["post_duration"])
    current["duration_ratio"] = duration_ratio(target_duration, current["tts_duration"])
    current["speed"] = result["ratio"]
    current["status"] = "speed_adjusted"
    current["ffmpeg_tempo_applied"] = True
    current["ffmpeg_tempo_ratio"] = result["ratio"]
    current["ffmpeg_tempo_pre_duration"] = result["pre_duration"]
    current["ffmpeg_tempo_post_duration"] = result["post_duration"]
    current["ffmpeg_tempo_audio_path"] = result["new_audio_path"]
    _emit_sentence_progress(on_progress, position=position, current=current, phase="ffmpeg_tempo_align")
    return True
```

In `_reconcile_one_sentence`, replace the `status == "ok"` speed adjustment block with:

```python
    if status == "ok":
        if not _try_ffmpeg_tempo_alignment(
            current=current,
            position=position,
            on_progress=on_progress,
            reason="near_miss_ratio",
        ):
            _try_speed_adjustment(current=current, voice_id=voice_id, target_language=target_language)
            _emit_sentence_progress(on_progress, position=position, current=current, phase="speed_adjust")
```

Also replace the post-rewrite `status == "ok"` speed adjustment block with the same `_try_ffmpeg_tempo_alignment(... reason="near_miss_ratio")` first.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_duration_reconcile.py::test_reconcile_duration_ffmpeg_aligns_near_miss_long_without_rewrite tests/test_duration_reconcile.py::test_reconcile_duration_ffmpeg_aligns_near_miss_short_without_rewrite -q
```

Expected: both tests pass.

## Task 2: Final Overlong And Short Fallback

**Files:**
- Modify: `tests/test_duration_reconcile.py`
- Modify: `pipeline/duration_reconcile.py`

- [ ] **Step 1: Write failing tests for clip-overlong and final extra expansion**

Append these tests after the near-miss tests:

```python
def test_reconcile_duration_marks_final_overlong_for_clip_without_extra_rewrite(monkeypatch):
    durations = iter([6.2, 6.1])
    rewrite_calls = []
    progress = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return f"Candidate {kwargs['attempt_number']}"

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={},
        av_output={"sentences": [{
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "target_duration": 5.0,
            "target_chars_range": (60, 70),
            "text": "Long text",
            "est_chars": 9,
        }]},
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 6.4}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=2,
        on_progress=progress.append,
    )

    sentence = result[0]
    assert sentence["status"] == "warning_long"
    assert sentence["final_fallback_action"] == "clip_overlong"
    assert sentence["final_fallback_reason"] == "overlong_after_attempts"
    assert sentence["best_effort"] is True
    assert sentence["best_effort_reason"] == "max_attempts_exhausted"
    assert [call["attempt_number"] for call in rewrite_calls] == [1, 2]
    assert any(event["phase"] == "final_clip_fallback" for event in progress)


def test_reconcile_duration_final_short_extra_expand_can_align(monkeypatch):
    durations = iter([4.0, 4.45, 4.7])
    rewrite_calls = []
    align_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        if kwargs["attempt_number"] == 999:
            return "Final expanded candidate"
        return f"Candidate {kwargs['attempt_number']}"

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={},
        av_output={"sentences": [{
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "target_duration": 5.0,
            "target_chars_range": (20, 30),
            "text": "Short",
            "est_chars": 5,
        }]},
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.0}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=2,
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["text"] == "Final expanded candidate"
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["final_extra_expand_attempted"] is True
    assert sentence["final_rewrite_result"] == "aligned"
    assert sentence["final_extra_expand_before_text"] == "Candidate 2"
    assert sentence["final_extra_expand_after_text"] == "Final expanded candidate"
    assert [call["direction"] for call in rewrite_calls] == ["expand", "expand", "expand"]
    assert rewrite_calls[-1]["attempt_number"] == 999
    assert align_calls[0]["audio_duration"] == pytest.approx(4.7)


def test_reconcile_duration_final_short_extra_expand_failure_does_not_loop(monkeypatch):
    durations = iter([4.0, 4.1, 4.2])
    rewrite_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return f"Candidate {kwargs['attempt_number']}"

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={},
        av_output={"sentences": [{
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "target_duration": 5.0,
            "target_chars_range": (20, 30),
            "text": "Short",
            "est_chars": 5,
        }]},
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.0}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=2,
    )

    sentence = result[0]
    assert sentence["status"] == "warning_short"
    assert sentence["final_fallback_action"] == "extra_expand_failed"
    assert sentence["final_fallback_reason"] == "short_after_attempts"
    assert sentence["final_extra_expand_attempted"] is True
    assert sentence["final_rewrite_result"] == "still_short"
    assert len(rewrite_calls) == 3
    assert rewrite_calls[-1]["attempt_number"] == 999
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
pytest tests/test_duration_reconcile.py::test_reconcile_duration_marks_final_overlong_for_clip_without_extra_rewrite tests/test_duration_reconcile.py::test_reconcile_duration_final_short_extra_expand_can_align tests/test_duration_reconcile.py::test_reconcile_duration_final_short_extra_expand_failure_does_not_loop -q
```

Expected: tests fail because final fallback metadata and extra expansion do not exist.

- [ ] **Step 3: Implement final fallback helpers**

In `pipeline/duration_reconcile.py`, add after `_try_ffmpeg_tempo_alignment`:

```python
def _mark_clip_overlong_fallback(
    *,
    current: dict,
    position: int,
    on_progress: Callable[[dict], None] | None,
) -> None:
    current["final_fallback_action"] = "clip_overlong"
    current["final_fallback_reason"] = "overlong_after_attempts"
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_clip_fallback")


def _run_final_extra_expand(
    *,
    current: dict,
    position: int,
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None,
    project_id: str | None,
    on_progress: Callable[[dict], None] | None,
) -> None:
    before_text = current.get("text", "")
    current["final_extra_expand_attempted"] = True
    current["final_fallback_action"] = "extra_expand"
    current["final_fallback_reason"] = "short_after_attempts"
    current["final_extra_expand_before_text"] = before_text
    current["active_attempt"] = 999
    current["active_action"] = "expand"
    current["active_temperature"] = av_translate.rewrite_temperature_for_attempt(999)
    current["active_tts_attempt"] = current.get("tts_regenerate_attempts", 0) + 1
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_rewrite_start")
    try:
        new_text = av_translate.rewrite_one(
            asr_index=int(current.get("asr_index", position)),
            prev_text=before_text,
            overshoot_sec=0.0,
            direction="expand",
            new_target_chars_range=tuple(current.get("target_chars_range") or (1, 2)),
            script_segments=script_segments,
            shot_notes=shot_notes,
            av_inputs=av_inputs,
            voice_id=voice_id,
            user_id=user_id,
            project_id=project_id,
            attempt_number=999,
            previous_attempts=list(current.get("attempts") or []),
            temperature=current["active_temperature"],
            required_terms=list(current.get("must_keep_terms") or []),
            omitted_terms=list(current.get("omitted_source_terms") or []),
            return_sentence=True,
        )
    except Exception as exc:
        current["final_fallback_action"] = "extra_expand_failed"
        current["final_rewrite_result"] = "rewrite_failed"
        current["final_extra_expand_error"] = _error_text(exc)
        _emit_sentence_progress(on_progress, position=position, current=current, phase="final_rewrite_result")
        return
    if isinstance(new_text, dict):
        new_text = str(new_text.get("text") or "")
    else:
        new_text = str(new_text or "")
    current["text"] = new_text
    current["est_chars"] = len(new_text)
    current["final_extra_expand_after_text"] = new_text
    current["tts_path"], current_duration = _regenerate_segment(
        sentence=current,
        voice_id=voice_id,
        target_language=target_language,
        suffix=_candidate_suffix("final_expand", 999),
    )
    current["tts_regenerate_attempts"] += 1
    current["tts_duration"] = current_duration
    current["duration_ratio"] = duration_ratio(current["target_duration"], current_duration)
    status, speed = classify_overshoot(current["target_duration"], current_duration)
    current["status"] = status
    current["speed"] = speed
    if _try_ffmpeg_tempo_alignment(
        current=current,
        position=position,
        on_progress=on_progress,
        reason="short_after_attempts",
    ):
        current["final_rewrite_result"] = "aligned"
    elif current["duration_ratio"] < MIN_FFMPEG_TEMPO_RATIO:
        current["final_fallback_action"] = "extra_expand_failed"
        current["final_rewrite_result"] = "still_short"
        current["status"] = "warning_short"
    else:
        current["final_fallback_action"] = "extra_expand_failed"
        current["final_rewrite_result"] = "still_long"
        current["status"] = _warning_status_for_ratio(current["duration_ratio"])
    _emit_sentence_progress(on_progress, position=position, current=current, phase="final_rewrite_result")
```

After the normal loop applies `best_candidate` and sets `warning_*`, add:

```python
                final_ratio = duration_ratio(current["target_duration"], current["tts_duration"])
                if MIN_FFMPEG_TEMPO_RATIO <= final_ratio <= MAX_FFMPEG_TEMPO_RATIO:
                    _try_ffmpeg_tempo_alignment(
                        current=current,
                        position=position,
                        on_progress=on_progress,
                        reason="near_miss_ratio",
                    )
                elif final_ratio > MAX_FFMPEG_TEMPO_RATIO:
                    _mark_clip_overlong_fallback(
                        current=current,
                        position=position,
                        on_progress=on_progress,
                    )
                else:
                    _run_final_extra_expand(
                        current=current,
                        position=position,
                        voice_id=voice_id,
                        target_language=target_language,
                        av_inputs=av_inputs,
                        shot_notes=shot_notes,
                        script_segments=script_segments,
                        user_id=user_id,
                        project_id=project_id,
                        on_progress=on_progress,
                    )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_duration_reconcile.py::test_reconcile_duration_marks_final_overlong_for_clip_without_extra_rewrite tests/test_duration_reconcile.py::test_reconcile_duration_final_short_extra_expand_can_align tests/test_duration_reconcile.py::test_reconcile_duration_final_short_extra_expand_failure_does_not_loop -q
```

Expected: all three tests pass.

## Task 3: Final Compose Summary And Front-End Visibility

**Files:**
- Modify: `tests/test_sentence_translate_runtime.py`
- Modify: `appcore/tts_strategies/sentence_reconcile.py`
- Modify: `tests/test_translate_detail_shell_templates.py`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Write failing runtime summary assertion**

In `tests/test_sentence_translate_runtime.py`, update `fake_rebuild` inside `test_tts_step_records_fallback_final_compose_summary` so the segment carries the fallback action:

```python
        segments[0]["final_fallback_action"] = "clip_overlong"
```

Add this assertion after the existing final processing label assertions:

```python
    assert any("超长截断" in note for note in summary["notes"])
```

- [ ] **Step 2: Write failing template assertions**

Append to `test_sentence_reconcile_process_is_rendered_in_tts_duration_log` in `tests/test_translate_detail_shell_templates.py`:

```python
    assert "ffmpeg_tempo_align" in script
    assert "final_rewrite_start" in script
    assert "final_rewrite_result" in script
    assert "final_clip_fallback" in script
    assert "FFmpeg 对齐" in script
    assert "超长截断" in script
    assert "二次重写" in script
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_tts_step_records_fallback_final_compose_summary tests/test_translate_detail_shell_templates.py::test_sentence_reconcile_process_is_rendered_in_tts_duration_log -q
```

Expected: failures because notes and labels are not implemented.

- [ ] **Step 4: Implement summary note and template labels**

In `_build_final_compose_summary` in `appcore/tts_strategies/sentence_reconcile.py`, after the existing clipped note:

```python
        if any(str(segment.get("final_fallback_action") or "") == "clip_overlong" for segment in clipped_segments):
            notes.append("超长截断兜底：句级收敛最终仍超过目标窗口，已按最终时间轴裁剪后输出。")
```

In `sentenceProgressPhaseLabel` in `web/templates/_task_workbench_scripts.html`, add:

```javascript
      ffmpeg_tempo_align: "FFmpeg 对齐",
      final_rewrite_start: "二次重写开始",
      final_rewrite_result: "二次重写结果",
      final_clip_fallback: "超长截断",
```

Add helper after `renderSemanticCoverageChip`:

```javascript
  function renderSentenceFinalFallbackBadge(sentence = {}, debug = {}) {
    const action = String(sentence.final_fallback_action || debug.final_fallback_action || "");
    const labels = {
      ffmpeg_tempo_align: "FFmpeg 对齐",
      clip_overlong: "超长截断",
      extra_expand: "二次重写",
      extra_expand_failed: "二次重写未收敛",
    };
    if (!labels[action]) return "";
    return `<span class="semantic-coverage-chip warning">${escapeHtml(labels[action])}</span>`;
  }

  function renderSentenceFinalFallbackDetail(sentence = {}, debug = {}) {
    const action = String(sentence.final_fallback_action || debug.final_fallback_action || "");
    if (!action) return "";
    const pre = numberOrNull(sentence.ffmpeg_tempo_pre_duration ?? debug.ffmpeg_tempo_pre_duration);
    const post = numberOrNull(sentence.ffmpeg_tempo_post_duration ?? debug.ffmpeg_tempo_post_duration);
    const ratio = numberOrNull(sentence.ffmpeg_tempo_ratio ?? debug.ffmpeg_tempo_ratio);
    if (action === "ffmpeg_tempo_align") {
      return `<div class="duration-round-meta">FFmpeg 对齐：${escapeHtml(formatAvDuration(pre))} → ${escapeHtml(formatAvDuration(post))} · atempo=${escapeHtml(ratio != null ? ratio.toFixed(4) : "--")}</div>`;
    }
    if (action === "clip_overlong") {
      return '<div class="duration-round-meta warning">超长截断：最终仍超过目标窗口，进入最终时间轴裁剪兜底。</div>';
    }
    const beforeText = sentence.final_extra_expand_before_text || debug.final_extra_expand_before_text || "";
    const afterText = sentence.final_extra_expand_after_text || debug.final_extra_expand_after_text || "";
    const result = sentence.final_rewrite_result || debug.final_rewrite_result || "";
    return `<div class="duration-round-meta warning">二次重写：${escapeHtml(result || "--")}${beforeText ? ` · 重写前：${escapeHtml(beforeText)}` : ""}${afterText ? ` · 重写后：${escapeHtml(afterText)}` : ""}</div>`;
  }
```

In `renderSentenceReconcileDetailRows`, after `const semanticHtml = ...`, add:

```javascript
      const fallbackBadgeHtml = renderSentenceFinalFallbackBadge(sentence, debug);
      const fallbackDetailHtml = renderSentenceFinalFallbackDetail(sentence, debug);
```

Then include `${fallbackBadgeHtml}` in the title next to `${semanticHtml}`, and include `${fallbackDetailHtml}` before `${issueHtml}`.

- [ ] **Step 5: Run tests and verify pass**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py::test_tts_step_records_fallback_final_compose_summary tests/test_translate_detail_shell_templates.py::test_sentence_reconcile_process_is_rendered_in_tts_duration_log -q
```

Expected: both tests pass.

## Task 4: Focused Regression Suite

**Files:**
- Verify only.

- [ ] **Step 1: Run duration reconcile suite**

Run:

```bash
pytest tests/test_duration_reconcile.py -q
```

Expected: pass.

- [ ] **Step 2: Run runtime and template focused suite**

Run:

```bash
pytest tests/test_sentence_translate_runtime.py tests/test_translate_detail_shell_templates.py -q
```

Expected: pass.

- [ ] **Step 3: Run combined required suite from spec**

Run:

```bash
pytest tests/test_duration_reconcile.py tests/test_sentence_translate_runtime.py tests/test_translate_detail_shell_templates.py -q
```

Expected: pass.

## Task 5: Dev Server Route Check

**Files:**
- Verify only.

- [ ] **Step 1: Start dev server on a free port**

Run:

```bash
python -m flask --app web.app:create_app run --host 127.0.0.1 --port 5057
```

Use another explicit high port, such as `5058`, only if `5057` is already occupied.

- [ ] **Step 2: Verify unauthenticated route behavior**

Run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<PORT>/omni-translate/14e2e3b7-8a76-4459-aa84-7a9a49799d70
```

Expected with the command above: `302` from `http://127.0.0.1:5057/omni-translate/14e2e3b7-8a76-4459-aa84-7a9a49799d70`.

- [ ] **Step 3: Stop dev server**

Stop the process started in Step 1. Do not restart production services.
