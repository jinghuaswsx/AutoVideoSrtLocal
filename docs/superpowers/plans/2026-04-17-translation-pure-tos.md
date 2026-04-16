# Translation Pure TOS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all new English, German, and French translation tasks use pure TOS upload and download flow, with no local final-download fallback for those new tasks.

**Architecture:** Mark new TOS-created translation tasks explicitly in task state, route all new task creation through `bootstrap/complete`, and make the shared translation download helper treat those marked tasks as TOS-only delivery. Legacy tasks remain readable without migration.

**Tech Stack:** Flask, in-memory task state mirrored to MySQL `state_json`, pytest, Volcengine TOS helpers, existing translation route blueprints.

---

### Task 1: Mark New Translation Tasks As Pure TOS And Close Legacy Upload Entrypoints

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `web/routes/tos_upload.py`
- Modify: `web/routes/task.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Test: `tests/test_tos_upload_routes.py`
- Test: `tests/test_security_upload_validation.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write failing tests for pure-TOS task markers and disabled local upload routes**

```python
def test_tos_upload_complete_marks_task_as_pure_tos(...):
    response = authed_client_no_db.post(
        "/api/tos-upload/complete",
        json={
            "task_id": "task-from-tos",
            "object_key": "uploads/1/task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 201
    task = store.get("task-from-tos")
    assert task["delivery_mode"] == "pure_tos"


def test_task_upload_route_rejects_local_translation_upload(client):
    response = client.post(
        "/api/tasks",
        data={"video": _make_file("test.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 410
    assert "TOS" in response.get_json()["error"]


def test_de_translate_start_upload_route_rejects_local_file_upload(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/de-translate/start",
        data={"video": (io.BytesIO(b"video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 410


def test_fr_translate_complete_marks_task_as_pure_tos(...):
    response = authed_client_no_db.post(
        "/api/fr-translate/complete",
        json={
            "task_id": "fr-task-from-tos",
            "object_key": "uploads/1/fr-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 201
    assert store.get("fr-task-from-tos")["delivery_mode"] == "pure_tos"
```

- [ ] **Step 2: Run targeted tests to verify they fail for the expected reason**

Run: `pytest tests/test_tos_upload_routes.py tests/test_security_upload_validation.py tests/test_web_routes.py -q`

Expected: failures showing missing `delivery_mode == "pure_tos"` and existing local upload routes still returning `201`.

- [ ] **Step 3: Add the pure-TOS task marker to task state and new-task complete handlers**

```python
def create(...):
    task = {
        ...
        "delivery_mode": "",
        "source_tos_key": "",
        ...
    }
```

```python
store.update(
    task_id,
    display_name=display_name,
    source_tos_key=object_key,
    source_object_info={
        "file_size": object_size,
        "content_type": content_type,
        "original_filename": original_filename,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    },
    delivery_mode="pure_tos",
)
```

```python
return jsonify(
    {
        "error": "新建翻译任务已切换为 TOS 直传，请先调用 bootstrap/complete 接口",
    }
), 410
```

- [ ] **Step 4: Run targeted tests to verify the new marker and route shutdown pass**

Run: `pytest tests/test_tos_upload_routes.py tests/test_security_upload_validation.py tests/test_web_routes.py -q`

Expected: the new pure-TOS assertions pass and local upload route assertions return `410`.

- [ ] **Step 5: Commit the task-creation changes**

```bash
git add appcore/task_state.py web/routes/tos_upload.py web/routes/task.py web/routes/de_translate.py web/routes/fr_translate.py tests/test_tos_upload_routes.py tests/test_security_upload_validation.py tests/test_web_routes.py
git commit -m "feat: mark new translation tasks as pure TOS"
```

### Task 2: Enforce TOS-Only Final Downloads For Pure-TOS Translation Tasks

**Files:**
- Modify: `web/services/artifact_download.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write failing tests for pure-TOS download behavior**

```python
def test_download_route_rejects_missing_tos_upload_for_pure_tos_task(tmp_path, authed_client_no_db):
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"capcut-archive")

    store.create("task-pure-tos-missing", "video.mp4", str(tmp_path), user_id=1)
    store.update(
        "task-pure-tos-missing",
        delivery_mode="pure_tos",
        display_name="example",
    )
    store.update_variant(
        "task-pure-tos-missing",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )

    response = authed_client_no_db.get("/api/tasks/task-pure-tos-missing/download/capcut?variant=normal")

    assert response.status_code == 409
    assert "TOS" in response.get_json()["error"]


