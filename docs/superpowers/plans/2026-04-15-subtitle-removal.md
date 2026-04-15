# Subtitle Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight subtitle-removal module where a user uploads one video, chooses `全屏去除` or `框选去除` on the first frame, and the server keeps polling the third-party job in the background until the cleaned result is stored back in our own TOS and downloadable from a task detail page.

**Architecture:** Add a standalone `subtitle_removal` project type with a dedicated upload page and a single-task detail page. Reuse the existing TOS direct-upload flow, `projects.state_json` persistence, FFmpeg utilities, and Socket.IO, but isolate provider submission, polling, result download, and restart recovery inside a dedicated runtime so the workflow does not depend on the browser staying open.

**Tech Stack:** Flask + Jinja2 + Socket.IO / MySQL `projects` table / existing TOS helpers / FFmpeg + ffprobe / `requests` / Python background threads

**Spec:** `docs/superpowers/specs/2026-04-15-subtitle-removal-design.md`

---

## File Structure

### Create

| File | Responsibility |
| --- | --- |
| `appcore/subtitle_removal_provider.py` | Wrap the third-party submit/progress API, normalize responses, and raise consistent provider errors. |
| `appcore/subtitle_removal_runtime.py` | Run submit -> poll -> download result -> upload result state transitions for one task. |
| `web/services/subtitle_removal_runner.py` | Start background threads and bridge runtime events to Socket.IO rooms. |
| `web/routes/subtitle_removal.py` | Upload page, detail page, upload APIs, submit/resume/resubmit/delete APIs, artifact/download APIs, and restart recovery entrypoint. |
| `web/templates/subtitle_removal_upload.html` | Lightweight upload page for the new module. |
| `web/templates/subtitle_removal_detail.html` | Detail page shell that loads the first-frame selector, task state, and result area. |
| `web/templates/_subtitle_removal_styles.html` | Shared CSS for upload/detail pages and the first-frame selector. |
| `web/templates/_subtitle_removal_scripts.html` | Frontend upload, selection, submit, polling fallback, and result rendering logic. |
| `tests/test_subtitle_removal_routes.py` | Route/API tests for bootstrap, complete, submit, resume, delete, artifact, and download endpoints. |
| `tests/test_subtitle_removal_runtime.py` | Runtime tests for provider polling, success, failure, result upload, and restart recovery. |
| `tests/test_subtitle_removal_provider.py` | Provider payload/response normalization tests. |

### Modify

| File | Change |
| --- | --- |
| `config.py` | Add provider URL/token/polling/max-duration settings for subtitle removal. |
| `appcore/events.py` | Add `sr_*` event names used by the new runtime and Socket.IO bridge. |
| `appcore/settings.py` | Register the `subtitle_removal` project label so retention settings and UI labels can recognize the new module. |
| `appcore/task_state.py` | Persist `projects.type`, add `create_subtitle_removal(...)`, and keep subtitle-removal-specific state fields consistent. |
| `appcore/tos_clients.py` | Include `result_tos_key` in cleanup key collection so delete/cleanup covers generated files too. |
| `pipeline/ffutil.py` | Add `probe_media_info(...)` so width/height/duration can be extracted during upload completion. |
| `web/store.py` | Re-export `create_subtitle_removal` for route code and tests that already rely on the facade. |
| `web/app.py` | Register the new blueprint, add the `join_subtitle_removal_task` Socket.IO event, and trigger restart recovery on startup. |
| `web/templates/layout.html` | Add the new sidebar entry with the `🧽` icon. |
| `tests/test_web_routes.py` | Add page-render/nav-icon regression coverage for the upload/detail shells. |

---

## Task 1: Scaffold the module shell

**Files:**
- Create: `web/routes/subtitle_removal.py`
- Create: `web/templates/subtitle_removal_upload.html`
- Create: `web/templates/subtitle_removal_detail.html`
- Create: `web/templates/_subtitle_removal_styles.html`
- Create: `web/templates/_subtitle_removal_scripts.html`
- Modify: `appcore/events.py`
- Modify: `web/app.py`
- Modify: `web/templates/layout.html`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing page-shell tests**

```python
def test_subtitle_removal_pages_render(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-page",
        "uploads/sr-page.mp4",
        "output/sr-page",
        original_filename="demo.mp4",
        user_id=1,
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "type": "subtitle_removal",
        "display_name": "demo",
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    upload_response = authed_client_no_db.get("/subtitle-removal")
    detail_response = authed_client_no_db.get("/subtitle-removal/sr-page")

    assert upload_response.status_code == 200
    assert "字幕移除" in upload_response.get_data(as_text=True)
    assert detail_response.status_code == 200
    body = detail_response.get_data(as_text=True)
    assert "全屏去除" in body
    assert "框选去除" in body
    assert "join_subtitle_removal_task" in body


def test_layout_contains_subtitle_removal_nav_icon(authed_client_no_db):
    response = authed_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/subtitle-removal"' in body
    assert '<span class="nav-icon">🧽</span>' in body
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_web_routes.py -q -k "subtitle_removal_pages_render or subtitle_removal_nav_icon"`

Expected: FAIL with `ModuleNotFoundError: No module named 'web.routes.subtitle_removal'` or `AssertionError` for missing route/nav content.

- [ ] **Step 3: Add the blueprint, templates, events, sidebar entry, and Socket.IO room hook**

```python
# appcore/events.py
EVT_SR_STEP_UPDATE = "sr_step_update"
EVT_SR_DONE = "sr_done"
EVT_SR_ERROR = "sr_error"
```

```python
# web/app.py
from web.routes.subtitle_removal import bp as subtitle_removal_bp

app.register_blueprint(subtitle_removal_bp)


@socketio.on("join_subtitle_removal_task")
def on_join_subtitle_removal(data):
    from flask_login import current_user
    if not current_user.is_authenticated:
        return
    task_id = data.get("task_id")
    if task_id:
        from web import store
        task = store.get(task_id)
        if task and task.get("_user_id") == current_user.id:
            join_room(task_id)
```

```python
# web/routes/subtitle_removal.py
bp = Blueprint("subtitle_removal", __name__)


@bp.route("/subtitle-removal")
@login_required
def upload_page():
    return render_template("subtitle_removal_upload.html")


@bp.route("/subtitle-removal/<task_id>")
@login_required
def detail_page(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'subtitle_removal' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    return render_template("subtitle_removal_detail.html", project=row, state=state, task_id=task_id)
```

```html
<!-- web/templates/layout.html -->
<a href="/subtitle-removal" class="nav-link{% if request.path.startswith('/subtitle-removal') %} active{% endif %}">
  <span class="nav-icon">🧽</span>
  字幕移除
</a>
```

