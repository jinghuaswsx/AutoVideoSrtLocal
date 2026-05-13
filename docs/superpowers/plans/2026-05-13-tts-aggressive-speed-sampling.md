# TTS Aggressive Speed Sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make over-video TTS speed assembly spend its three native-speed samples on independent `speed=1.06` candidates while widening safe speed bounds to `[0.94, 1.06]`.

**Architecture:** Keep the existing segment-assembly optimizer and adoption rules. Add a small speed candidate policy helper in `appcore/runtime/_helpers.py`, then let `_run_segment_speedup_assembly` consume candidate specs instead of deduplicating speeds through feedback-only adaptive selection.

**Tech Stack:** Python 3.12, pytest, existing `PipelineRunner` duration loop, ElevenLabs TTS engine wrapper.

---

### Task 1: Candidate Policy Tests

**Files:**
- Modify: `tests/test_tts_duration_loop.py`
- Modify: `appcore/runtime/_helpers.py`

- [ ] **Step 1: Write failing tests for widened bounds and over-video repeated samples**

Add tests near `TestSegmentCandidateAssembly`:

```python
def test_aggressive_over_video_speed_policy_repeats_1_06_samples(self):
    from appcore.runtime import _speedup_sampling_plan

    plan = _speedup_sampling_plan(
        base_duration=30.5,
        video_duration=26.8,
        previous_candidates=[],
    )

    assert [item["speed"] for item in plan] == [1.06, 1.06, 1.06]
    assert [item["attempt"] for item in plan] == [1, 2, 3]
    assert [item["sample_index"] for item in plan] == [1, 2, 3]
```

```python
def test_under_floor_speed_policy_uses_widened_slow_bounds(self):
    from appcore.runtime import _adaptive_speed_candidate

    assert _adaptive_speed_candidate(
        base_duration=40.0,
        video_duration=60.0,
        previous_candidates=[],
    ) == pytest.approx(0.94)
    assert _adaptive_speed_candidate(
        base_duration=80.0,
        video_duration=60.0,
        previous_candidates=[],
    ) == pytest.approx(1.06)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSegmentCandidateAssembly::test_aggressive_over_video_speed_policy_repeats_1_06_samples tests/test_tts_duration_loop.py::TestSegmentCandidateAssembly::test_under_floor_speed_policy_uses_widened_slow_bounds -q
```

Expected: fail because `_speedup_sampling_plan` is not exported and adaptive bounds are still `[0.95, 1.05]`.

- [ ] **Step 3: Implement minimal helper changes**

In `appcore/runtime/_helpers.py`, widen `_TTS_SPEED_MIN/_MAX` to `0.94/1.06`, update docs, and add `_speedup_sampling_plan`:

```python
def _speedup_sampling_plan(
    *,
    base_duration: float,
    video_duration: float,
    previous_candidates: list[dict] | None,
    max_candidates: int = 3,
) -> list[dict]:
    previous = list(previous_candidates or [])
    remaining = max(0, int(max_candidates) - len(previous))
    if remaining <= 0:
        return []
    if base_duration > video_duration:
        start = len(previous) + 1
        return [
            {"attempt": attempt, "sample_index": attempt, "speed": 1.06}
            for attempt in range(start, start + remaining)
        ]
    speed = _adaptive_speed_candidate(
        base_duration=base_duration,
        video_duration=video_duration,
        previous_candidates=previous,
        max_candidates=max_candidates,
    )
    if speed is None:
        return []
    attempt = len(previous) + 1
    return [{"attempt": attempt, "sample_index": attempt, "speed": speed}]
```

Export it from `appcore/runtime/__init__.py`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the same targeted pytest command. Expected: pass.

### Task 2: Runtime Uses Same-Speed Samples

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Modify: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Write failing duration-loop test**

Update or add a test that patches `speedup_segments_by_speed` by attempt key so three `1.06` calls can produce distinct durations:

```python
def test_over_video_stage1_uses_three_1_06_samples_and_can_adopt_later_sample(self, tmp_path, monkeypatch):
    called = self._common_patches(
        monkeypatch,
        audio_dur=30.5,
        audio_segments=[6.0, 6.0, 6.0, 6.0, 6.5],
        speedup_segments_by_attempt={
            1: [6.0, 6.0, 6.0, 6.0, 6.1],
            2: [5.4, 5.4, 5.4, 5.4, 5.0],
            3: [6.0, 6.0, 6.0, 6.0, 6.0],
        },
    )
    runner = self._make_runner()
    result = self._run(runner, tmp_path, video_duration=26.8)

    assert called["speedup_speeds"] == [1.06, 1.06]
    round_rec = result["rounds"][0]
    assert round_rec["segment_assembly_hit"] is True
    assert round_rec["segment_assembly_duration"] == pytest.approx(26.6)
    assert round_rec["speedup_candidates"][1]["sample_index"] == 2
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
pytest tests/test_tts_duration_loop.py::TestSpeedupShortcut::test_over_video_stage1_uses_three_1_06_samples_and_can_adopt_later_sample -q
```

Expected: fail because runtime still calls `_adaptive_speed_candidate` directly and does not record `sample_index`.

- [ ] **Step 3: Update runtime loop**

In `_run_segment_speedup_assembly`, replace the per-attempt speed selection with `_speedup_sampling_plan(...)[:1]` each iteration. Use returned `attempt`, `sample_index`, and `speed` for variant naming and metadata. Include both attempt and sample index in the variant suffix so repeated `1.06` writes separate files.

- [ ] **Step 4: Run the new test and verify GREEN**

Run the same targeted pytest command. Expected: pass.

### Task 3: Regression Suite

**Files:**
- Modify: `tests/test_tts_duration_loop.py`
- Modify: `docs/superpowers/plans/2026-05-13-tts-aggressive-speed-sampling.md`

- [ ] **Step 1: Run focused duration-loop tests**

Run:

```bash
pytest tests/test_tts_duration_loop.py -q
```

Expected: pass.

- [ ] **Step 2: Run TTS speed pipeline tests**

Run:

```bash
pytest tests/test_tts_speedup_pipeline.py -q
```

Expected: pass.

- [ ] **Step 3: Inspect diff against the spec**

Run:

```bash
git diff -- docs/superpowers/specs/2026-05-13-tts-aggressive-speed-sampling-design.md appcore/runtime/_helpers.py appcore/runtime/_pipeline_runner.py tests/test_tts_duration_loop.py
```

Expected: code and tests cover widened bounds, repeated `1.06`, unique variants, sample metadata, and unchanged assembly adoption.
