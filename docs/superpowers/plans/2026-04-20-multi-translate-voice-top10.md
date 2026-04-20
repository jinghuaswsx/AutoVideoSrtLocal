# Multi-Translate Voice Top-10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the multi-language voice selector showing 10 vector-matched recommendations after ASR and after male/female rematch, without letting the pinned default voice consume one of those recommendation slots.

**Architecture:** Extend the shared voice-matching function so callers can exclude specific voice IDs before slicing to `top_k`. Use that path from both `MultiTranslateRunner._step_voice_match()` and `/api/multi-translate/<task_id>/rematch`. Update the selector UI so `只看推荐` hides the pinned default row when it is not part of the recommendation set.

**Tech Stack:** Python, Flask, NumPy, existing `pipeline.voice_match` helpers, vanilla JS, pytest.

---

### Task 1: Lock Down Recommendation Semantics

**Files:**
- Modify: `pipeline/voice_match.py`
- Modify: `tests/test_voice_match.py`

- [ ] **Step 1: Write the failing unit test for excluded default voices**

```python
def test_match_candidates_excludes_voice_ids_without_shrinking_top_k():
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    rows = [...]
    with patch("pipeline.voice_match._query_voices_by_language", return_value=rows):
        top = match_candidates(
            query_vec,
            language="en",
            top_k=3,
            exclude_voice_ids={"default"},
        )
    assert [c["voice_id"] for c in top] == ["b", "c", "d"]
```

- [ ] **Step 2: Run the focused test and watch it fail**

Run: `python -m pytest tests/test_voice_match.py::test_match_candidates_excludes_voice_ids_without_shrinking_top_k -q`

Expected: `TypeError` because `exclude_voice_ids` is not supported yet.

- [ ] **Step 3: Implement minimal exclusion support in `match_candidates`**

```python
excluded = {voice_id for voice_id in (exclude_voice_ids or []) if voice_id}
...
if row["voice_id"] in excluded:
    continue
```

- [ ] **Step 4: Re-run the focused voice-match test file**

Run: `python -m pytest tests/test_voice_match.py -q`

Expected: all voice-match unit tests pass.

### Task 2: Use the New Exclusion Path in Multi-Translate

**Files:**
- Modify: `appcore/runtime_multi.py`
- Modify: `web/routes/multi_translate.py`
- Modify: `tests/test_runtime_multi_voice_match.py`
- Create or Modify: `tests/test_multi_translate_routes.py`

- [ ] **Step 1: Write failing regression tests for runtime and rematch route**

```python
assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}
assert payload["candidates"][0]["voice_id"] == "voice-b"
```

- [ ] **Step 2: Run only the new regression tests**

Run: `python -m pytest tests/test_runtime_multi_voice_match.py tests/test_multi_translate_routes.py -q`

Expected: failures showing the exclusion argument or route behavior is missing.

- [ ] **Step 3: Pass the default voice ID into both recommendation flows**

```python
default_voice_id = resolve_default_voice(lang, user_id=self.user_id)
candidates = match_candidates(
    vec,
    language=lang,
    top_k=10,
    exclude_voice_ids={default_voice_id} if default_voice_id else None,
)
```

- [ ] **Step 4: Re-run runtime and route tests**

Run: `python -m pytest tests/test_runtime_multi_voice_match.py tests/test_multi_translate_routes.py -q`

Expected: all targeted backend regression tests pass.

### Task 3: Keep the UI Count Honest

**Files:**
- Modify: `web/static/voice_selector_multi.js`

- [ ] **Step 1: Adjust the pinned-default rendering guard**

```javascript
const showPinnedDefault = !!defaultVoice && applyFilter(defaultVoice) && (!onlyRec || candidatesMap.has(defaultVoice.voice_id));
```

- [ ] **Step 2: Verify the full targeted suite after the JS tweak**

Run: `python -m pytest tests/test_voice_match.py tests/test_runtime_multi_voice_match.py tests/test_multi_translate_routes.py tests/test_multi_translate_e2e_smoke.py -q`

Expected: all targeted tests pass.

- [ ] **Step 3: Sanity-check the worktree diff before reporting**

Run: `git status --short`

Expected: only the intended plan, tests, backend files, and selector JS are modified.