```html
<!-- web/templates/subtitle_removal_upload.html -->
{% extends "layout.html" %}
{% block title %}字幕移除{% endblock %}
{% block content %}
{% include "_subtitle_removal_styles.html" %}
<section class="sr-page-card">
  <div class="sr-page-header">
    <h1>字幕移除</h1>
    <p>上传视频后先截取首帧，再选择“全屏去除”或“框选去除”。</p>
  </div>
  <div class="sr-upload-dropzone" id="srUploadDropzone">
    <input type="file" id="srUploadInput" accept="video/*" hidden>
    <button type="button" class="btn btn-primary" id="srPickVideoButton">上传视频</button>
    <p class="sr-upload-hint">上传完成后会自动提取首帧，并进入任务详情页。</p>
  </div>
</section>
{% include "_subtitle_removal_scripts.html" %}
{% endblock %}
```

```html
<!-- web/templates/subtitle_removal_detail.html -->
{% extends "layout.html" %}
{% block title %}字幕移除{% endblock %}
{% block content %}
{% include "_subtitle_removal_styles.html" %}
<section class="sr-detail-grid" data-task-id="{{ task_id }}">
  <article class="sr-card">
    <h2>源视频</h2>
    <div id="srSourceSummary"></div>
    <img id="srFirstFrameImage" alt="视频首帧">
  </article>
  <article class="sr-card">
    <h2>去除方式</h2>
    <div class="sr-mode-group">
      <label><input type="radio" name="remove_mode" value="full" checked> 全屏去除</label>
      <label><input type="radio" name="remove_mode" value="box"> 框选去除</label>
    </div>
    <div id="srSelectionStage"></div>
    <button type="button" class="btn btn-primary" id="srSubmitButton">提交去字幕任务</button>
  </article>
  <article class="sr-card">
    <h2>任务状态</h2>
    <div id="srStatusPanel"></div>
  </article>
  <article class="sr-card">
    <h2>处理结果</h2>
    <div id="srResultPanel"></div>
  </article>
</section>
<script>
  window.subtitleRemovalBootstrap = {{ state | tojson }};
</script>
{% include "_subtitle_removal_scripts.html" %}
{% endblock %}
```

- [ ] **Step 4: Run the page-shell tests again**

Run: `pytest tests/test_web_routes.py -q -k "subtitle_removal_pages_render or subtitle_removal_nav_icon"`

Expected: PASS.

- [ ] **Step 5: Commit the shell**

```bash
git add appcore/events.py web/app.py web/routes/subtitle_removal.py web/templates/layout.html web/templates/subtitle_removal_upload.html web/templates/subtitle_removal_detail.html web/templates/_subtitle_removal_styles.html web/templates/_subtitle_removal_scripts.html tests/test_web_routes.py
git commit -m "feat: scaffold subtitle removal module shell"
```

---

## Task 2: Add direct upload completion and media preparation

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `web/store.py`
- Modify: `pipeline/ffutil.py`
- Modify: `appcore/tos_clients.py`
- Modify: `web/routes/subtitle_removal.py`
- Create: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_appcore_task_state.py`
- Modify: `tests/test_tos_clients.py`

- [ ] **Step 1: Write the failing state, upload, and cleanup tests**

```python
def test_create_subtitle_removal_initializes_expected_shape():
    task = task_state.create_subtitle_removal(
        "sr-init",
        "uploads/source.mp4",
        "output/sr-init",
        original_filename="source.mp4",
        user_id=9,
    )

    assert task["type"] == "subtitle_removal"
    assert task["status"] == "uploaded"
    assert task["steps"] == {
        "prepare": "pending",
        "submit": "pending",
        "poll": "pending",
        "download_result": "pending",
        "upload_result": "pending",
    }
    assert task["remove_mode"] == ""
    assert task["selection_box"] is None
    assert task["result_tos_key"] == ""
