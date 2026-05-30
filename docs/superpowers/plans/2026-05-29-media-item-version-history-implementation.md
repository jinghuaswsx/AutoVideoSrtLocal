# Media Item Version History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Same product + same raw source + same target language keeps one current video card, archives overwritten video+cover versions, and lets admins delete current or historical versions without affecting unrelated materials.

**Architecture:** `media_items` remains the current-version table. New `media_item_versions` rows store overwritten video+cover metadata. Task-center manual video submission uses `product_id + source_raw_id + lang` to decide whether to create a new current card or archive-and-replace the existing one.

**Tech Stack:** Python 3.12, Flask routes/services, MySQL migrations, pytest, existing `web/static/medias.js` product edit UI.

---

### File Structure

- Create: `db/migrations/2026_05_29_media_item_versions.sql`
  Defines `media_item_versions` and indexes for item-level history lookup and source/lang lookup.
- Modify: `appcore/medias.py`
  Adds DAO helpers to archive current item metadata, replace the current video atomically, list active versions, count versions, and soft-delete a history version.
- Modify: `appcore/tasks.py`
  Changes manual translated-video submission from append-only to source-aware archive-and-replace.
- Modify: `web/routes/medias/_serializers.py`
  Adds `versions_count` to item JSON so the card can show the history entry only when needed.
- Modify: `web/services/media_product_detail.py`
  Loads per-item history counts in one call for product detail payloads.
- Modify: `web/services/media_items.py`
  Keeps current-item delete admin-aware and adds service helpers for listing/deleting versions.
- Modify: `web/routes/medias/items.py`
  Adds `GET /medias/api/items/<item_id>/versions` and `DELETE /medias/api/item-versions/<version_id>`.
- Modify: `web/routes/medias/__init__.py`
  Exposes the new item version route handlers from the medias blueprint module.
- Modify: `web/static/medias.js`
  Adds only a "历史版本" button to existing video cards and a modal for version listing/deletion.
- Test: `tests/test_db_migration_media_item_versions.py`
  Locks migration schema.
- Test: `tests/test_appcore_medias.py`
  Covers DAO archive/list/delete behavior.
- Test: `tests/test_appcore_tasks.py`
  Covers source-aware overwrite and different-source non-overwrite.
- Test: `tests/test_media_items_service.py`
  Covers current/history delete permissions and object key deletion sets.
- Test: `tests/test_medias_routes.py`
  Covers new route wiring and admin flag propagation.
- Test: `tests/test_medias_translation_assets.py`
  Covers front-end button/modal strings and endpoints.

### Task 1: Migration

**Files:**
- Create: `db/migrations/2026_05_29_media_item_versions.sql`
- Test: `tests/test_db_migration_media_item_versions.py`

- [ ] **Step 1: Write the failing migration test**

```python
from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_29_media_item_versions.sql")


def test_media_item_versions_migration_defines_history_table():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_item_versions" in body
    assert "media_item_id INT NOT NULL" in body
    assert "cover_object_key VARCHAR(500) DEFAULT NULL" in body
    assert "deleted_cover_object_key VARCHAR(500) DEFAULT NULL" in body
    assert "KEY idx_item_versions (media_item_id, deleted_at, version_no)" in body
    assert "KEY idx_source_lang_versions (product_id, source_raw_id, lang, deleted_at)" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_migration_media_item_versions.py -q`

Expected: FAIL because `db/migrations/2026_05_29_media_item_versions.sql` does not exist.

- [ ] **Step 3: Add migration**

