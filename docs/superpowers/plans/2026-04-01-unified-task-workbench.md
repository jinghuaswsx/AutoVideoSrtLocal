# Unified Task Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the project detail page and execution page into a single task workbench, and add a task-level `全自动 / 手动确认` mode that truly pauses reviewable steps when enabled.

**Architecture:** Reuse the existing artifact protocol and task state as the source of truth, extract shared workbench rendering from the current execution page, and teach the runtime to persist step messages plus waiting/confirmation state for alignment and translation. Route both fresh uploads and existing projects through the same task workbench instead of maintaining a summary-only detail page.

**Tech Stack:** Flask, Jinja2, Socket.IO client, existing `appcore.task_state`, existing `PipelineRunner`, pytest.

---

## File Map

**New files:**
- `web/templates/_task_workbench.html` - shared workbench markup for config, progress, reviews, and results
- `web/templates/_task_workbench_scripts.html` - shared JS renderer and task state client logic

**Modified files:**
- `appcore/task_state.py` - persist step messages and review state
- `appcore/runtime.py` - true waiting behavior for alignment/translation when manual mode is enabled
- `web/routes/projects.py` - feed the unified workbench page with initial task state
- `web/routes/task.py` - accept/store confirmation mode and support resuming paused tasks
- `web/templates/index.html` - reduce to upload entry + shared workbench partials
- `web/templates/project_detail.html` - replace summary layout with shared workbench
- `tests/test_pipeline_runner.py` - cover auto/manual runtime behavior
- `tests/test_web_routes.py` - cover unified workbench template and request flow

---

### Task 1: Add failing tests for manual confirmation runtime behavior

**Files:**
- Modify: `tests/test_pipeline_runner.py`
- Modify: `appcore/runtime.py`
- Modify: `appcore/task_state.py`

- [ ] **Step 1: Write a failing test for manual alignment pause**

Add a test to `tests/test_pipeline_runner.py` asserting that `interactive_review=True` leaves alignment in `waiting`, preserves artifacts, and does not auto-confirm:

```python
def test_step_alignment_waits_when_interactive_review_enabled(tmp_path, monkeypatch):
    task = store.create("task-manual-alignment", "video.mp4", str(tmp_path))
    task["interactive_review"] = True
    task["utterances"] = [
        {"text": "hello", "start_time": 0.0, "end_time": 0.8},
        {"text": "world", "start_time": 0.8, "end_time": 1.6},
    ]

    monkeypatch.setattr("pipeline.alignment.detect_scene_cuts", lambda video_path: [])
    monkeypatch.setattr(
        "pipeline.alignment.compile_alignment",
        lambda utterances, scene_cuts=None: {
            "break_after": [False, True],
            "script_segments": [{"index": 0, "text": "hello world", "start_time": 0.0, "end_time": 1.6}],
        },
    )

    class FakeVoiceLibrary:
        def recommend_voice(self, text):
            return {"id": "adam"}

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_alignment("task-manual-alignment", "video.mp4", str(tmp_path))

    saved = store.get("task-manual-alignment")
    assert saved["steps"]["alignment"] == "waiting"
    assert saved["_alignment_confirmed"] is False
    assert saved["artifacts"]["alignment"]["items"][1]["segments"][0]["text"] == "hello world"
```

- [ ] **Step 2: Write a failing test for manual translation pause**

Add a test asserting translation enters `waiting` and does not mark `_segments_confirmed`:

```python
def test_step_translate_waits_when_interactive_review_enabled(tmp_path, monkeypatch):
    task = store.create("task-manual-translate", "video.mp4", str(tmp_path))
    task["interactive_review"] = True
    task["script_segments"] = [{"index": 0, "text": "你好", "start_time": 0.0, "end_time": 1.0}]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "你好")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal": {
            "full_text": "Hello there",
            "sentences": [{"index": 0, "text": "Hello there", "source_segment_indices": [0]}],
        },
    )

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_translate("task-manual-translate")

    saved = store.get("task-manual-translate")
    assert saved["steps"]["translate"] == "waiting"
    assert saved["_segments_confirmed"] is False
    assert saved["artifacts"]["translate"]["layout"] == "variant_compare"
```

- [ ] **Step 3: Run only the new tests to verify they fail**

Run:

```bash
pytest tests/test_pipeline_runner.py -k "manual_alignment or manual_translate" -q
```

Expected: failures because runtime still marks these steps `done`.

---

### Task 2: Add failing tests for the unified workbench page

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `web/templates/index.html`
- Modify: `web/templates/project_detail.html`

- [ ] **Step 1: Add a failing template parity test for project detail**

Add a test asserting the project detail page contains the same workbench hooks the execution page depends on:

```python
def test_project_detail_page_contains_shared_workbench_hooks(logged_in_client):
    store.create("task-project-workbench", "video.mp4", "output/task-project-workbench")

    response = logged_in_client.get("/projects/task-project-workbench")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "voiceSelect" in body
    assert "interactiveReviewToggle" in body
    assert "renderStepPreviews" in body
    assert "pipelineCard" in body
```

- [ ] **Step 2: Add a failing test for project detail initial task hydration**

Add a test asserting persisted task state is embedded for JS bootstrap:

```python
def test_project_detail_page_bootstraps_persisted_task_state(logged_in_client):
    task = store.create("task-project-state", "video.mp4", "output/task-project-state")
    store.update(
        "task-project-state",
        interactive_review=True,
        steps={"extract": "done", "asr": "waiting", "alignment": "waiting", "translate": "pending", "tts": "pending", "subtitle": "pending", "compose": "pending", "export": "pending"},
    )

    response = logged_in_client.get("/projects/task-project-state")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "task-project-state" in body
    assert "\"interactive_review\": true" in body.lower()
```

- [ ] **Step 3: Add a failing test for upload page confirmation mode control**

Add a test asserting the upload/execution page exposes the new mode switch:

```python
def test_index_page_contains_confirmation_mode_control(logged_in_client):
    response = logged_in_client.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "interactiveReviewToggle" in body
    assert "全自动" in body
    assert "手动确认" in body
```

- [ ] **Step 4: Run only the new route/template tests to verify they fail**

Run:

```bash
pytest tests/test_web_routes.py -k "workbench_hooks or project_detail_page_bootstraps or confirmation_mode_control" -q
```

Expected: failures because the project detail page still renders the older summary view.

---

### Task 3: Persist step messages and review state in task_state

**Files:**
- Modify: `appcore/task_state.py`

- [ ] **Step 1: Add minimal persistent fields to task creation**

Extend `create()` so new tasks include:

```python
"step_messages": {
    "extract": "",
    "asr": "",
    "alignment": "",
    "translate": "",
    "tts": "",
    "subtitle": "",
    "compose": "",
    "export": "",
},
"current_review_step": "",
```

- [ ] **Step 2: Add helper(s) to persist step messages**

Add a helper like:

```python
def set_step_message(task_id: str, step: str, message: str):
    task = _tasks.get(task_id)
    if task:
        task.setdefault("step_messages", {})[step] = message
        _sync_task_to_db(task_id)
```

and export it through `web/store.py`.

- [ ] **Step 3: Ensure review-step tracking is persisted**

Add/update helpers so `current_review_step` is written when a task enters or leaves manual review, for example:

```python
def set_current_review_step(task_id: str, step: str):
    task = _tasks.get(task_id)
    if task:
        task["current_review_step"] = step
        _sync_task_to_db(task_id)
```

- [ ] **Step 4: Run the focused task_state-related tests**

Run:

```bash
pytest tests/test_appcore_task_state.py -q
```

Expected: existing task_state tests still pass.

---

### Task 4: Make runtime truly pause for manual confirmation

**Files:**
- Modify: `appcore/runtime.py`

- [ ] **Step 1: Update `_set_step()` to persist the latest step message**

Inside `_set_step()`, persist the message through the new task_state helper before emitting:

```python
if message:
    task_state.set_step_message(task_id, step, message)
```

- [ ] **Step 2: Change alignment to enter `waiting` in manual mode**

In `_step_alignment()`:

- keep artifact persistence as-is
- if `interactive_review` is true:
  - `task_state.set_current_review_step(task_id, "alignment")`
  - `self._set_step(task_id, "alignment", "waiting", "分段结果已生成，等待人工确认")`
  - emit payload with `requires_confirmation=True`
- else:
  - auto-confirm and keep `done`

- [ ] **Step 3: Change translation to enter `waiting` in manual mode**

In `_step_translate()`:

- after persisting artifacts, if `interactive_review` is true:
  - build editable segment payload from `task["script_segments"]` plus localized output
  - set `_segments_confirmed=False`
  - `task_state.set_current_review_step(task_id, "translate")`
  - `self._set_step(task_id, "translate", "waiting", "翻译结果已生成，等待人工确认")`
  - emit `translate_result` with `requires_confirmation=True`
- else:
  - keep `_segments_confirmed=True`
  - keep `done`

- [ ] **Step 4: Re-run the focused runtime tests**

Run:

```bash
pytest tests/test_pipeline_runner.py -k "manual_alignment or manual_translate or auto_confirms" -q
```

Expected: the new manual tests and the existing auto-confirm test pass together.

---

### Task 5: Build shared workbench template fragments

