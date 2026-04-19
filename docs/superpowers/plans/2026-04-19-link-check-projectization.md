# Link Check Projectization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `link-check` from a temporary single-page checker into a global project module with persistent intermediate state, project list/detail pages, failed-result highlighting, and URL-driven target-language auto-selection.

**Architecture:** Keep the existing Shopify locale-lock + image download + optional reference comparison + Gemini analysis pipeline, but make `link_check` a first-class persisted `projects` type. The list page becomes the project entry point, the detail page reuses the existing per-image result renderer, and all runtime updates flow through `appcore.task_state` so DB `state_json` stays current across refreshes and interrupted runs.

**Tech Stack:** Flask, Jinja, vanilla JS, existing `projects` table, `appcore.task_state`, existing `appcore.link_check_*` modules, pytest.

---

## File Structure

**Create**
- `appcore/link_check_locale.py`
  - Parse Shopify locale subdirectories like `/fr/` and `/fr-fr/`, normalize them against enabled `media_languages`, and build default link-check display names.
- `tests/test_link_check_locale.py`
  - Verify locale extraction, enabled-language matching, and display-name fallback behavior.
- `tests/test_link_check_project_routes.py`
  - Cover global list/detail/create/rename/delete behaviors for `link_check` projects.
- `web/static/link_check_projects.js`
  - Handle create-project form submission, URL-driven language auto-selection, and redirect-to-detail after creation.
- `web/templates/link_check_detail.html`
  - Dedicated persisted detail page shell that boots the stored task JSON and reuses the existing result renderer.

**Modify**
- `appcore/task_state.py`
  - Persist `link_check` tasks to `projects`, add project metadata (`display_name`, `steps`, `step_messages`), and keep `expires_at` empty for permanent retention.
- `appcore/link_check_runtime.py`
  - Update persisted steps/progress at each phase so partial work survives refreshes and interrupted runs.
- `appcore/task_recovery.py`
  - Treat interrupted `link_check` projects as recoverable persisted tasks and mark orphaned in-progress runs as `failed` while preserving partial items.
- `appcore/cleanup.py`
  - Exclude `link_check` from the permanent-project zombie cleanup path.
- `web/routes/link_check.py`
  - Replace user-owned temporary route behavior with global project list/detail rendering plus create/status/rename/delete APIs scoped to `type='link_check'`.
- `web/templates/link_check.html`
  - Convert the current single-page temporary checker into the project list + create-project page.
- `web/static/link_check.js`
  - Hydrate persisted detail data, poll while a project is still active, and highlight failed parameters/evidence in red with issue summaries.
- `web/static/link_check.css`
  - Add project-list layout styles, detail-page highlight styles, and alert emphasis that matches the Ocean Blue admin palette.
- `tests/test_appcore_task_state_db.py`
  - Assert `create_link_check()` writes persisted DB rows with `expires_at = NULL`.
- `tests/test_task_recovery.py`
  - Cover interrupted `link_check` recovery semantics.
- `tests/test_cleanup.py`
  - Verify zombie cleanup keeps `link_check` projects.
- `tests/test_link_check_runtime.py`
  - Verify runtime step/progress updates and persisted partial state.
- `tests/test_link_check_routes.py`
  - Keep API serialization coverage for image preview URLs and detail payload shape.
- `tests/test_link_check_ui_assets.py`
  - Assert target-language auto-detect hooks, project/detail shells, and failure highlight markers exist.

## Task 1: Add Shopify Locale Detection Helpers

**Files:**
- Create: `appcore/link_check_locale.py`
- Create: `tests/test_link_check_locale.py`

- [ ] **Step 1: Write the failing locale-detection tests**