```

```python
def test_subtitle_removal_complete_upload_prepares_first_frame(authed_client_no_db, monkeypatch, tmp_path):
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake-mp4")

    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.is_tos_configured", lambda: True)
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.build_source_object_key", lambda user_id, task_id, name: f"uploads/{user_id}/{task_id}/{name}")
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.generate_signed_upload_url", lambda key: f"https://upload.example/{key}")
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.object_exists", lambda key: True)
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.head_object", lambda key: type("Head", (), {"content_length": 2048})())
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.download_file", lambda key, path: str(source_video))
    monkeypatch.setattr("web.routes.subtitle_removal.extract_thumbnail", lambda video_path, output_dir, scale=None: str(tmp_path / "thumbnail.jpg"))
    monkeypatch.setattr("web.routes.subtitle_removal.probe_media_info", lambda path: {"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0})
    monkeypatch.setattr("web.routes.subtitle_removal.db_execute", lambda *args, **kwargs: None)

    bootstrap = authed_client_no_db.post("/api/subtitle-removal/upload/bootstrap", json={"original_filename": "source.mp4"})
    payload = bootstrap.get_json()

    complete = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert complete.status_code == 201
    task = store.get(payload["task_id"])
    assert task["steps"]["prepare"] == "done"
    assert task["thumbnail_path"].endswith("thumbnail.jpg")
    assert task["media_info"]["resolution"] == "720x1280"
```

```python
def test_collect_task_tos_keys_includes_result_tos_key():
    keys = tos_clients.collect_task_tos_keys({
        "source_tos_key": "uploads/1/a/source.mp4",
        "result_tos_key": "artifacts/1/a/subtitle_removal/result.mp4",
    })

    assert keys == [
        "uploads/1/a/source.mp4",
        "artifacts/1/a/subtitle_removal/result.mp4",
    ]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_appcore_task_state.py tests/test_tos_clients.py -q -k "subtitle_removal or result_tos_key"`

Expected: FAIL because `create_subtitle_removal`, `probe_media_info`, and `/api/subtitle-removal/upload/*` do not exist yet.

- [ ] **Step 3: Implement state creation, media probing, upload bootstrap/complete, and source artifact serving**

```python
# appcore/task_state.py
def create_subtitle_removal(task_id: str, video_path: str, task_dir: str,
                            original_filename: str, user_id: int) -> dict:
    task = {
        "id": task_id,
        "type": "subtitle_removal",
        "status": "uploaded",
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": original_filename,
        "display_name": "",
        "thumbnail_path": "",
        "source_tos_key": "",
        "source_object_info": {},
        "media_info": {"width": 0, "height": 0, "resolution": "", "duration": 0.0, "file_size_mb": 0.0},
        "steps": {
            "prepare": "pending",
            "submit": "pending",
            "poll": "pending",
            "download_result": "pending",
            "upload_result": "pending",
        },
        "step_messages": {},
        "remove_mode": "",
        "selection_box": None,
        "position_payload": None,
        "provider_task_id": "",
        "provider_status": "",
        "provider_emsg": "",
        "provider_result_url": "",
        "provider_raw": {},
        "poll_attempts": 0,
        "last_polled_at": None,
        "result_video_path": "",
        "result_tos_key": "",
        "result_object_info": {},
        "error": "",
        "_user_id": user_id,
    }
    with _lock:
        _tasks[task_id] = task
    _sync_task_to_db(task_id)
    return task
```

```python
# appcore/task_state.py
db_execute(
    """INSERT INTO projects (id, user_id, type, original_filename, status, task_dir, state_json, expires_at)
       VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
       ON DUPLICATE KEY UPDATE
         type = VALUES(type),
         status = VALUES(status),
         state_json = VALUES(state_json),
         task_dir = VALUES(task_dir)""",
    (task_id, user_id, task.get("type", "translation"), original_filename, task.get("status", "uploaded"), task.get("task_dir", ""), state_json),
)
```

```python
# pipeline/ffutil.py
def probe_media_info(path: str) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        return {
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}" if width and height else "",
            "duration": duration,
        }
    except Exception:
        return {"width": 0, "height": 0, "resolution": "", "duration": 0.0}
```

```python
# web/routes/subtitle_removal.py
bp = Blueprint("subtitle_removal", __name__, url_prefix="")


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        raise NotFound()
    return task


@bp.route("/api/subtitle-removal/upload/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    if not tos_clients.is_tos_configured():
        return jsonify({"error": "TOS is not configured"}), 503
    body = request.get_json(silent=True) or {}
    original_filename = os.path.basename((body.get("original_filename") or "").strip())
    if not original_filename:
        return jsonify({"error": "original_filename required"}), 400
    task_id = str(uuid.uuid4())
    object_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    return jsonify({
        "task_id": task_id,
        "object_key": object_key,
        "upload_url": tos_clients.generate_signed_upload_url(object_key),
    })


@bp.route("/api/subtitle-removal/upload/complete", methods=["POST"])
@login_required
def complete_upload():
    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    original_filename = os.path.basename((body.get("original_filename") or "").strip())
    object_key = (body.get("object_key") or "").strip()
    if not task_id or not original_filename or not object_key:
        return jsonify({"error": "task_id, original_filename and object_key required"}), 400
    expected_key = tos_clients.build_source_object_key(current_user.id, task_id, original_filename)
    if object_key != expected_key:
        return jsonify({"error": "object_key mismatch"}), 400
    if not tos_clients.object_exists(object_key):
        return jsonify({"error": "uploaded object not found"}), 400

    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    ext = os.path.splitext(original_filename)[1].lower() or ".mp4"
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    tos_clients.download_file(object_key, video_path)

    task = store.create_subtitle_removal(task_id, video_path, task_dir, original_filename, current_user.id)
    media_info = probe_media_info(video_path)
    thumbnail_path = extract_thumbnail(video_path, task_dir)
    display_name = os.path.splitext(original_filename)[0][:32] or "subtitle-removal"
    object_head = tos_clients.head_object(object_key)

    store.update(
        task_id,
        status="ready",
        display_name=display_name,
        thumbnail_path=thumbnail_path or "",
        source_tos_key=object_key,
        source_object_info={
            "file_size": int(getattr(object_head, "content_length", 0) or body.get("file_size") or 0),
            "content_type": (body.get("content_type") or "").strip(),
            "original_filename": original_filename,
        },
        media_info={
            **media_info,
            "file_size_mb": round((int(getattr(object_head, "content_length", 0) or body.get("file_size") or 0)) / 1024 / 1024, 2),
        },
    )
    store.set_step(task_id, "prepare", "done")
    store.set_step_message(task_id, "prepare", "首帧和媒体信息已准备完成")
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
    return jsonify({"task_id": task["id"]}), 201


@bp.route("/api/subtitle-removal/<task_id>/artifact/source")
@login_required
def source_artifact(task_id: str):
    task = _get_owned_task(task_id)
    thumbnail_path = task.get("thumbnail_path") or ""
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        return "Not Found", 404
    return send_file(thumbnail_path)
```

```python
# appcore/tos_clients.py
def collect_task_tos_keys(task: dict | None) -> list[str]:
    if not task:
        return []

    keys: list[str] = []
    source_tos_key = (task.get("source_tos_key") or "").strip()
    result_tos_key = (task.get("result_tos_key") or "").strip()
    if source_tos_key:
        keys.append(source_tos_key)
    if result_tos_key:
        keys.append(result_tos_key)

    tos_uploads = task.get("tos_uploads") or {}
    if isinstance(tos_uploads, dict):
        for slot, payload in tos_uploads.items():
            if isinstance(payload, dict):
                tos_key = (payload.get("tos_key") or "").strip()
                if tos_key:
                    keys.append(tos_key)
            elif isinstance(slot, str) and slot.strip():
                keys.append(slot.strip())

    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped
```

- [ ] **Step 4: Re-export the state factory from the web facade**

```python
# web/store.py
from appcore.task_state import (
    confirm_alignment,
    confirm_segments,
    create,
    create_subtitle_removal,
    get,
    get_all,
    set_artifact,
    set_current_review_step,
    set_preview_file,
    set_step,
    set_step_message,
    set_variant_artifact,
    set_variant_preview_file,
    update,
    update_variant,
)

__all__ = [
    "confirm_alignment",
    "confirm_segments",
    "create",
    "create_subtitle_removal",
    "get",
    "get_all",
    "set_artifact",
    "set_current_review_step",
    "set_preview_file",
    "set_step",
    "set_step_message",
    "set_variant_artifact",
    "set_variant_preview_file",
    "update",
    "update_variant",
]
```

- [ ] **Step 5: Run the upload/state tests again**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_appcore_task_state.py tests/test_tos_clients.py -q -k "subtitle_removal or result_tos_key"`

Expected: PASS.

- [ ] **Step 6: Commit the media-preparation slice**

```bash
git add appcore/task_state.py appcore/tos_clients.py pipeline/ffutil.py web/store.py web/routes/subtitle_removal.py tests/test_subtitle_removal_routes.py tests/test_appcore_task_state.py tests/test_tos_clients.py
git commit -m "feat: add subtitle removal upload preparation flow"
```

---

## Task 3: Build the first-frame selection UI

**Files:**
- Modify: `web/templates/subtitle_removal_detail.html`
- Modify: `web/templates/_subtitle_removal_styles.html`
- Modify: `web/templates/_subtitle_removal_scripts.html`
- Modify: `web/routes/subtitle_removal.py`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_subtitle_removal_routes.py`

- [ ] **Step 1: Write the failing selector/UI tests**

```python
def test_subtitle_removal_detail_contains_selection_stage(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-selector",
        "uploads/source.mp4",
        "output/sr-selector",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-selector",
        status="ready",
        thumbnail_path="output/sr-selector/thumbnail.jpg",
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
    )
    row = {
        "id": "sr-selector",
        "user_id": 1,
        "type": "subtitle_removal",
        "display_name": "source",
        "original_filename": "source.mp4",
        "status": "ready",
        "deleted_at": None,
        "state_json": json.dumps(store.get("sr-selector"), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-selector")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "srSelectionOverlay" in body
    assert "computeSelectionBox" in body
    assert "提交去字幕任务" in body
```

```python
def test_subtitle_removal_state_api_returns_detail_payload(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-state",
        "uploads/source.mp4",
        "output/sr-state",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update("sr-state", status="ready", remove_mode="box")

    response = authed_client_no_db.get("/api/subtitle-removal/sr-state")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["id"] == "sr-state"
    assert payload["remove_mode"] == "box"
    assert "media_info" in payload
```

- [ ] **Step 2: Run the selector/UI tests and verify they fail**

Run: `pytest tests/test_web_routes.py tests/test_subtitle_removal_routes.py -q -k "selection_stage or state_api_returns_detail_payload"`

Expected: FAIL because the detail page does not yet render selector hooks and the JSON state endpoint does not exist.

- [ ] **Step 3: Implement the detail-state API and the first-frame box-selection UI**

```python
# web/routes/subtitle_removal.py
@bp.route("/api/subtitle-removal/<task_id>")
@login_required
def get_state(task_id: str):
    task = _get_owned_task(task_id)
    return jsonify(_serialize_task(task))
```

```html
<!-- web/templates/subtitle_removal_detail.html -->
<div class="sr-selection-shell">
  <div class="sr-selection-toolbar">
    <label class="sr-mode-pill"><input type="radio" name="remove_mode" value="full" checked> 全屏去除</label>
    <label class="sr-mode-pill"><input type="radio" name="remove_mode" value="box"> 框选去除</label>
  </div>
  <div class="sr-selection-stage" id="srSelectionStage">
    <img id="srFirstFrameImage" src="/api/subtitle-removal/{{ task_id }}/artifact/source" alt="视频首帧">
    <div id="srSelectionOverlay"></div>
    <div id="srSelectionHint">框选去除时，请在首帧上拖出字幕区域。</div>
  </div>
</div>
```

```javascript
// web/templates/_subtitle_removal_scripts.html
function computeSelectionBox(displayBox, naturalSize) {
  const x1 = Math.max(0, Math.round(displayBox.left / displayBox.renderWidth * naturalSize.width));
  const y1 = Math.max(0, Math.round(displayBox.top / displayBox.renderHeight * naturalSize.height));
  const x2 = Math.min(naturalSize.width, Math.round((displayBox.left + displayBox.width) / displayBox.renderWidth * naturalSize.width));
  const y2 = Math.min(naturalSize.height, Math.round((displayBox.top + displayBox.height) / displayBox.renderHeight * naturalSize.height));
  return {
    x1,
    y1,
    x2,
    y2,
    width: Math.max(0, x2 - x1),
    height: Math.max(0, y2 - y1),
  };
}

function syncModeUi(mode) {
  const overlay = document.getElementById("srSelectionOverlay");
  overlay.style.display = mode === "box" ? "block" : "none";
  document.body.dataset.srMode = mode;
}

async function refreshSubtitleRemovalState(taskId) {
  const response = await fetch(`/api/subtitle-removal/${taskId}`);
  const payload = await response.json();
  renderSubtitleRemovalState(payload);
}
```

```css
/* web/templates/_subtitle_removal_styles.html */
.sr-selection-stage {
  position: relative;
  border: 1px solid #dbe3ee;
  border-radius: 18px;
  overflow: hidden;
  background: #f8fbff;
}

#srSelectionOverlay {
  position: absolute;
  border: 2px solid #0ea5e9;
  background: rgba(14, 165, 233, 0.12);
  pointer-events: none;
}
```

- [ ] **Step 4: Run the selector/UI tests again**

Run: `pytest tests/test_web_routes.py tests/test_subtitle_removal_routes.py -q -k "selection_stage or state_api_returns_detail_payload"`

Expected: PASS.

- [ ] **Step 5: Commit the selection UI**

```bash
git add web/routes/subtitle_removal.py web/templates/subtitle_removal_detail.html web/templates/_subtitle_removal_styles.html web/templates/_subtitle_removal_scripts.html tests/test_web_routes.py tests/test_subtitle_removal_routes.py
git commit -m "feat: add subtitle removal first-frame selector"
```

---

## Task 4: Add provider configuration and API client

**Files:**
- Modify: `config.py`
- Modify: `appcore/settings.py`
- Create: `appcore/subtitle_removal_provider.py`
- Create: `tests/test_subtitle_removal_provider.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: Write the failing config/provider tests**

```python
def test_subtitle_removal_provider_submit_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-1"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("appcore.subtitle_removal_provider.requests.post", fake_post)
    monkeypatch.setattr("appcore.subtitle_removal_provider.config.SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr("appcore.subtitle_removal_provider.config.SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")

    task_id = submit_task(
        file_size_mb=2.09,
        duration_seconds=10.0,
        resolution="720x1280",
        video_name="sr_task_0_0_720_1280",
        source_url="https://tos.example/source.mp4",
    )

    assert task_id == "provider-task-1"
    assert captured["headers"]["authorization"] == "GOLDEN_demo"
    assert captured["json"]["biz"] == "aiRemoveSubtitleSubmitTask"
    assert captured["json"]["videoName"] == "sr_task_0_0_720_1280"
```

```python
def test_subtitle_removal_provider_progress_returns_first_item(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "msg": "ok",
                "data": [
                    {
                        "taskId": "provider-task-1",
                        "status": "success",
                        "resultUrl": "https://provider.example/result.mp4",
                        "position": "{\"l\":0,\"t\":0,\"w\":720,\"h\":1280}",
                    }
                ],
            }

    monkeypatch.setattr("appcore.subtitle_removal_provider.requests.post", lambda *args, **kwargs: FakeResponse())

    payload = query_progress("provider-task-1")

    assert payload["taskId"] == "provider-task-1"
    assert payload["status"] == "success"
    assert payload["resultUrl"].endswith("result.mp4")
```

```python
def test_config_exposes_subtitle_removal_provider_defaults(monkeypatch):
    monkeypatch.setenv("SUBTITLE_REMOVAL_PROVIDER_TOKEN", "token")
    monkeypatch.delenv("SUBTITLE_REMOVAL_PROVIDER_URL", raising=False)
    reloaded = importlib.reload(config)

    assert reloaded.SUBTITLE_REMOVAL_PROVIDER_URL == "https://goodline.simplemokey.com/api/openAi"
    assert reloaded.SUBTITLE_REMOVAL_POLL_FAST_SECONDS == 8
    assert reloaded.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS == 15
    assert reloaded.SUBTITLE_REMOVAL_MAX_DURATION_SECONDS == 600
```

```python
def test_project_type_labels_include_subtitle_removal():
    assert settings.PROJECT_TYPE_LABELS["subtitle_removal"] == "字幕移除"
```

- [ ] **Step 2: Run the config/provider tests and verify they fail**

Run: `pytest tests/test_subtitle_removal_provider.py tests/test_config.py tests/test_settings.py -q -k "subtitle_removal_provider or subtitle_removal_provider_defaults or project_type_labels_include_subtitle_removal"`

Expected: FAIL because the provider client/config keys do not exist yet.

- [ ] **Step 3: Add config entries, retention label support, and the provider client**

```python
# config.py
SUBTITLE_REMOVAL_PROVIDER_URL = _env("SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
SUBTITLE_REMOVAL_PROVIDER_TOKEN = _env("SUBTITLE_REMOVAL_PROVIDER_TOKEN")
SUBTITLE_REMOVAL_NOTIFY_URL = _env("SUBTITLE_REMOVAL_NOTIFY_URL")
SUBTITLE_REMOVAL_POLL_FAST_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_FAST_SECONDS", "8"))
SUBTITLE_REMOVAL_POLL_SLOW_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_SLOW_SECONDS", "15"))
SUBTITLE_REMOVAL_MAX_DURATION_SECONDS = int(_env("SUBTITLE_REMOVAL_MAX_DURATION_SECONDS", "600"))
```

```python
# appcore/settings.py
PROJECT_TYPE_LABELS: dict[str, str] = {
    "translation": "视频翻译（英文）",
    "de_translate": "视频翻译（德语）",
    "fr_translate": "视频翻译（法语）",
    "copywriting": "文案创作",
    "video_creation": "视频创作",
    "text_translate": "文案翻译",
    "subtitle_removal": "字幕移除",
}
```

```python
# appcore/subtitle_removal_provider.py
from __future__ import annotations

import requests

import config


class SubtitleRemovalProviderError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not config.SUBTITLE_REMOVAL_PROVIDER_TOKEN:
        raise SubtitleRemovalProviderError("SUBTITLE_REMOVAL_PROVIDER_TOKEN is not configured")
    return {"authorization": config.SUBTITLE_REMOVAL_PROVIDER_TOKEN}


def _post(payload: dict) -> dict:
    response = requests.post(
        config.SUBTITLE_REMOVAL_PROVIDER_URL,
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise SubtitleRemovalProviderError(data.get("msg") or "subtitle removal provider request failed")
    return data


def submit_task(*, file_size_mb: float, duration_seconds: float, resolution: str,
                video_name: str, source_url: str, cover_url: str = "") -> str:
    data = _post({
        "biz": "aiRemoveSubtitleSubmitTask",
        "fileSize": round(file_size_mb, 2),
        "duration": round(duration_seconds, 2),
        "resolution": resolution,
        "videoName": video_name,
        "coverUrl": cover_url,
        "url": source_url,
        "notifyUrl": config.SUBTITLE_REMOVAL_NOTIFY_URL,
    })
    payload = data.get("data")
    if isinstance(payload, dict) and payload.get("taskId"):
        return str(payload["taskId"])
    if isinstance(payload, list) and payload and payload[0].get("taskId"):
        return str(payload[0]["taskId"])
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    raise SubtitleRemovalProviderError("Provider submit response missing taskId")


def query_progress(task_id: str) -> dict:
    data = _post({"biz": "aiRemoveSubtitleProgress", "taskId": task_id})
    items = data.get("data") or []
    if not isinstance(items, list) or not items:
        raise SubtitleRemovalProviderError("Provider progress response missing data")
    return items[0]
```

- [ ] **Step 4: Run the config/provider tests again**

Run: `pytest tests/test_subtitle_removal_provider.py tests/test_config.py tests/test_settings.py -q -k "subtitle_removal_provider or subtitle_removal_provider_defaults or project_type_labels_include_subtitle_removal"`

Expected: PASS.

- [ ] **Step 5: Commit the provider slice**

```bash
git add config.py appcore/settings.py appcore/subtitle_removal_provider.py tests/test_subtitle_removal_provider.py tests/test_config.py tests/test_settings.py
git commit -m "feat: add subtitle removal provider client"
```

---

## Task 5: Implement submit API, background runtime, and Socket.IO updates

**Files:**
- Create: `appcore/subtitle_removal_runtime.py`
- Create: `web/services/subtitle_removal_runner.py`
- Modify: `web/routes/subtitle_removal.py`
- Modify: `tests/test_subtitle_removal_routes.py`
- Create: `tests/test_subtitle_removal_runtime.py`

- [ ] **Step 1: Write the failing submit/runtime tests**

```python
def test_subtitle_removal_submit_persists_mode_and_starts_runner(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-submit",
        "uploads/source.mp4",
        "output/sr-submit",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit",
        status="ready",
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = {}
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: started.setdefault("task_id", task_id))

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit/submit",
        json={"remove_mode": "box", "selection_box": {"x1": 0, "y1": 1000, "x2": 720, "y2": 1180}},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-submit"
    task = store.get("sr-submit")
    assert task["remove_mode"] == "box"
    assert task["steps"]["submit"] == "queued"
```

```python
def test_runtime_success_downloads_and_uploads_result(monkeypatch, tmp_path):
    task = task_state.create_subtitle_removal(
        "sr-runtime",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime",
        status="submitted",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime/source.mp4",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.generate_signed_download_url", lambda key, expires=None: "https://tos.example/source.mp4")
    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", lambda **kwargs: "provider-task-1")
    monkeypatch.setattr("appcore.subtitle_removal_runtime.query_progress", lambda task_id: {
        "taskId": task_id,
        "status": "success",
        "emsg": "成功",
        "resultUrl": "https://provider.example/result.mp4",
        "position": "{\"l\":0,\"t\":0,\"w\":720,\"h\":1280}",
    })
    monkeypatch.setattr("appcore.subtitle_removal_runtime._download_result_file", lambda url, path: str(tmp_path / "result.cleaned.mp4"))
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.upload_file", lambda local_path, object_key: None)
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.build_artifact_object_key", lambda user_id, task_id, variant, filename: f"artifacts/{user_id}/{task_id}/{variant}/{filename}")

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime")

    task = task_state.get("sr-runtime")
    assert task["status"] == "done"
    assert task["provider_task_id"] == "provider-task-1"
    assert task["result_tos_key"].endswith("result.cleaned.mp4")
```

- [ ] **Step 2: Run the submit/runtime tests and verify they fail**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py -q -k "submit_persists_mode_and_starts_runner or runtime_success_downloads_and_uploads_result"`

Expected: FAIL because the submit endpoint, runtime, and runner bridge do not exist yet.

- [ ] **Step 3: Extend the route helpers, add the submit endpoint, and add the background runtime**

```python
# web/routes/subtitle_removal.py
def _normalize_selection_box(mode: str, selection_box: dict | None, media_info: dict) -> dict:
    width = int(media_info.get("width") or 0)
    height = int(media_info.get("height") or 0)
    if mode == "full":
        return {"x1": 0, "y1": 0, "x2": width, "y2": height}
    if not selection_box:
        raise BadRequest("selection_box required for box mode")
    x1 = max(0, min(width, int(selection_box.get("x1") or 0)))
    y1 = max(0, min(height, int(selection_box.get("y1") or 0)))
    x2 = max(0, min(width, int(selection_box.get("x2") or 0)))
    y2 = max(0, min(height, int(selection_box.get("y2") or 0)))
    if x2 <= x1 or y2 <= y1:
        raise BadRequest("selection_box must have positive width and height")
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _to_position_payload(selection_box: dict) -> dict:
    return {
        "l": selection_box["x1"],
        "t": selection_box["y1"],
        "w": selection_box["x2"] - selection_box["x1"],
        "h": selection_box["y2"] - selection_box["y1"],
    }


def _serialize_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "status": task.get("status", ""),
        "display_name": task.get("display_name", ""),
        "original_filename": task.get("original_filename", ""),
        "steps": task.get("steps", {}),
        "step_messages": task.get("step_messages", {}),
        "media_info": task.get("media_info", {}),
        "remove_mode": task.get("remove_mode", ""),
        "selection_box": task.get("selection_box"),
        "provider_task_id": task.get("provider_task_id", ""),
        "provider_status": task.get("provider_status", ""),
        "provider_emsg": task.get("provider_emsg", ""),
        "result_tos_key": task.get("result_tos_key", ""),
        "error": task.get("error", ""),
    }