**Files:**
- Create: `web/templates/_task_workbench.html`
- Create: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/index.html`
- Modify: `web/templates/project_detail.html`

- [ ] **Step 1: Extract shared workbench markup**

Move the repeated UI sections out of `web/templates/index.html` into `_task_workbench.html`:

- config card
- pipeline card
- review panels
- result panel
- error area

Keep parameters data-driven via template variables such as:

- `task_id`
- `initial_task`
- `allow_upload`
- `allow_processing`

- [ ] **Step 2: Extract shared renderer JS**

Move the task rendering helpers and socket/task refresh logic into `_task_workbench_scripts.html`, keeping one renderer implementation for:

- `renderStepPreviews`
- `renderVariantCompareArtifact`
- `renderAlignmentReview`
- `renderTranslateReview`
- `renderDownloads`

- [ ] **Step 3: Keep upload page as the new-task shell**

Reduce `web/templates/index.html` to:

- upload zone
- `{% include "_task_workbench.html" %}`
- `{% include "_task_workbench_scripts.html" %}`

with `allow_upload=True`, `allow_processing=True`, and no preloaded task by default.

- [ ] **Step 4: Turn project detail into the same workbench**

Replace the old summary-centric `project_detail.html` with the shared workbench includes and initial task bootstrap data, using:

- `allow_upload=False`
- `allow_processing=True`
- `task_id=project.id`
- `initial_task=state`

- [ ] **Step 5: Re-run the focused template tests**

Run:

```bash
pytest tests/test_web_routes.py -k "workbench_hooks or project_detail_page_bootstraps or confirmation_mode_control" -q
```

Expected: all three tests pass.

---

### Task 6: Wire the workbench route flow and resume behavior

**Files:**
- Modify: `web/routes/projects.py`
- Modify: `web/routes/task.py`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Pass initial serialized task state into project detail**

Update `projects.detail()` so the template gets both the parsed `state` dict and a JSON-safe string for JS bootstrap.

- [ ] **Step 2: Persist the new confirmation mode from the start request**

Ensure `/api/tasks/<task_id>/start` reads `interactive_review` from the request body and stores it as a real boolean sourced from the new UI control.

- [ ] **Step 3: Redirect fresh uploads into the project workbench**

Update the upload flow JS so that after `POST /api/tasks` succeeds, the browser navigates to `/projects/<task_id>` instead of staying on the temporary shell page.

- [ ] **Step 4: Support resuming paused tasks from the project workbench**

In the shared JS bootstrap:

- if `initial_task.steps` already shows `waiting`
- or `initial_task.current_review_step` is set

then restore the appropriate review panel without waiting for a new socket event.

- [ ] **Step 5: Run focused workbench navigation tests**

Run:

```bash
pytest tests/test_web_routes.py -k "project_detail or task_detail or index_page" -q
```

Expected: route/template coverage stays green for the touched surfaces that do not depend on local MySQL availability.

---

### Task 7: Finish confirm APIs so they clear waiting state and continue cleanly

**Files:**
- Modify: `web/routes/task.py`
- Modify: `appcore/task_state.py`

- [ ] **Step 1: Clear review-step metadata after alignment confirm**

After `store.confirm_alignment(...)`, clear `current_review_step`, rebuild the alignment artifact, and set the alignment step back to `done`.

- [ ] **Step 2: Clear review-step metadata after translation confirm**

After `store.confirm_segments(...)`, clear `current_review_step`, rebuild the translate artifact, and set the translate step back to `done`.

- [ ] **Step 3: Add/adjust tests for confirm APIs under manual mode**

Extend `tests/test_web_routes.py` with assertions that the confirm endpoints:

- set the internal confirm flag
- leave the persisted artifact updated
- clear the waiting review marker

- [ ] **Step 4: Run the focused confirm tests**

Run:

```bash
pytest tests/test_web_routes.py -k "alignment_route or segments_route" -q
```

Expected: the updated confirm-route tests pass.

---

### Task 8: Final verification for the feature slice

**Files:**
- Modify: none unless fixes are needed

- [ ] **Step 1: Run the focused runtime and route suites together**

Run:

```bash
pytest tests/test_pipeline_runner.py tests/test_appcore_task_state.py tests/test_web_routes.py -q
```

Expected: feature-focused tests pass, except any pre-existing cases blocked by the local MySQL dependency.

- [ ] **Step 2: Smoke-check template rendering manually if needed**

Run:

```bash
python main.py
```

Then verify:

- upload page shows the new `全自动 / 手动确认` control
- upload success navigates to `/projects/<task_id>`
- `/projects/<task_id>` renders the full workbench instead of the old summary cards

- [ ] **Step 3: Record residual verification gap**

If local MySQL remains unavailable, note that full route/auth integration could not be verified end-to-end on this machine because the baseline DB-backed fixtures fail before exercising the page.