```python
def test_detect_target_language_from_plain_locale_segment():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/fr/products/demo?variant=1",
        {"de", "fr", "ja"},
    ) == "fr"


def test_detect_target_language_falls_back_to_primary_subtag():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/fr-fr/products/demo",
        {"de", "fr", "ja"},
    ) == "fr"


def test_detect_target_language_returns_empty_when_segment_not_enabled():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/es/products/demo",
        {"de", "fr", "ja"},
    ) == ""


def test_build_display_name_prefers_product_handle_and_language():
    from appcore.link_check_locale import build_link_check_display_name

    assert build_link_check_display_name(
        "https://newjoyloo.com/fr/products/baseball-cap-organizer?variant=1",
        "fr",
    ) == "baseball-cap-organizer · FR"
```

- [ ] **Step 2: Run the focused locale test file and confirm the helper module does not exist yet**

Run: `pytest tests/test_link_check_locale.py -q`

Expected: `FAIL` with `ModuleNotFoundError: No module named 'appcore.link_check_locale'`.

- [ ] **Step 3: Implement locale parsing and default display-name helpers**

```python
from __future__ import annotations

from urllib.parse import urlparse


def _path_segments(link_url: str) -> list[str]:
    path = urlparse(link_url).path or ""
    return [segment.strip().lower() for segment in path.split("/") if segment.strip()]


def detect_target_language_from_url(link_url: str, enabled_codes: set[str]) -> str:
    for segment in _path_segments(link_url):
        if segment in enabled_codes:
            return segment
        if "-" in segment:
            primary = segment.split("-", 1)[0]
            if primary in enabled_codes:
                return primary
    return ""


def build_link_check_display_name(link_url: str, target_language: str) -> str:
    segments = _path_segments(link_url)
    handle = ""
    if "products" in segments:
        index = segments.index("products")
        if index + 1 < len(segments):
            handle = segments[index + 1]
    base = handle or urlparse(link_url).netloc or "link-check"
    suffix = (target_language or "").upper()
    return f"{base[:40]} · {suffix}" if suffix else base[:40]
```

- [ ] **Step 4: Re-run the locale helper tests**

Run: `pytest tests/test_link_check_locale.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Commit the locale helper task**

```bash
git add appcore/link_check_locale.py tests/test_link_check_locale.py
git commit -m "feat: add link check locale helpers"
```

## Task 2: Persist Link Check Projects and Protect Their Retention

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `appcore/task_recovery.py`
- Modify: `appcore/cleanup.py`
- Modify: `tests/test_appcore_task_state_db.py`
- Modify: `tests/test_task_recovery.py`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing persistence, recovery, and cleanup tests**

```python
def test_create_link_check_persists_to_db_with_null_expires_at(user_id, tmp_path):
    import appcore.task_state as ts
    from appcore.db import query_one

    ts.create_link_check(
        "test_ts_link_check",
        task_dir=str(tmp_path),
        user_id=user_id,
        link_url="https://newjoyloo.com/fr/products/demo",
        target_language="fr",
        target_language_name="法语",
        reference_images=[],
        display_name="demo · FR",
    )

    row = query_one("SELECT status, type, expires_at FROM projects WHERE id = %s", ("test_ts_link_check",))

    assert row["type"] == "link_check"
    assert row["status"] == "queued"
    assert row["expires_at"] is None


def test_recover_project_state_marks_orphaned_link_check_as_failed():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        "link_check",
        "lc-orphan",
        {
            "status": "analyzing",
            "steps": {"lock_locale": "done", "download": "done", "analyze": "running", "summarize": "pending"},
            "items": [{"id": "site-1", "status": "done"}],
        },
        active=False,
    )

    assert changed is True
    assert status == "failed"
    assert recovered["items"][0]["id"] == "site-1"
    assert recovered["steps"]["analyze"] == "error"


def test_run_cleanup_skips_link_check_from_null_expiry_cleanup(monkeypatch):
    import appcore.cleanup as cleanup

    captured_sql = []

    def fake_query(sql, args=()):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup.tos_clients, "is_tos_configured", lambda: False)

    cleanup.run_cleanup()

    zombie_sql = next(sql for sql in captured_sql if "expires_at IS NULL" in sql)
    assert "type NOT IN ('image_translate', 'link_check')" in zombie_sql