@bp.route("/api/subtitle-removal/<task_id>/submit", methods=["POST"])
@login_required
def submit(task_id: str):
    task = _get_owned_task(task_id)
    body = request.get_json(silent=True) or {}
    mode = (body.get("remove_mode") or "").strip()
    selection_box = body.get("selection_box")
    media_info = task.get("media_info") or {}
    if mode not in {"full", "box"}:
        return jsonify({"error": "remove_mode must be full or box"}), 400
    if mode == "box" and not selection_box:
        return jsonify({"error": "selection_box required for box mode"}), 400
    if float(media_info.get("duration") or 0.0) > config.SUBTITLE_REMOVAL_MAX_DURATION_SECONDS:
        return jsonify({"error": "video duration exceeds provider limit"}), 400

    normalized = _normalize_selection_box(mode, selection_box, media_info)
    store.update(
        task_id,
        status="queued",
        remove_mode=mode,
        selection_box=normalized,
        position_payload=_to_position_payload(normalized),
        provider_task_id="",
        provider_status="queued",
        provider_emsg="等待提交到第三方",
        provider_result_url="",
        result_video_path="",
        result_tos_key="",
        result_object_info={},
        error="",
    )
    store.set_step(task_id, "submit", "queued")
    store.set_step_message(task_id, "submit", "等待后台提交任务")
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202
```

```python
# web/services/subtitle_removal_runner.py
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runtime = SubtitleRemovalRuntime(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runtime.start, args=(task_id,), daemon=True)
    thread.start()
