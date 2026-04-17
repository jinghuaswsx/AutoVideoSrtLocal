# TTS Duration Final Range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TTS duration control continue up to 5 rounds unless audio lands in `[video-3, video]`, then truncate over-long final audio instead of deleting tail blocks.

**Architecture:** Keep the existing round-based rewrite loop, but move success judgment to the final target window and add explicit final-audio truncation in `_step_tts`. Preserve downstream compatibility by updating final segment timing metadata to match the truncated audio.

**Tech Stack:** Python, pytest, ffmpeg, existing `appcore.runtime` helpers.

---

### Task 1: Add failing loop-behavior tests

**Files:**
- Modify: `tests/test_tts_duration_loop.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_round2_stage1_hit_but_not_final_range_continues(...):
    ...

def test_best_pick_uses_distance_to_final_range(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts_duration_loop.py -k "stage1_hit or final_range" -v`
Expected: FAIL because the current loop still stops in the old `[0.9v, 1.1v]` window.

- [ ] **Step 3: Implement minimal loop fix**

```python
if final_target_lo <= audio_duration <= final_target_hi:
    ...
best_i = min(range(len(rounds)), key=lambda i: _distance_to_range(...))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tts_duration_loop.py -k "stage1_hit or final_range" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tts_duration_loop.py appcore/runtime.py
git commit -m "fix: honor final tts duration target range"
```

### Task 2: Add failing final-audio handling tests

**Files:**
- Modify: `tests/test_tts_duration_loop.py`
- Modify: `appcore/runtime.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_step_tts_truncates_overlong_final_audio(...):
    ...

def test_step_tts_keeps_short_final_audio(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts_duration_loop.py -k "truncates_overlong or keeps_short" -v`
Expected: FAIL because `_step_tts` currently uses tail-block trimming instead of direct truncation.

- [ ] **Step 3: Implement minimal final-audio fix**

```python
def _truncate_audio_to_duration(...):
    ...

if pre_trim_duration > video_duration:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tts_duration_loop.py -k "truncates_overlong or keeps_short" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tts_duration_loop.py appcore/runtime.py
git commit -m "fix: truncate overlong final tts audio"
```

### Task 3: Run focused regression checks

**Files:**
- Modify: `appcore/runtime.py`
- Test: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: Run focused regression suite**

Run: `pytest tests/test_tts_duration_loop.py -v`
Expected: PASS

- [ ] **Step 2: Review related runtime behavior**

Run: `pytest tests/test_runtime_v2.py -k tts -v`
Expected: PASS or no matching tests.

- [ ] **Step 3: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "test: cover final range tts duration control"
```