```sql
CREATE TABLE IF NOT EXISTS media_item_versions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  media_item_id INT NOT NULL,
  product_id INT NOT NULL,
  lang VARCHAR(8) NOT NULL,
  source_raw_id INT DEFAULT NULL,
  version_no INT NOT NULL,
  filename VARCHAR(500) NOT NULL,
  display_name VARCHAR(255) DEFAULT NULL,
  object_key VARCHAR(500) NOT NULL,
  cover_object_key VARCHAR(500) DEFAULT NULL,
  file_url VARCHAR(1000) DEFAULT NULL,
  thumbnail_path VARCHAR(500) DEFAULT NULL,
  duration_seconds FLOAT DEFAULT NULL,
  file_size BIGINT DEFAULT NULL,
  task_id INT DEFAULT NULL,
  archived_by INT DEFAULT NULL,
  archived_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archive_reason VARCHAR(64) DEFAULT NULL,
  deleted_at DATETIME DEFAULT NULL,
  deleted_by INT DEFAULT NULL,
  deleted_object_key VARCHAR(500) DEFAULT NULL,
  deleted_cover_object_key VARCHAR(500) DEFAULT NULL,
  KEY idx_item_versions (media_item_id, deleted_at, version_no),
  KEY idx_source_lang_versions (product_id, source_raw_id, lang, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_migration_media_item_versions.py -q`

Expected: PASS.

### Task 2: DAO Version Helpers

**Files:**
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias.py`

- [ ] **Step 1: Write failing DAO tests**

Add focused monkeypatch tests that call:

```python
medias.find_current_item_by_source(product_id=9, lang="DE", source_raw_id=251)
medias.archive_and_replace_item_version(
    301,
    actor_user_id=2,
    filename="new.mp4",
    object_key="1/medias/new.mp4",
    display_name="new.mp4",
    file_size=456,
    task_id=44,
)
medias.list_item_versions(301)
medias.soft_delete_item_version(8, deleted_by=1)
```

Assert the SQL uses `product_id + lang + source_raw_id + deleted_at IS NULL`, archives old `object_key` and `cover_object_key`, clears current `cover_object_key` when replacing the video, returns version URLs from route serializers later, and records deleted video/cover keys on history deletion.

- [ ] **Step 2: Run failing DAO tests**

Run: `pytest tests/test_appcore_medias.py -q -k "item_version or current_item_by_source"`

Expected: FAIL because helper functions are missing.

- [ ] **Step 3: Implement DAO helpers**

Add:

```python
def find_current_item_by_source(*, product_id: int, lang: str, source_raw_id: int) -> dict | None: ...
def archive_and_replace_item_version(item_id: int, *, actor_user_id: int, filename: str, object_key: str, display_name: str | None, file_size: int | None, task_id: int | None, archive_reason: str = "manual_task_video_overwrite") -> dict: ...
def list_item_versions(item_id: int) -> list[dict]: ...
def count_item_versions(item_ids: list[int]) -> dict[int, int]: ...
def get_item_version(version_id: int) -> dict | None: ...
def soft_delete_item_version(version_id: int, *, deleted_by: int | None) -> dict | None: ...
```

`archive_and_replace_item_version` must use one connection, `conn.begin()`, `SELECT ... FOR UPDATE`, insert the old row into `media_item_versions`, update the same `media_items.id`, set `cover_object_key=NULL`, `thumbnail_path=NULL`, `duration_seconds=NULL`, then `conn.commit()`.

- [ ] **Step 4: Run DAO tests**

Run: `pytest tests/test_appcore_medias.py -q -k "item_version or current_item_by_source"`

Expected: PASS.

### Task 3: Task Center Manual Video Overwrite

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Update failing behavior tests**

Replace the append-only same-source test with:

```python
def test_submit_child_step_manual_output_archives_and_replaces_same_source_lang(monkeypatch):
    # Existing product_id=9, source_raw_id=251, lang=de item id=301 should be reused.
    # Assert medias.create_item is not called.
    # Assert medias.archive_and_replace_item_version is called with item_id=301.
    # Assert result["media_item_id"] == 301 and result["archived_version_id"] == 77.
```

Add:

```python
def test_submit_child_step_manual_output_creates_new_item_for_different_source(monkeypatch):
    # Existing item lookup for source_raw_id=252 returns None.
    # Assert medias.create_item is called once and source_raw_id is persisted to the new item.