```

```python
# appcore/subtitle_removal_runtime.py
class SubtitleRemovalRuntime:
    def __init__(self, bus: EventBus, user_id: int | None = None):
        self._bus = bus
        self._user_id = user_id

    def start(self, task_id: str):
        task = task_state.get(task_id)
        if not task:
            return
        try:
            if not task.get("provider_task_id"):
                self._submit(task_id)
            self._poll_until_terminal(task_id)
        except Exception as exc:
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_step(task_id, "poll", "error")
            task_state.set_expires_at(task_id, "subtitle_removal")
            self._emit(EVT_SR_ERROR, task_id, {"message": str(exc)})

    def _submit(self, task_id: str):
        task = task_state.get(task_id)
        selection = task.get("selection_box") or {}
        video_name = f"sr_{task_id}_{selection['x1']}_{selection['y1']}_{selection['x2']}_{selection['y2']}"
        source_url = tos_clients.generate_signed_download_url(task["source_tos_key"], expires=86400)
        provider_task_id = submit_task(
            file_size_mb=float((task.get("media_info") or {}).get("file_size_mb") or 0.0),
            duration_seconds=float((task.get("media_info") or {}).get("duration") or 0.0),
            resolution=(task.get("media_info") or {}).get("resolution") or "",
            video_name=video_name,
            source_url=source_url,
        )
        task_state.update(
            task_id,
            status="running",
            provider_task_id=provider_task_id,
            provider_status="waiting",
            provider_emsg="第三方已接单",
        )
        task_state.set_step(task_id, "submit", "done")
        task_state.set_step(task_id, "poll", "running")
        self._emit(EVT_SR_STEP_UPDATE, task_id, {"step": "submit", "status": "done", "message": "第三方任务已提交"})