```

- [ ] **Step 2: Run the three focused backend test files and confirm the new expectations fail**

Run: `pytest tests/test_appcore_task_state_db.py tests/test_task_recovery.py tests/test_cleanup.py -q`

Expected: `FAIL` because `create_link_check()` does not persist, recovery does not handle `link_check`, and cleanup still only excludes `image_translate`.

- [ ] **Step 3: Make `link_check` a persisted permanent project type**

```python
def create_link_check(task_id: str, task_dir: str, *, user_id: int, link_url: str,
                      target_language: str, target_language_name: str,
                      reference_images: list[dict], display_name: str = "") -> dict:
    task = {
        "id": task_id,
        "type": "link_check",
        "status": "queued",
        "task_dir": task_dir,
        "display_name": display_name,
        "original_filename": "",
        "link_url": link_url,
        "resolved_url": "",
        "page_language": "",
        "target_language": target_language,
        "target_language_name": target_language_name,
        "reference_images": reference_images,
        "steps": {
            "lock_locale": "pending",
            "download": "pending",
            "analyze": "pending",
            "summarize": "pending",
        },
        "step_messages": {
            "lock_locale": "",
            "download": "",
            "analyze": "",
            "summarize": "",
        },
        "progress": {
            "total": 0,
            "downloaded": 0,
            "analyzed": 0,
            "compared": 0,
            "binary_checked": 0,
            "same_image_llm_done": 0,
            "failed": 0,
        },
        "summary": {
            "pass_count": 0,
            "no_text_count": 0,
            "replace_count": 0,
            "review_count": 0,
            "reference_unmatched_count": 0,
            "reference_matched_count": 0,
            "binary_checked_count": 0,
            "binary_direct_pass_count": 0,
            "binary_direct_replace_count": 0,
            "same_image_llm_done_count": 0,
            "same_image_llm_yes_count": 0,
            "overall_decision": "running",
        },
        "items": [],
        "error": "",
        "_user_id": user_id,
    }
    with _lock:
        _tasks[task_id] = task
    _db_upsert(task_id, user_id, task, "")
    return task
```

```python
LINK_CHECK_RUNNING_STATUSES = {"queued", "locking_locale", "downloading", "analyzing"}
RECOVERABLE_PROJECT_TYPES = {"video_creation", "video_review", "link_check"} | PIPELINE_PROJECT_TYPES

if project_type == "link_check" and recovered.get("status") in LINK_CHECK_RUNNING_STATUSES:
    changed = _mark_running_steps_as_error(recovered)
    recovered["status"] = "failed"
    recovered["error"] = RECOVERY_ERROR_MESSAGE
    return True, recovered, "failed"
```

```python
zombie_rows = query(
    "SELECT id, task_dir, user_id, state_json FROM projects "
    "WHERE expires_at IS NULL "
    "AND type NOT IN ('image_translate', 'link_check') "
    "AND status NOT IN ('uploaded', 'running') "
    "AND created_at < NOW() - INTERVAL 30 DAY "
    "AND deleted_at IS NULL"
)
```

- [ ] **Step 4: Re-run the persistence, recovery, and cleanup tests**

Run: `pytest tests/test_appcore_task_state_db.py tests/test_task_recovery.py tests/test_cleanup.py -q`

Expected: all selected tests `PASS`.

- [ ] **Step 5: Commit the persistence task**

```bash
git add appcore/task_state.py appcore/task_recovery.py appcore/cleanup.py tests/test_appcore_task_state_db.py tests/test_task_recovery.py tests/test_cleanup.py
git commit -m "feat: persist link check projects"
```

## Task 3: Add Global Link Check Project Routes

**Files:**
- Modify: `web/routes/link_check.py`
- Create: `tests/test_link_check_project_routes.py`
- Modify: `tests/test_link_check_routes.py`

- [ ] **Step 1: Write the failing global project route tests**

```python
def test_link_check_list_page_renders_global_projects(logged_in_client, monkeypatch):
    rows = [
        {"id": "lc-1", "display_name": "demo · FR", "status": "review_ready", "created_at": None, "updated_at": None,
         "state_json": '{"summary":{"overall_decision":"unfinished","replace_count":2}}'},
    ]
    monkeypatch.setattr("web.routes.link_check.db_query", lambda sql, args=(): rows)
    monkeypatch.setattr("web.routes.link_check.recover_all_interrupted_tasks", lambda: None)

    response = logged_in_client.get("/link-check")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "demo · FR" in html
    assert "新建检查项目" in html