```

- [ ] **Step 2: Run failing task tests**

Run: `pytest tests/test_appcore_tasks.py -q -k "manual_output_archives_and_replaces or creates_new_item_for_different_source"`

Expected: FAIL because manual video submission still creates a new item.

- [ ] **Step 3: Implement source-aware replace**

Inside `submit_child_step_manual_output`, for `kind == "video"`:

```python
source_raw_id = _source_raw_id_for_child_task_row(row)
existing_item = (
    medias.find_current_item_by_source(
        product_id=product_id,
        lang=lang,
        source_raw_id=source_raw_id,
    )
    if source_raw_id is not None
    else None
)
if existing_item:
    replacement = medias.archive_and_replace_item_version(
        int(existing_item["id"]),
        actor_user_id=int(actor_user_id),
        filename=filename,
        object_key=object_key,
        display_name=display_name,
        file_size=file_info.get("file_size"),
        task_id=int(task_id),
    )
    item_id = int(existing_item["id"])
    result["archived_version_id"] = replacement.get("version_id")
else:
    item_id = medias.create_item(...)
    if source_raw_id is not None:
        execute("UPDATE media_items SET source_raw_id=%s WHERE id=%s", ...)
```

Keep background thumbnail/cache refresh against the final `item_id`.

- [ ] **Step 4: Run task tests**

Run: `pytest tests/test_appcore_tasks.py -q -k "manual_output"`

Expected: PASS.

### Task 4: APIs and Delete Permissions

**Files:**
- Modify: `web/services/media_items.py`
- Modify: `web/routes/medias/items.py`
- Modify: `web/routes/medias/__init__.py`
- Test: `tests/test_media_items_service.py`
- Test: `tests/test_medias_routes.py`
- Test: `tests/characterization/test_medias_routes_baseline.py`

- [ ] **Step 1: Write failing service and route tests**

Add service tests for:

```python
build_item_versions_response(44, [{"id": 7, "media_item_id": 44, "object_key": "old.mp4", "cover_object_key": "old.jpg"}], is_admin=True)
build_item_version_delete_response(7, version_row, is_admin=False, soft_delete_item_version_fn=...)
build_item_version_delete_response(7, version_row, is_admin=True, soft_delete_item_version_fn=...)
```

Add route tests for:

```python
client.get("/medias/api/items/44/versions")
client.delete("/medias/api/item-versions/7")
```

Assert normal users get 403/409 for history deletion, admins get object keys for both video and cover, and current item deletion still does not cascade history.

- [ ] **Step 2: Run failing API tests**

Run: `pytest tests/test_media_items_service.py tests/test_medias_routes.py::test_item_versions_route_returns_versions tests/test_medias_routes.py::test_delete_item_version_route_deletes_video_and_cover -q`

Expected: FAIL because new service functions and routes are missing.

- [ ] **Step 3: Implement service and routes**

Service functions return `MediaItemResponse`:

```python
def build_item_versions_response(item_id: int, versions: list[dict], *, is_admin: bool) -> MediaItemResponse: ...
def build_item_version_delete_response(version_id: int, version: dict | None, *, is_admin: bool, soft_delete_item_version_fn=medias.soft_delete_item_version) -> MediaItemResponse: ...
```

Routes:

```python
@bp.route("/api/items/<int:item_id>/versions", methods=["GET"])
@login_required
def api_item_versions(item_id: int): ...

@bp.route("/api/item-versions/<int:version_id>", methods=["DELETE"])
@login_required
def api_delete_item_version(version_id: int): ...
```

Both routes must check product access through the owning current `media_items` row. Delete route must call `_delete_media_object` for version video and cover keys returned by the service.

- [ ] **Step 4: Run API tests**

Run: `pytest tests/test_media_items_service.py tests/test_medias_routes.py -q -k "item_version or item_delete_response or delete_item"`

Expected: PASS.

### Task 5: Product Payload Version Counts

**Files:**
- Modify: `web/services/media_product_detail.py`
- Modify: `web/routes/medias/_serializers.py`
- Test: `tests/test_media_product_detail_service.py`

- [ ] **Step 1: Write failing payload test**

```python
def test_product_detail_includes_item_versions_count():
    payload = build_product_detail_response(
        9,
        product={"id": 9, "name": "Demo"},
        list_items_fn=lambda pid: [{"id": 44, "product_id": 9, "lang": "fr", "filename": "v.mp4", "object_key": "k", "created_at": None}],
        count_item_versions_fn=lambda ids: {44: 2},
        ...
    )
    assert payload["items"][0]["versions_count"] == 2