```

- [ ] **Step 4: Implement polling, result download, and result upload in the runtime**

```python
# appcore/subtitle_removal_runtime.py
def _poll_until_terminal(self, task_id: str):
    first_phase_deadline = time.time() + 60
    while True:
        task = task_state.get(task_id)
        progress = query_progress(task["provider_task_id"])
        status = (progress.get("status") or "").lower()
        task_state.update(
            task_id,
            provider_status=status,
            provider_emsg=progress.get("emsg") or "",
            provider_result_url=progress.get("resultUrl") or "",
            provider_raw=progress,
            last_polled_at=datetime.now().isoformat(timespec="seconds"),
            poll_attempts=int(task.get("poll_attempts") or 0) + 1,
        )
        self._emit(EVT_SR_STEP_UPDATE, task_id, {"step": "poll", "status": status, "message": progress.get("emsg") or status})

        if status == "success":
            self._download_and_upload_result(task_id, progress)
            return
        if status == "failed":
            raise SubtitleRemovalProviderError(progress.get("emsg") or "subtitle removal failed")

        sleep_seconds = config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS if time.time() < first_phase_deadline else config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS
        time.sleep(max(1, sleep_seconds))


def _download_and_upload_result(self, task_id: str, progress: dict):
    task = task_state.get(task_id)
    task_dir = task.get("task_dir") or ""
    local_result = os.path.join(task_dir, "result.cleaned.mp4")
    task_state.set_step(task_id, "download_result", "running")
    result_path = _download_result_file(progress.get("resultUrl") or "", local_result)
    task_state.update(task_id, result_video_path=result_path)
    task_state.set_step(task_id, "download_result", "done")

    task_state.set_step(task_id, "upload_result", "running")
    result_key = tos_clients.build_artifact_object_key(self._user_id or task.get("_user_id"), task_id, "subtitle_removal", "result.cleaned.mp4")
    tos_clients.upload_file(result_path, result_key)
    task_state.update(
        task_id,
        status="done",
        result_tos_key=result_key,
        result_object_info={"uploaded_at": datetime.now().isoformat(timespec="seconds")},
    )
    task_state.set_step(task_id, "upload_result", "done")
    task_state.set_expires_at(task_id, "subtitle_removal")
    self._emit(EVT_SR_DONE, task_id, {"task_id": task_id, "result_tos_key": result_key})
```

```python
# appcore/subtitle_removal_runtime.py
def _download_result_file(url: str, local_path: str) -> str:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    Path(local_path).write_bytes(response.content)
    return local_path
```

- [ ] **Step 5: Run the submit/runtime tests again**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py -q -k "submit_persists_mode_and_starts_runner or runtime_success_downloads_and_uploads_result"`

Expected: PASS.

- [ ] **Step 6: Commit the runtime slice**

