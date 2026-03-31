# Step Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-step preview panels so every pipeline stage can show its current key output inline on the main page.

**Architecture:** Extend in-memory task state with a unified `artifacts` payload and a safe artifact download route, then update the pipeline runner to populate preview metadata after each stage. Keep the existing page structure, but turn each step card into a stateful preview container that renders text, audio, video, and download outputs from the task JSON and artifact route.

**Tech Stack:** Flask, Flask-SocketIO, vanilla JavaScript, pytest, HTML/CSS

---

## File Structure

- Modify: `web/store.py`
  Responsibility: initialize and update task-scoped preview artifacts.
- Modify: `web/routes/task.py`
  Responsibility: expose task details plus safe artifact media access.
- Modify: `web/services/pipeline_runner.py`
  Responsibility: persist stage outputs into `artifacts` as the pipeline advances.
- Modify: `web/templates/index.html`
  Responsibility: render per-step preview panels and hydrate them from task details.
- Modify: `tests/test_web_routes.py`
  Responsibility: verify task detail payloads and artifact route behavior.

### Task 1: Add task artifact state and media route

**Files:**
- Modify: `web/store.py`
- Modify: `web/routes/task.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_task_detail_returns_artifacts_structure():
    app = create_app()
    client = app.test_client()

    task = store.create("task-preview", "video.mp4", "output/task-preview")
    store.update(
        "task-preview",
        artifacts={
            "extract": {
                "title": "音频提取",
                "items": [{"type": "audio", "label": "提取音频", "artifact": "audio_extract"}],
            }
        },
    )

    response = client.get("/api/tasks/task-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert "artifacts" in payload
    assert payload["artifacts"]["extract"]["items"][0]["artifact"] == "audio_extract"


def test_artifact_route_serves_whitelisted_preview_file(tmp_path):
    app = create_app()
    client = app.test_client()

    audio_path = tmp_path / "preview.mp3"
    audio_path.write_bytes(b"audio-preview")
    store.create("task-file", "video.mp4", str(tmp_path))
    store.update(
        "task-file",
        artifacts={
            "extract": {
                "title": "音频提取",
                "items": [{"type": "audio", "label": "提取音频", "artifact": "audio_extract"}],
            }
        },
        preview_files={"audio_extract": str(audio_path)},
    )

    response = client.get("/api/tasks/task-file/artifact/audio_extract")

    assert response.status_code == 200
    assert response.data == b"audio-preview"


def test_artifact_route_rejects_unknown_name(tmp_path):
    app = create_app()
    client = app.test_client()

    store.create("task-bad", "video.mp4", str(tmp_path))

    response = client.get("/api/tasks/task-bad/artifact/not_allowed")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `pytest tests/test_web_routes.py -q`
Expected: FAIL because task payloads do not yet include `artifacts` defaults and `/api/tasks/<id>/artifact/<name>` does not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
# web/store.py
def create(task_id: str, video_path: str, task_dir: str) -> dict:
    task = {
        ...
        "artifacts": {},
        "preview_files": {},
    }
    _tasks[task_id] = task
    return task


def set_artifact(task_id: str, step: str, payload: dict):
    task = _tasks.get(task_id)
    if task:
        task.setdefault("artifacts", {})[step] = payload


def set_preview_file(task_id: str, name: str, path: str):
    task = _tasks.get(task_id)
    if task:
        task.setdefault("preview_files", {})[name] = path
```

```python
# web/routes/task.py
@bp.route("/<task_id>/artifact/<name>", methods=["GET"])
def get_artifact(task_id, name):
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    allowed = task.get("preview_files", {})
    path = allowed.get(name)
    if not path or not os.path.exists(path):
        return jsonify({"error": "Artifact not found"}), 404

    return send_file(os.path.abspath(path), as_attachment=False)
```

- [ ] **Step 4: Run the route tests to verify they pass**

Run: `pytest tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_routes.py web/store.py web/routes/task.py
git commit -m "feat: add task preview artifact routes"
```

### Task 2: Persist preview artifacts during pipeline execution