```

- [ ] **Step 2: Run failing payload test**

Run: `pytest tests/test_media_product_detail_service.py -q -k versions_count`

Expected: FAIL because `count_item_versions_fn` is not accepted and serializer omits `versions_count`.

- [ ] **Step 3: Implement payload count**

Add `count_item_versions_fn=None` parameter to `build_product_detail_response`, defaulting to `medias.count_item_versions`, annotate each item dict with `versions_count`, and serialize:

```python
"versions_count": int(it.get("versions_count") or 0),
```

- [ ] **Step 4: Run payload test**

Run: `pytest tests/test_media_product_detail_service.py -q -k versions_count`

Expected: PASS.

### Task 6: Frontend History Button and Modal

**Files:**
- Modify: `web/static/medias.js`
- Test: `tests/test_medias_translation_assets.py`

- [ ] **Step 1: Write failing asset tests**

```python
def test_medias_js_has_item_history_entry_and_endpoints():
    source = Path("web/static/medias.js").read_text(encoding="utf-8")
    assert "data-act=\"history\"" in source
    assert "/medias/api/items/${itemId}/versions" in source
    assert "/medias/api/item-versions/${versionId}" in source
    assert "历史版本" in source
```

- [ ] **Step 2: Run failing asset test**

Run: `pytest tests/test_medias_translation_assets.py -q -k history`

Expected: FAIL because the strings do not exist.

- [ ] **Step 3: Implement minimal UI**

In `edRenderItems`, add only one button when `it.versions_count > 0`:

```javascript
${Number(it.versions_count || 0) > 0
  ? `<button class="oc-btn text sm" data-act="history">${icon('history', 12)}<span>历史版本 (${Number(it.versions_count || 0)})</span></button>`
  : ''}
```

Add `edOpenItemHistory(itemId)`, `edRenderItemHistoryModal(data)`, and `edDeleteItemVersion(versionId)` near item actions. Reuse existing modal patterns where available; otherwise create one overlay dynamically. History delete refreshes the modal and then product data so the count updates.

- [ ] **Step 4: Run frontend asset test**

Run: `pytest tests/test_medias_translation_assets.py -q -k history`

Expected: PASS.

### Task 7: Verification and Commit

**Files:**
- All changed files.

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
pytest tests/test_db_migration_media_item_versions.py `
  tests/test_appcore_medias.py `
  tests/test_appcore_tasks.py `
  tests/test_media_items_service.py `
  tests/test_media_product_detail_service.py `
  tests/test_medias_routes.py `
  tests/test_medias_translation_assets.py -q
```

Expected: PASS, with only existing unrelated warnings.

- [ ] **Step 2: Compile touched Python**

Run:

```powershell
python -m compileall appcore\medias.py appcore\tasks.py web\routes\medias web\services
```

Expected: PASS.

- [ ] **Step 3: Check diff hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; status contains only intended files.

- [ ] **Step 4: Commit**

Run:

```powershell
git add db\migrations\2026_05_29_media_item_versions.sql appcore\medias.py appcore\tasks.py web\routes\medias\_serializers.py web\routes\medias\__init__.py web\routes\medias\items.py web\services\media_product_detail.py web\services\media_items.py web\static\medias.js tests\test_db_migration_media_item_versions.py tests\test_appcore_medias.py tests\test_appcore_tasks.py tests\test_media_items_service.py tests\test_media_product_detail_service.py tests\test_medias_routes.py tests\test_medias_translation_assets.py
git commit -m "feat: archive overwritten media item versions"
```

Expected: commit succeeds on the worktree branch.

### Self-Review

- Spec coverage: same-source overwrite, different-source isolation, old video+cover archive, admin-only current/history delete, current delete no history cascade, history button/modal, and migration are all mapped to tasks.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" remains.
- Type consistency: helper names in tasks match the file structure and later route/service tests.
