# TTS Best-Pick Overrun Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent best-pick TTS output from exporting audio longer than the source video when the five-round duration loop fails to converge.

**Architecture:** Reuse the existing segment candidate assembly helper inside the best-pick fallback path before destructive truncation. Add a hard overrun guard that truncates only best-pick output to `video_duration` and keeps returned TTS segment metadata aligned with the audible prefix.

**Tech Stack:** Python 3.12, pytest, existing `PipelineRunner._run_tts_duration_loop`, existing `pipeline.tts` assembly helpers, existing runtime duration helpers.

---

## File Structure

- Modify `tests/test_tts_duration_loop.py`: add regression tests under `TestSpeedupShortcut` because the existing fixture already stubs TTS generation, speedup candidates, assembly, and duration probes.
- Modify `appcore/runtime/_pipeline_runner.py`: extend `_run_tts_duration_loop` best-pick fallback only; do not touch normal converged or shortcut-window behavior.
- Reference `docs/superpowers/specs/2026-05-13-tts-best-pick-overrun-guard-design.md` in commit messages.

## Task 1: Best-Pick Assembly Regression

**Files:**
- Modify: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Write the failing test**

Add this test to `class TestSpeedupShortcut`:

```python
    def test_best_pick_overrun_tries_segment_assembly_outside_shortcut_window(
        self, tmp_path, monkeypatch,
    ):
        """Best-pick at +12% over video should still get one native-speed assembly chance."""
        called = self._common_patches(
            monkeypatch,
            audio_dur=[33.5, 42.4, 28.0, 27.0, 26.0],
            audio_segments=[14.2, 14.1, 14.1],
            speedup_segments_by_speed={
                1.05: [12.6, 12.5, 12.4],
            },
        )
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=37.872)
        round_rec = result["rounds"][1]

        assert result["final_round"] == 2
        assert called["speedup_speeds"] == [1.05]
        assert result["tts_audio_path"].endswith(
            "tts_full.round_2.segment_assembly.assembled.mp3"
        )
        assert round_rec["speedup_context"] == "best_pick_overrun"
        assert round_rec["speedup_final_audio_choice"] == "assembly"
        assert round_rec["final_reason"] == "best_pick_segment_assembly_refined"
        assert round_rec["segment_assembly_hit"] is True
        assert round_rec["segment_assembly_duration"] == pytest.approx(37.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut::test_best_pick_overrun_tries_segment_assembly_outside_shortcut_window -q
```

Expected: FAIL because the current best-pick path never calls speedup assembly after all rounds finish.

## Task 2: Hard Overrun Guard Regression

**Files:**
- Modify: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Write the failing test**

Add this test to `class TestSpeedupShortcut`:

```python
    def test_best_pick_overrun_miss_truncates_to_video_duration_and_fits_segments(
        self, tmp_path, monkeypatch,
    ):
        """If assembly misses, best-pick output must be capped to video duration, not v+2."""
        called = self._common_patches(
            monkeypatch,
            audio_dur=[33.5, 42.4, 28.0, 27.0, 26.0],
            audio_segments=[14.2, 14.1, 14.1],
            speedup_segments_by_speed={
                1.05: [14.0, 14.0, 14.0],
            },
        )
        runner = self._make_runner()
        result = self._run(runner, tmp_path, video_duration=37.872)
        round_rec = result["rounds"][1]

        assert result["final_round"] == 2
        assert called["speedup_speeds"] == [1.05]
        assert result["tts_audio_path"].endswith("tts_full.hard_overrun.normal.mp3")
        assert [s["tts_duration"] for s in result["tts_segments"]] == [
            pytest.approx(14.2),
            pytest.approx(14.1),
            pytest.approx(9.572),
        ]
        assert round_rec["speedup_context"] == "best_pick_overrun"
        assert round_rec["speedup_final_audio_choice"] == "truncated"
        assert round_rec["final_reason"] == "best_pick_hard_truncated"
        assert round_rec["hard_overrun_guard_applied"] is True
        assert round_rec["hard_overrun_post_duration"] == pytest.approx(37.872)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut::test_best_pick_overrun_miss_truncates_to_video_duration_and_fits_segments -q
```

Expected: FAIL because the current code returns the original best-pick segments after `_maybe_tempo_align` skips.