**Files:**
- Modify: `web/services/pipeline_runner.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_done_payload_is_compatible_with_step_previews():
    task = store.create("task-preview-payload", "video.mp4", "output/task-preview-payload")
    store.update(
        "task-preview-payload",
        result={"soft_video": "soft.mp4", "hard_video": "hard.mp4", "srt": "subtitle.srt"},
        exports={"capcut_archive": "capcut.zip", "capcut_manifest": "manifest.json"},
    )

    store.set_artifact(
        "task-preview-payload",
        "compose",
        {
            "title": "视频合成",
            "items": [
                {"type": "video", "label": "软字幕视频", "artifact": "soft_video"},
                {"type": "video", "label": "硬字幕视频", "artifact": "hard_video"},
            ],
        },
    )

    saved = store.get("task-preview-payload")

    assert saved["artifacts"]["compose"]["items"][0]["artifact"] == "soft_video"
```

- [ ] **Step 2: Run the focused test to verify the current store shape is insufficient**

Run: `pytest tests/test_web_routes.py::test_pipeline_done_payload_is_compatible_with_step_previews -q`
Expected: FAIL until artifact helpers are exercised consistently.

- [ ] **Step 3: Write the minimal implementation**

```python
# web/services/pipeline_runner.py
def _artifact_media(task_id: str, label: str, artifact: str, kind: str) -> dict:
    return {"type": kind, "label": label, "artifact": artifact}


def _artifact_text(task_id: str, title: str, step: str, items: list[dict]):
    store.set_artifact(task_id, step, {"title": title, "items": items})
```

```python
# inside pipeline steps
store.set_preview_file(task_id, "audio_extract", audio_path)
store.set_artifact(task_id, "extract", {
    "title": "音频提取",
    "items": [{"type": "audio", "label": "提取音频", "artifact": "audio_extract"}],
})

store.set_artifact(task_id, "asr", {
    "title": "语音识别",
    "items": [{"type": "utterances", "label": "识别文本", "utterances": utterances}],
})

store.set_artifact(task_id, "subtitle", {
    "title": "字幕生成",
    "items": [{"type": "text", "label": "SRT 预览", "content": srt_content}],
})
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_web_routes.py -q`
Expected: PASS with artifact metadata available for later UI rendering.

- [ ] **Step 5: Commit**

```bash
git add web/services/pipeline_runner.py tests/test_web_routes.py
git commit -m "feat: persist per-step preview artifacts"
```

### Task 3: Render step previews in the main page

**Files:**
- Modify: `web/templates/index.html`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_index_page_contains_step_preview_container():
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "step-preview" in body
    assert "renderStepPreviews" in body
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_web_routes.py::test_index_page_contains_step_preview_container -q`
Expected: FAIL because the page only renders status rows today.

- [ ] **Step 3: Write the minimal implementation**

```html
<div class="step-body">
  <div class="step-main">
    ...
  </div>
  <div class="step-preview" id="preview-extract">
    <div class="preview-placeholder">当前步骤产物会显示在这里</div>
  </div>
</div>
```

```javascript
async function refreshTaskState() {
  if (!taskId) return;
  const response = await fetch(`/api/tasks/${taskId}`);
  currentTask = await response.json();
  renderStepPreviews(currentTask.artifacts || {});
}

function renderStepPreviews(artifacts) {
  STEP_ORDER.forEach(step => {
    const previewEl = document.getElementById(`preview-${step}`);
    ...
  });
}
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/templates/index.html tests/test_web_routes.py
git commit -m "feat: render inline step previews"
```

### Task 4: Regression verification

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/services/pipeline_runner.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Run the full targeted suite**

Run: `pytest tests/test_web_routes.py tests/test_compose.py tests/test_capcut_export.py -q`
Expected: PASS

- [ ] **Step 2: Run the full project suite**

Run: `pytest tests -q`
Expected: PASS

- [ ] **Step 3: Run compile verification**

Run: `python -m compileall -q pipeline web main.py config.py`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add web/templates/index.html web/services/pipeline_runner.py web/routes/task.py web/store.py tests/test_web_routes.py
git commit -m "feat: add step output previews to pipeline page"
```