def test_create_link_check_project_infers_target_language_when_omitted(authed_user_client_no_db, monkeypatch):
    created = {}
    monkeypatch.setattr("web.routes.link_check.medias.list_enabled_language_codes", lambda: ["de", "fr", "ja"])
    monkeypatch.setattr("web.routes.link_check.medias.get_language", lambda code: {"code": code, "name_zh": "法语", "enabled": 1})
    monkeypatch.setattr("web.routes.link_check.store.create_link_check", lambda task_id, task_dir, **kwargs: created.update(kwargs) or {"id": task_id})
    monkeypatch.setattr("web.routes.link_check.link_check_runner.start", lambda task_id: True)

    response = authed_user_client_no_db.post(
        "/api/link-check/tasks",
        data={"link_url": "https://newjoyloo.com/fr/products/demo", "target_language": ""},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert created["target_language"] == "fr"


def test_link_check_detail_page_loads_persisted_state_without_user_filter(logged_in_client, monkeypatch):
    row = {
        "id": "lc-1",
        "type": "link_check",
        "display_name": "demo · FR",
        "status": "review_ready",
        "state_json": '{"id":"lc-1","status":"review_ready","items":[],"summary":{"overall_decision":"unfinished"}}',
    }
    monkeypatch.setattr("web.routes.link_check.db_query_one", lambda sql, args=(): row)

    response = logged_in_client.get("/link-check/lc-1")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "demo · FR" in html
    assert '"overall_decision": "unfinished"' in html


def test_link_check_rename_and_delete_are_global(logged_in_client, monkeypatch):
    updates = []
    monkeypatch.setattr("web.routes.link_check.db_query_one", lambda sql, args=(): {"id": "lc-1", "task_dir": "", "state_json": "{}", "type": "link_check"})
    monkeypatch.setattr("web.routes.link_check.db_execute", lambda sql, args=(): updates.append((sql, args)))
    monkeypatch.setattr("web.routes.link_check.cleanup.delete_task_storage", lambda payload: None)

    rename_response = logged_in_client.patch("/api/link-check/tasks/lc-1", json={"display_name": "重命名项目"})
    delete_response = logged_in_client.delete("/api/link-check/tasks/lc-1")

    assert rename_response.status_code == 200
    assert delete_response.status_code == 200
    assert any("UPDATE projects SET display_name" in sql for sql, _ in updates)
    assert any("deleted_at" in sql for sql, _ in updates)
```

- [ ] **Step 2: Run the route-focused tests and confirm the current link-check routes are still temporary/user-owned**

Run: `pytest tests/test_link_check_project_routes.py tests/test_link_check_routes.py -q`

Expected: `FAIL` because `/link-check` still renders the temporary single-page shell, the create route still requires manual `target_language`, and rename/delete routes do not exist for `link_check`.

- [ ] **Step 3: Implement global list/detail/create/rename/delete routes for `type='link_check'`**

```python
@bp.route("/link-check")
@login_required
def page():
    recover_all_interrupted_tasks()
    rows = db_query(
        "SELECT id, display_name, status, created_at, updated_at, state_json "
        "FROM projects WHERE type = 'link_check' AND deleted_at IS NULL ORDER BY created_at DESC"
    )
    return render_template("link_check.html", projects=_decorate_projects(rows))


@bp.route("/link-check/<task_id>")
@login_required
def detail(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND type = 'link_check' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        abort(404)
    task = store.get(task_id) or json.loads(row.get("state_json") or "{}")
    return render_template(
        "link_check_detail.html",
        project=row,
        initial_task_json=json.dumps(task, ensure_ascii=False),
    )


@bp.route("/api/link-check/tasks", methods=["POST"])
@login_required
def create_task():
    link_url = (request.form.get("link_url") or "").strip()
    target_language = (request.form.get("target_language") or "").strip().lower()
    enabled_codes = set(medias.list_enabled_language_codes())
    if not target_language:
        target_language = detect_target_language_from_url(link_url, enabled_codes)
    if not link_url or not target_language:
        return jsonify({"error": "link_url 和 target_language 必填"}), 400
    language = medias.get_language(target_language)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target_language 非法"}), 400
    display_name = build_link_check_display_name(link_url, target_language)
    store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=target_language,
        target_language_name=language.get("name_zh") or target_language,
        reference_images=references,
        display_name=display_name,
    )
    link_check_runner.start(task_id)
    return jsonify({"task_id": task_id, "detail_url": url_for("link_check.detail", task_id=task_id)})
```

```python
@bp.route("/api/link-check/tasks/<task_id>", methods=["PATCH"])
@login_required
def rename_task(task_id: str):
    row = db_query_one("SELECT id FROM projects WHERE id=%s AND type='link_check' AND deleted_at IS NULL", (task_id,))
    if not row:
        return jsonify({"error": "Task not found"}), 404
    body = request.get_json(silent=True) or {}
    new_name = (body.get("display_name") or "").strip()
    if not new_name:
        return jsonify({"error": "display_name required"}), 400
    resolved = new_name[:50]
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (resolved, task_id))
    store.get(task_id)
    store.update(task_id, display_name=resolved)
    return jsonify({"status": "ok", "display_name": resolved})