## Task 3: Implement Best-Pick Overrun Guard

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`

- [ ] **Step 1: Add best-pick assembly call**

Inside `_run_tts_duration_loop`, after `best_record["final_distance"] = round(best_distance, 3)` and before `_maybe_tempo_align`, call the existing `_run_segment_speedup_assembly` helper when `best_record["audio_duration"] > video_duration`.

Implementation shape:

```python
        best_record["speedup_final_audio_choice"] = "best_pick"
        if best_record["audio_duration"] > video_duration:
            best_record["speedup_applied"] = True
            best_record["speedup_context"] = "best_pick_overrun"
            best_record["speedup_pre_duration"] = best_record["audio_duration"]
            _substep("最佳轮次仍超长：生成段级 speed 候选并重组音频")
            self._emit_duration_round(task_id, best_i + 1, "speedup_start", best_record)
            assembly = _run_segment_speedup_assembly(
                result={
                    "full_audio_path": best_product["tts_audio_path"],
                    "segments": best_product["tts_segments"],
                },
                audio_duration=best_record["audio_duration"],
                round_record=best_record,
                context="best_pick_overrun",
            )
            if assembly.get("hit"):
                best_product["tts_audio_path"] = assembly["audio_path"]
                best_product["tts_segments"] = assembly["segments"]
                best_record["audio_duration"] = assembly["duration"]
                best_record["final_reason"] = "best_pick_segment_assembly_refined"
                best_record["speedup_final_audio_choice"] = "assembly"
                best_record["final_distance"] = 0.0
            self._emit_duration_round(task_id, best_i + 1, "speedup_done", best_record)
```

- [ ] **Step 2: Add hard overrun guard**

After `_maybe_tempo_align`, probe the chosen path. If it is still over `video_duration`, call `_truncate_audio_to_duration` with `duration=video_duration`, then update product and metadata.

Implementation shape:

```python
        final_best_audio = self._maybe_tempo_align(...)
        best_product["tts_audio_path"] = final_best_audio
        final_best_duration = tts_engine.get_audio_duration(final_best_audio)
        if final_best_duration > video_duration:
            hard_path = os.path.join(task_dir, f"tts_full.hard_overrun.{variant}.mp3")
            trim_result = self._truncate_audio_to_duration(
                input_audio_path=final_best_audio,
                output_audio_path=hard_path,
                duration=video_duration,
                tts_segments=best_product["tts_segments"],
                tts_script=best_product["tts_script"],
                localized_translation=best_product["localized_translation"],
            )
            if not trim_result.get("skipped"):
                best_product["tts_audio_path"] = trim_result["audio_path"]
                best_product["tts_segments"] = trim_result["tts_segments"]
                best_product["tts_script"] = trim_result["tts_script"]
                best_product["localized_translation"] = trim_result["localized_translation"]
                best_record["hard_overrun_guard_applied"] = True
                best_record["hard_overrun_pre_duration"] = round(final_best_duration, 3)
                best_record["hard_overrun_post_duration"] = trim_result["final_duration"]
                best_record["hard_overrun_removed_count"] = trim_result["removed_count"]
                best_record["hard_overrun_removed_duration"] = trim_result["removed_duration"]
                best_record["hard_overrun_audio_path"] = _relative(trim_result["audio_path"])
                best_record["speedup_final_audio_choice"] = "truncated"
                best_record["final_reason"] = "best_pick_hard_truncated"
                best_record["final_distance"] = 0.0
```

- [ ] **Step 3: Keep persisted round state in sync**

Before `task_state.update(...)`, set `rounds[best_i] = best_record` after all assembly/truncation updates. The existing `_save_json` call later will persist the updated `rounds`.

- [ ] **Step 4: Run both new tests**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut::test_best_pick_overrun_tries_segment_assembly_outside_shortcut_window tests/test_tts_duration_loop.py::TestSpeedupShortcut::test_best_pick_overrun_miss_truncates_to_video_duration_and_fits_segments -q
```

Expected: PASS.

## Task 4: Regression Verification

**Files:**
- Test only.

- [ ] **Step 1: Run speedup shortcut suite**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut -q
```

Expected: PASS.

- [ ] **Step 2: Run focused pipeline helper suite**

Run:

```bash
pytest tests/test_tts_speedup_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 3: Inspect git diff**

Run:

```bash
git diff -- docs/superpowers/plans/2026-05-13-tts-best-pick-overrun-guard.md tests/test_tts_duration_loop.py appcore/runtime/_pipeline_runner.py
```

Expected: diff only touches the plan, focused tests, and the best-pick section of `_run_tts_duration_loop`.

## Self-Review

- Spec coverage: Tasks 1 and 3 cover best-pick assembly outside the old shortcut window. Tasks 2 and 3 cover truncation to video duration and metadata fitting. Task 4 covers existing regression suites.
- Marker scan: no unresolved markers are intentionally left in this plan.
- Type consistency: metadata names match the spec: `speedup_context`, `speedup_final_audio_choice`, `hard_overrun_guard_applied`, and `best_pick_hard_truncated`.