```bash
git add appcore/subtitle_removal_runtime.py web/services/subtitle_removal_runner.py web/routes/subtitle_removal.py tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py
git commit -m "feat: add subtitle removal background runtime"
```

---

## Task 6: Add result preview, download, resume, resubmit, and delete flows

**Files:**
- Modify: `web/routes/subtitle_removal.py`
- Modify: `web/templates/subtitle_removal_detail.html`
- Modify: `web/templates/_subtitle_removal_scripts.html`
- Modify: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing result-action tests**

```python
def test_subtitle_removal_result_download_redirects_to_tos_when_local_file_missing(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-download",
        "uploads/source.mp4",
        "output/sr-download",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-download",
        status="done",
        result_tos_key="artifacts/1/sr-download/subtitle_removal/result.cleaned.mp4",
        result_video_path="",
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.generate_signed_download_url", lambda key, expires=None: "https://tos.example/result.cleaned.mp4")

    response = authed_client_no_db.get("/api/subtitle-removal/sr-download/download/result")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://tos.example/result.cleaned.mp4"
```

```python
def test_subtitle_removal_resubmit_clears_previous_provider_state(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-resubmit",
        "uploads/source.mp4",
        "output/sr-resubmit",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-resubmit",
        status="error",
        provider_task_id="provider-task-1",
        provider_status="failed",
        provider_result_url="https://provider.example/result.mp4",
        result_tos_key="artifacts/1/sr-resubmit/subtitle_removal/result.cleaned.mp4",
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = {}
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: started.setdefault("task_id", task_id))

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-resubmit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-resubmit"
    task = store.get("sr-resubmit")
    assert task["provider_task_id"] == ""
    assert task["result_tos_key"] == ""
    assert task["remove_mode"] == "full"
```

```python
def test_subtitle_removal_delete_soft_deletes_project_and_cleans_tos_keys(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-delete",
        "uploads/source.mp4",
        "output/sr-delete",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-delete",
        source_tos_key="uploads/1/sr-delete/source.mp4",
        result_tos_key="artifacts/1/sr-delete/subtitle_removal/result.cleaned.mp4",
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    deleted = []
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr("web.routes.subtitle_removal.db_execute", lambda *args, **kwargs: None)

    response = authed_client_no_db.delete("/api/subtitle-removal/sr-delete")

    assert response.status_code == 204
    assert deleted == [
        "uploads/1/sr-delete/source.mp4",
        "artifacts/1/sr-delete/subtitle_removal/result.cleaned.mp4",
    ]
```

- [ ] **Step 2: Run the result-action tests and verify they fail**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_web_routes.py -q -k "result_download_redirects_to_tos_when_local_file_missing or resubmit_clears_previous_provider_state or delete_soft_deletes_project_and_cleans_tos_keys"`

Expected: FAIL because the result/download/resubmit/delete endpoints do not exist yet.

- [ ] **Step 3: Implement result artifact, download, resume, resubmit, and delete endpoints**

```python
# web/routes/subtitle_removal.py
@bp.route("/api/subtitle-removal/<task_id>/artifact/result")
@login_required
def result_artifact(task_id: str):
    task = _get_owned_task(task_id)
    result_video_path = task.get("result_video_path") or ""
    if result_video_path and os.path.exists(result_video_path):
        return send_file(result_video_path)
    if task.get("result_tos_key"):
        return redirect(tos_clients.generate_signed_download_url(task["result_tos_key"]))
    return "Not Found", 404


@bp.route("/api/subtitle-removal/<task_id>/download/result")
@login_required
def download_result(task_id: str):
    task = _get_owned_task(task_id)
    result_video_path = task.get("result_video_path") or ""
    if result_video_path and os.path.exists(result_video_path):
        return send_file(result_video_path, as_attachment=True, download_name=f"{task.get('display_name') or task_id}.cleaned.mp4")
    if task.get("result_tos_key"):
        return redirect(tos_clients.generate_signed_download_url(task["result_tos_key"]))
    return "Not Found", 404