@bp.route("/api/link-check/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: str):
    row = db_query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND type='link_check' AND deleted_at IS NULL",
        (task_id,),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404
    cleanup_payload = dict(store.get(task_id) or {})
    cleanup_payload["task_dir"] = row.get("task_dir") or ""
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup.delete_task_storage(cleanup_payload)
    db_execute("UPDATE projects SET deleted_at = NOW() WHERE id = %s", (task_id,))
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Re-run the route tests**

Run: `pytest tests/test_link_check_project_routes.py tests/test_link_check_routes.py -q`

Expected: all selected tests `PASS`.

- [ ] **Step 5: Commit the global route task**

```bash
git add web/routes/link_check.py tests/test_link_check_project_routes.py tests/test_link_check_routes.py
git commit -m "feat: add global link check project routes"
```

## Task 4: Persist Runtime Steps and Partial Link Check Progress

**Files:**
- Modify: `appcore/link_check_runtime.py`
- Modify: `tests/test_link_check_runtime.py`

- [ ] **Step 1: Write the failing runtime-persistence tests**

```python
def test_runtime_updates_steps_and_summary_for_persisted_project(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime
    from appcore import task_state

    task_state.create_link_check(
        "lc-runtime-steps",
        task_dir="scratch/runtime-steps",
        user_id=1,
        link_url="https://newjoyloo.com/fr/products/demo",
        target_language="fr",
        target_language_name="法语",
        reference_images=[],
        display_name="demo · FR",
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type("Page", (), {"resolved_url": url, "page_language": "fr", "images": []})()
        def download_images(self, images, task_dir):
            return []

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-runtime-steps")

    saved = task_state.get("lc-runtime-steps")
    assert saved["steps"]["lock_locale"] == "done"
    assert saved["steps"]["download"] == "done"
    assert saved["steps"]["analyze"] == "done"
    assert saved["steps"]["summarize"] == "done"
    assert saved["summary"]["overall_decision"] == "done"
```

- [ ] **Step 2: Run the runtime test file and confirm step bookkeeping is not complete yet**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: `FAIL` because `create_link_check()` and `LinkCheckRuntime.start()` do not yet maintain `steps` / `step_messages` through the full run.

- [ ] **Step 3: Update the runtime to keep persisted step/progress state current**

```python
task_state.update(task_id, status="locking_locale", error="")
task_state.set_step(task_id, "lock_locale", "running")
task_state.set_step_message(task_id, "lock_locale", "正在锁定目标语种页面")

page = self.fetcher.fetch_page(task["link_url"], task["target_language"])

task_state.set_step(task_id, "lock_locale", "done")
task_state.set_step(task_id, "download", "running")
task_state.set_step_message(task_id, "download", f"已抓取 {len(page.images)} 张候选图片")
downloaded = self.fetcher.download_images(page.images, task["task_dir"])
task_state.set_step(task_id, "download", "done")
task_state.set_step(task_id, "analyze", "running")
task_state.set_step_message(task_id, "analyze", f"正在分析 {len(downloaded)} 张图片")
for item in downloaded:
    result = {
        "id": item["id"],
        "kind": item["kind"],
        "source_url": item["source_url"],
        "_local_path": item["local_path"],
        "analysis": {},
        "reference_match": {"status": "not_provided", "score": 0.0},
        "binary_quick_check": _skipped_binary("未提供参考图，跳过二值快检"),
        "same_image_llm": _skipped_same_image("未提供参考图，跳过同图判断"),
        "status": "running",
        "error": "",
    }
    task["items"].append(result)
    task_state.update(task_id, items=task["items"], progress=task["progress"])
task_state.set_step(task_id, "analyze", "done")
task_state.set_step(task_id, "summarize", "running")
self._finalize(task)
task_state.set_step(task_id, "summarize", "done")
task_state.set_step_message(task_id, "summarize", "汇总完成")
```

```python
except Exception as exc:
    task_state.set_step(task_id, "summarize", "error")
    task_state.update(task_id, status="failed", error=str(exc))
```

- [ ] **Step 4: Re-run the runtime tests**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: all runtime tests `PASS`.

- [ ] **Step 5: Commit the runtime persistence task**

```bash
git add appcore/link_check_runtime.py tests/test_link_check_runtime.py
git commit -m "feat: persist link check runtime progress"
```

## Task 5: Build the Link Check Project List Page and URL-Driven Language Auto-Select

**Files:**
- Modify: `web/templates/link_check.html`
- Create: `web/static/link_check_projects.js`
- Modify: `web/static/link_check.css`
- Modify: `tests/test_link_check_ui_assets.py`

- [ ] **Step 1: Write the failing list-page asset tests**

```python
def test_link_check_assets_include_project_list_shell_and_create_form():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert 'id="linkCheckProjectForm"' in template
    assert 'id="linkCheckProjectList"' in template
    assert "detectTargetLanguageFromUrl" in script
    assert "window.location.assign" in script
    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
```

- [ ] **Step 2: Run the UI asset tests and confirm the new list page assets are still missing**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: `FAIL` because the current template still renders the old one-page checker and `web/static/link_check_projects.js` does not exist.

- [ ] **Step 3: Implement the project list + create page and client-side language auto-selection**

```html
<section class="lc-panel">
  <div class="lc-panel-head">
    <div>
      <h2>新建检查项目</h2>
      <p>输入 Shopify 小语种商品链接，系统会创建全局可见的链接检查项目。</p>
    </div>
  </div>

  <form id="linkCheckProjectForm" class="lc-form" enctype="multipart/form-data">
    <label class="lc-field">
      <span>检查链接</span>
      <input id="linkUrl" name="link_url" type="url" required>
    </label>
    <label class="lc-field">
      <span>目标语言</span>
      <select id="targetLanguage" name="target_language" data-autodetect="true"></select>
    </label>
    <label class="lc-field">
      <span>参考图片（可选）</span>
      <input id="referenceImages" name="reference_images" type="file" accept="image/jpeg,image/png,image/webp" multiple>
    </label>
    <div class="lc-actions">
      <button id="linkCheckSubmit" class="btn btn-primary" type="submit">创建项目</button>
      <div id="linkCheckStatus" class="lc-status-text">等待开始</div>
    </div>
  </form>
</section>

<section class="lc-panel">
  <div class="lc-panel-head">
    <div>
      <h2>项目列表</h2>
      <p>所有成员都可以看到这些链接检查项目，并继续跟进处理中间状态。</p>
    </div>
  </div>
  <div id="linkCheckProjectList" class="lc-project-list">
    {% for project in projects %}
      <a class="lc-project-card" href="{{ url_for('link_check.detail', task_id=project.id) }}">
        <div class="lc-project-card__title">{{ project.display_name or project.id }}</div>
        <div class="lc-project-card__meta">{{ project.status }}</div>
      </a>
    {% endfor %}
  </div>
</section>
```

```javascript
function detectTargetLanguageFromUrl(linkUrl, enabledCodes) {
  try {
    const url = new URL(linkUrl);
    const segments = url.pathname.split("/").filter(Boolean).map((segment) => segment.toLowerCase());
    for (const segment of segments) {
      if (enabledCodes.includes(segment)) return segment;
      if (segment.includes("-")) {
        const primary = segment.split("-", 1)[0];
        if (enabledCodes.includes(primary)) return primary;
      }
    }
  } catch {}
  return "";
}

linkUrlInput.addEventListener("input", function () {
  const detected = detectTargetLanguageFromUrl(linkUrlInput.value, enabledCodes);
  if (detected) targetLanguageSelect.value = detected;
});

const payload = await fetchJSON("/api/link-check/tasks", { method: "POST", body: new FormData(form) });
window.location.assign(payload.detail_url || `/link-check/${payload.task_id}`);
```

- [ ] **Step 4: Re-run the UI asset tests**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: the list-page asset tests `PASS`.

- [ ] **Step 5: Commit the project list page task**

```bash
git add web/templates/link_check.html web/static/link_check_projects.js web/static/link_check.css tests/test_link_check_ui_assets.py
git commit -m "feat: add link check project list page"
```

## Task 6: Add the Persisted Detail Page and Failure Highlighting

**Files:**
- Create: `web/templates/link_check_detail.html`
- Modify: `web/static/link_check.js`
- Modify: `web/static/link_check.css`
- Modify: `tests/test_link_check_ui_assets.py`

- [ ] **Step 1: Write the failing detail/highlight asset tests**

```python
def test_link_check_assets_include_detail_bootstrap_and_failure_highlights():
    template = Path("web/templates/link_check_detail.html").read_text(encoding="utf-8")
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert 'id="linkCheckDetailPage"' in template
    assert "__LINK_CHECK_TASK__" in template
    assert "collectIssueSummary(" in script
    assert "lc-meta-card--alert" in script
    assert ".lc-result-card--alert" in style
    assert ".lc-issue-summary" in style
```

- [ ] **Step 2: Run the detail UI asset tests and confirm the highlight markers are not in place yet**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: `FAIL` because the current detail view is still tied to the old single-page flow and does not emphasize failure evidence.

- [ ] **Step 3: Implement the persisted detail page, bootstrapped JSON hydration, and red failure emphasis**

```html
<div id="linkCheckDetailPage"
     data-task-id="{{ project.id }}"
     data-initial-task='{{ initial_task_json|tojson|safe }}'>
  <section id="linkCheckSummary" class="lc-panel lc-panel--summary"></section>
  <section id="linkCheckResults" class="lc-panel"></section>
</div>
<script>
  window.__LINK_CHECK_TASK__ = {{ initial_task_json|safe }};
</script>
<script src="{{ url_for('static', filename='link_check.js') }}"></script>
```

```javascript
function collectIssueSummary(item) {
  const issues = [];
  const analysis = item.analysis || {};
  const binary = item.binary_quick_check || {};
  const sameImage = item.same_image_llm || {};

  if (analysis.decision === "replace") issues.push("最终判定需替换");
  if (analysis.language_match === false) issues.push("识别语言不匹配");
  if (typeof analysis.quality_score === "number" && analysis.quality_score < 60) issues.push("质量分过低");
  if (binary.status === "fail") issues.push("二值快检未通过");
  if (typeof binary.foreground_overlap === "number" && typeof binary.threshold === "number" && binary.foreground_overlap < binary.threshold) {
    issues.push("前景重合度低于阈值");
  }
  if (sameImage.status === "done" && sameImage.answer === "不是") issues.push("大模型判断不是同图");

  return issues;
}

function buildMetaField(label, value, options) {
  const settings = options || {};
  return `
    <div class="lc-meta-card${settings.isAlert ? " lc-meta-card--alert" : ""}">
      <strong class="lc-meta-label">${escapeHtml(label)}</strong>
      <span class="lc-meta-value${settings.isAlert ? " lc-meta-value--alert" : ""}">${escapeHtml(value)}</span>
    </div>
  `;
}
```

```css
.lc-result-card--alert {
  border-color: var(--danger, oklch(58% 0.18 25));
  background: linear-gradient(180deg, rgba(255, 245, 245, 0.98), rgba(255, 255, 255, 1));
  box-shadow: 0 10px 22px -18px rgba(185, 28, 28, 0.42);
}

.lc-issue-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.lc-meta-card--alert {
  border-color: rgba(220, 38, 38, 0.28);
  background: rgba(255, 246, 246, 0.94);
}

.lc-meta-value--alert {
  color: var(--danger-fg, oklch(42% 0.14 25));
  font-size: 16px;
  font-weight: 700;
}
```

- [ ] **Step 4: Re-run the UI asset tests**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: all link-check UI asset tests `PASS`.

- [ ] **Step 5: Commit the detail/highlight task**

```bash
git add web/templates/link_check_detail.html web/static/link_check.js web/static/link_check.css tests/test_link_check_ui_assets.py
git commit -m "feat: highlight link check failures in detail view"
```

## Task 7: Verify the Full Link Check Project Flow End to End

**Files:**
- Modify: `tests/test_link_check_locale.py`
- Modify: `tests/test_appcore_task_state_db.py`
- Modify: `tests/test_task_recovery.py`
- Modify: `tests/test_cleanup.py`
- Modify: `tests/test_link_check_project_routes.py`
- Modify: `tests/test_link_check_routes.py`
- Modify: `tests/test_link_check_runtime.py`
- Modify: `tests/test_link_check_ui_assets.py`

- [ ] **Step 1: Run the combined focused suite for the whole module**

Run: `pytest tests/test_link_check_locale.py tests/test_appcore_task_state_db.py tests/test_task_recovery.py tests/test_cleanup.py tests/test_link_check_project_routes.py tests/test_link_check_routes.py tests/test_link_check_runtime.py tests/test_link_check_ui_assets.py -q`

Expected: every selected test passes.

- [ ] **Step 2: Run a syntax smoke check over the touched runtime and route files**

Run: `python -m py_compile appcore/link_check_locale.py appcore/task_state.py appcore/task_recovery.py appcore/cleanup.py appcore/link_check_runtime.py web/routes/link_check.py`

Expected: command exits `0` with no output.

- [ ] **Step 3: Commit any final test-fix adjustments**

```bash
git add tests/test_link_check_locale.py tests/test_appcore_task_state_db.py tests/test_task_recovery.py tests/test_cleanup.py tests/test_link_check_project_routes.py tests/test_link_check_routes.py tests/test_link_check_runtime.py tests/test_link_check_ui_assets.py
git commit -m "test: cover link check projectization flow"
```

## Spec Coverage Check

- [x] Global shared link-check project list/detail: Task 3 and Task 5.
- [x] Persistent intermediate state in `projects.state_json`: Task 2 and Task 4.
- [x] Failure parameter/evidence highlighting: Task 6.
- [x] URL-driven target-language auto-selection using `media_languages`: Task 1, Task 3, and Task 5.
- [x] Permanent retention and cleanup exclusion: Task 2.
- [x] Existing image preview / analysis API compatibility: Task 3 and Task 6.