def test_download_route_redirects_capcut_for_pure_tos_task(...):
    store.create("task-pure-tos-capcut", "video.mp4", str(tmp_path), user_id=1)
    store.update("task-pure-tos-capcut", delivery_mode="pure_tos", display_name="example")
    store.update_variant(
        "task-pure-tos-capcut",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )

    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *args, **kwargs: {
            "tos_key": "artifacts/1/task-pure-tos-capcut/normal/example_capcut_normal.zip",
        },
    )
    monkeypatch.setattr(
        "web.services.artifact_download.tos_clients.generate_signed_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )

    response = authed_client_no_db.get("/api/tasks/task-pure-tos-capcut/download/capcut?variant=normal")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://signed.example.com/artifacts/1/task-pure-tos-capcut/normal/example_capcut_normal.zip"
```

- [ ] **Step 2: Run download-focused tests to verify they fail before implementation**

Run: `pytest tests/test_web_routes.py -q`

Expected: failures showing pure-TOS tasks still falling back to local `send_file`.

- [ ] **Step 3: Teach the shared helper to treat pure-TOS tasks as TOS-only delivery**

```python
def _is_pure_tos_task(task: dict) -> bool:
    return (task.get("delivery_mode") or "").strip() == "pure_tos"
```

```python
if file_type == "capcut" and path:
    ...
    if upload_payload:
        return redirect(tos_clients.generate_signed_download_url(upload_payload["tos_key"]))
    if _is_pure_tos_task(task):
        return jsonify({"error": "CapCut 工程包尚未上传到 TOS，暂不可下载"}), 409
```

```python
if artifact_kind:
    uploaded_artifact = get_tos_upload_record(task, artifact_kind, variant)
    if uploaded_artifact:
        return redirect(tos_clients.generate_signed_download_url(uploaded_artifact["tos_key"]))
    if _is_pure_tos_task(task):
        return jsonify({"error": "下载文件尚未上传到 TOS，暂不可下载"}), 409
```

- [ ] **Step 4: Run the download-focused tests again**

Run: `pytest tests/test_web_routes.py -q`

Expected: pure-TOS tasks now redirect to signed TOS URLs or return explicit `409` instead of local file responses.

- [ ] **Step 5: Commit the download-helper changes**

```bash
git add web/services/artifact_download.py tests/test_web_routes.py
git commit -m "feat: enforce TOS-only downloads for new translation tasks"
```

### Task 3: Verify English, German, And French New-Task Flow End-To-End At Route Level

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_tos_upload_routes.py`
- Create or Modify: `tests/test_translation_tos_routes.py`

- [ ] **Step 1: Add route-level regression tests for all three translation modules**

```python
def test_english_complete_sets_pure_tos_and_source_metadata(...):
    ...


def test_de_complete_sets_pure_tos_and_source_metadata(...):
    ...


def test_fr_complete_sets_pure_tos_and_source_metadata(...):
    ...
```

```python
def test_pure_tos_delete_flow_collects_source_and_uploaded_artifact_keys(...):
    store.create("task-delete-pure-tos", "video.mp4", str(tmp_path), user_id=1)
    store.update(
        "task-delete-pure-tos",
        delivery_mode="pure_tos",
        source_tos_key="uploads/1/task-delete-pure-tos/source.mp4",
        tos_uploads={
            "normal:soft_video": {
                "tos_key": "artifacts/1/task-delete-pure-tos/normal/example_soft.mp4",
                "artifact_kind": "soft_video",
                "variant": "normal",
            }
        },
    )
    ...
```

- [ ] **Step 2: Run the consolidated regression suite**

Run: `pytest tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_security_upload_validation.py -q`

Expected: all pure-TOS creation, deletion, and download assertions pass together.

- [ ] **Step 3: Make any small naming or fixture consistency fixes required by the suite**

```python
assert task["delivery_mode"] == "pure_tos"
assert task["source_tos_key"] == expected_key
assert response.headers["Location"].startswith("https://signed.example.com/")
```

- [ ] **Step 4: Run the same consolidated suite again for final green verification**

Run: `pytest tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_security_upload_validation.py -q`

Expected: exit code `0` with all targeted pure-TOS translation tests passing.

- [ ] **Step 5: Commit the regression-test coverage**

```bash
git add tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_security_upload_validation.py tests/test_translation_tos_routes.py
git commit -m "test: cover pure TOS translation flow"
```