@bp.route("/api/subtitle-removal/<task_id>/resume-poll", methods=["POST"])
@login_required
def resume_poll(task_id: str):
    task = _get_owned_task(task_id)
    if not task.get("provider_task_id"):
        return jsonify({"error": "provider_task_id required"}), 400
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@bp.route("/api/subtitle-removal/<task_id>/resubmit", methods=["POST"])
@login_required
def resubmit(task_id: str):
    task = _get_owned_task(task_id)
    body = request.get_json(silent=True) or {}
    mode = (body.get("remove_mode") or task.get("remove_mode") or "").strip()
    normalized = _normalize_selection_box(mode, body.get("selection_box"), task.get("media_info") or {})
    store.update(
        task_id,
        status="queued",
        remove_mode=mode,
        selection_box=normalized,
        position_payload=_to_position_payload(normalized),
        provider_task_id="",
        provider_status="queued",
        provider_emsg="等待重新提交",
        provider_result_url="",
        provider_raw={},
        poll_attempts=0,
        last_polled_at=None,
        result_video_path="",
        result_tos_key="",
        result_object_info={},
        error="",
    )
    subtitle_removal_runner.start(task_id, user_id=current_user.id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@bp.route("/api/subtitle-removal/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    task = _get_owned_task(task_id)
    for object_key in tos_clients.collect_task_tos_keys(task):
        tos_clients.delete_object(object_key)
    db_execute("UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    return ("", 204)
```

- [ ] **Step 4: Wire the detail-page result actions and recovery buttons**

```javascript
// web/templates/_subtitle_removal_scripts.html
async function submitResubmission(taskId, payload) {
  const response = await fetch(`/api/subtitle-removal/${taskId}/resubmit`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("重新提交失败");
  return response.json();
}

async function resumeProviderPolling(taskId) {
  const response = await fetch(`/api/subtitle-removal/${taskId}/resume-poll`, {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken() },
  });
  if (!response.ok) throw new Error("继续轮询失败");
  return response.json();
}

function renderSubtitleRemovalState(task) {
  document.getElementById("srResultPanel").innerHTML = task.result_tos_key
    ? `<video controls src="/api/subtitle-removal/${task.id}/artifact/result"></video>
       <div class="sr-result-actions">
         <a class="btn btn-primary" href="/api/subtitle-removal/${task.id}/download/result">下载结果</a>
       </div>`
    : `<p class="sr-empty">结果生成后会在这里提供预览和下载。</p>
       ${task.provider_task_id && task.status !== "done" ? `<button type="button" class="btn btn-ghost" onclick="resumeProviderPolling('${task.id}')">继续轮询</button>` : ""}`;
}
```

- [ ] **Step 5: Run the result-action tests again**

Run: `pytest tests/test_subtitle_removal_routes.py tests/test_web_routes.py -q -k "result_download_redirects_to_tos_when_local_file_missing or resubmit_clears_previous_provider_state or delete_soft_deletes_project_and_cleans_tos_keys"`

Expected: PASS.

- [ ] **Step 6: Commit result actions**

```bash
git add web/routes/subtitle_removal.py web/templates/subtitle_removal_detail.html web/templates/_subtitle_removal_scripts.html tests/test_subtitle_removal_routes.py tests/test_web_routes.py
git commit -m "feat: add subtitle removal result actions"
```

---

## Task 7: Add startup recovery and regression coverage

**Files:**
- Modify: `web/app.py`
- Modify: `web/routes/subtitle_removal.py`
- Modify: `tests/test_subtitle_removal_runtime.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing recovery tests**

```python
def test_resume_inflight_tasks_requeues_polling_rows(monkeypatch):
    rows = [
        {
            "id": "sr-recover",
            "user_id": 1,
            "status": "running",
            "state_json": json.dumps({
                "id": "sr-recover",
                "type": "subtitle_removal",
                "status": "running",
                "provider_task_id": "provider-task-1",
                "steps": {
                    "prepare": "done",
                    "submit": "done",
                    "poll": "running",
                    "download_result": "pending",
                    "upload_result": "pending",
                },
            }, ensure_ascii=False),
        }
    ]
    started = []
    monkeypatch.setattr("web.routes.subtitle_removal.db_query", lambda sql, args=(): rows)
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: started.append((task_id, user_id)))

    resume_inflight_tasks()

    assert started == [("sr-recover", 1)]
    assert store.get("sr-recover")["provider_task_id"] == "provider-task-1"
```

```python
def test_create_app_triggers_subtitle_removal_recovery(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    called = []
    monkeypatch.setattr("web.app.resume_subtitle_removal_tasks", lambda: called.append(True))

    app = create_app()

    assert app is not None
    assert called == [True]
```

- [ ] **Step 2: Run the recovery tests and verify they fail**

Run: `pytest tests/test_subtitle_removal_runtime.py tests/test_web_routes.py -q -k "resume_inflight_tasks_requeues_polling_rows or triggers_subtitle_removal_recovery"`

Expected: FAIL because there is no restart recovery entrypoint yet.

- [ ] **Step 3: Implement restart recovery for in-flight subtitle-removal tasks**

```python
# web/routes/subtitle_removal.py
def resume_inflight_tasks() -> None:
    rows = db_query(
        "SELECT id, user_id, state_json FROM projects "
        "WHERE type = 'subtitle_removal' AND deleted_at IS NULL "
        "AND status IN ('queued', 'running', 'submitted')"
    )
    for row in rows:
        task = json.loads(row.get("state_json") or "{}")
        steps = task.get("steps") or {}
        inflight = (
            steps.get("submit") in {"queued", "running"}
            or steps.get("poll") in {"queued", "running"}
            or steps.get("download_result") == "running"
            or steps.get("upload_result") == "running"
        )
        if not inflight:
            continue
        task["_user_id"] = row["user_id"]
        store.get(task["id"])
        store.update(task["id"], **task)
        subtitle_removal_runner.start(task["id"], user_id=row["user_id"])
```

```python
# web/app.py
from web.routes.subtitle_removal import bp as subtitle_removal_bp, resume_inflight_tasks as resume_subtitle_removal_tasks

app.register_blueprint(subtitle_removal_bp)

if not app.testing and os.getenv("DISABLE_STARTUP_RECOVERY", "").lower() not in {"1", "true", "yes"}:
    try:
        resume_subtitle_removal_tasks()
    except Exception:
        app.logger.exception("subtitle-removal recovery failed during startup")
```

- [ ] **Step 4: Run the recovery tests again**

Run: `pytest tests/test_subtitle_removal_runtime.py tests/test_web_routes.py -q -k "resume_inflight_tasks_requeues_polling_rows or triggers_subtitle_removal_recovery"`

Expected: PASS.

- [ ] **Step 5: Commit restart recovery**

```bash
git add web/app.py web/routes/subtitle_removal.py tests/test_subtitle_removal_runtime.py tests/test_web_routes.py
git commit -m "feat: resume subtitle removal tasks on startup"
```

---

## Task 8: Final verification and release preparation

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-subtitle-removal-design.md` (only if implementation details changed)
- No code changes required if all previous tasks pass unchanged.

- [ ] **Step 1: Run the full subtitle-removal test suite**

Run:

```bash
pytest tests/test_web_routes.py tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_subtitle_removal_provider.py tests/test_appcore_task_state.py tests/test_tos_clients.py tests/test_config.py tests/test_settings.py -q
```

Expected: all subtitle-removal tests PASS.

- [ ] **Step 2: Run a manual smoke test against the local app**

Run:

```bash
set FLASK_SECRET_KEY=test-secret
set WTF_CSRF_ENABLED=0
@'
from web.app import create_app
app = create_app()
print(app.url_map)
'@ | python -
```

Manual checklist:
- Open `/subtitle-removal`.
- Upload a short MP4 through the direct-upload flow.
- Confirm the detail page shows the first frame.
- Submit one `全屏去除` job and one `框选去除` job.
- Refresh the page while the task is running and verify the state resumes.
- Verify the result panel plays the cleaned video and the download button works.

- [ ] **Step 3: Sync docs if the implementation deviated from the approved spec**

```bash
git diff -- docs/superpowers/specs/2026-04-15-subtitle-removal-design.md
```

Expected: no diff. If there is a necessary behavior change, update the spec before shipping.

- [ ] **Step 4: Publish**

```bash
bash deploy/publish.sh "feat: add subtitle removal module"
```

Expected: deployment script completes successfully and reports the new release revision.

- [ ] **Step 5: Final commit for doc sync (only if Step 3 changed docs)**

```bash
git add docs/superpowers/specs/2026-04-15-subtitle-removal-design.md
git commit -m "docs: sync subtitle removal spec after implementation"
```

---

## Self-Review Notes

### Spec coverage

- Upload page + detail page shell: Task 1.
- TOS direct upload, first-frame extraction, media probing: Task 2.
- `全屏去除` + `框选去除` first-frame selector: Task 3.
- Third-party submit/progress client: Task 4.
- Background submit/poll/download/upload pipeline independent of frontend: Task 5.
- Result preview/download plus resume/resubmit/delete actions: Task 6.
- Service restart recovery: Task 7.
- Menu icon `🧽`, project label, and release verification: Tasks 1, 4, and 8.

### Placeholder scan

- Searched for deferred-work markers and filler phrases, then removed any example wording that could be confused with a placeholder.
- The plan intentionally contains concrete file paths, commands, test names, payload shapes, helper names, and commit messages for every task.

### Type consistency

- Project type stays `subtitle_removal` everywhere: routes, state, settings, tests, and retention labels.
- Selection modes stay `full` and `box` everywhere: frontend radios, submit API, runtime normalization, and resubmit flow.
- Persisted provider fields stay `provider_task_id`, `provider_status`, `provider_emsg`, `provider_result_url`, and `provider_raw`.
- Result storage stays `result_video_path`, `result_tos_key`, and `result_object_info`.
