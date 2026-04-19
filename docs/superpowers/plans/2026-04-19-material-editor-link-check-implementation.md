# Material Editor Link Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在素材管理编辑弹窗的单语种页面中集成现有链接检测能力，自动收集当前语种参考图，持久化检测任务，并在页面内展示最近检测结果与详情。

**Architecture:** 后端继续复用既有 `link_check` runtime 和任务结构，不另起一套检测逻辑；通过扩展 `projects.type` 和 `task_state.create_link_check` 让任务持久化，通过 `media_products.link_check_tasks_json` 保存“产品 + 语种”的最近一次关联任务摘要。前端只作为当前语种页面的触发器和摘要/详情展示层，所有检测细节仍由 `link_check` 任务提供。

**Tech Stack:** Flask blueprints, MySQL migrations, in-process task_state + `projects.state_json`, Vanilla JS, pytest

---

### Task 1: 扩展数据库与产品级链接检测关联存储

**Files:**
- Create: `db/migrations/2026_04_19_link_check_project_type.sql`
- Create: `db/migrations/2026_04_19_media_products_link_check_tasks.sql`
- Modify: `db/schema.sql`
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias.py`

- [ ] **Step 1: 先写失败测试，锁定 `link_check_tasks_json` 的读写行为**

```python
# tests/test_appcore_medias.py
def test_update_product_link_check_tasks_json(user_id):
    pid = medias.create_product(user_id, "link-check-task-json")
    try:
        payload = {
            "de": {
                "task_id": "task-de-1",
                "status": "review_ready",
                "link_url": "https://newjoyloo.com/de/products/demo",
                "checked_at": "2026-04-19T22:10:00",
                "summary": {
                    "overall_decision": "unfinished",
                    "pass_count": 3,
                    "replace_count": 1,
                    "review_count": 0,
                },
            }
        }

        medias.update_product(pid, link_check_tasks_json=payload)
        row = medias.get_product(pid)

        assert isinstance(row["link_check_tasks_json"], str)
        assert "task-de-1" in row["link_check_tasks_json"]
    finally:
        medias.soft_delete_product(pid)


def test_parse_link_check_tasks_json_handles_str_dict_and_none():
    assert medias.parse_link_check_tasks_json(None) == {}
    assert medias.parse_link_check_tasks_json("") == {}
    assert medias.parse_link_check_tasks_json({"de": {"task_id": "x"}}) == {"de": {"task_id": "x"}}
    assert medias.parse_link_check_tasks_json('{"de":{"task_id":"x"}}') == {"de": {"task_id": "x"}}
```

- [ ] **Step 2: 运行测试，确认当前实现还不支持这些字段**

Run: `pytest tests/test_appcore_medias.py -k "link_check_tasks_json" -q`  
Expected: FAIL，报 `update_product` 忽略 `link_check_tasks_json` 或 `parse_link_check_tasks_json` 不存在

- [ ] **Step 3: 增加迁移、schema 和 `appcore.medias` 读写能力**

```sql
-- db/migrations/2026_04_19_link_check_project_type.sql
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','translate_lab',
    'image_translate','subtitle_removal',
    'bulk_translate','copywriting_translate',
    'multi_translate','link_check'
  ) NOT NULL DEFAULT 'translation';
```

```sql
-- db/migrations/2026_04_19_media_products_link_check_tasks.sql
ALTER TABLE media_products
  ADD COLUMN link_check_tasks_json JSON NULL COMMENT '按语种保存最近一次链接检测任务摘要 {lang: payload}';
```

```python
# db/schema.sql
CREATE TABLE IF NOT EXISTS projects (
    id               VARCHAR(36) PRIMARY KEY,
    user_id          INT NOT NULL,
    type             ENUM(
      'translation','copywriting','video_creation','video_review',
      'text_translate','de_translate','fr_translate','subtitle_removal',
      'translate_lab','image_translate','bulk_translate',
      'copywriting_translate','multi_translate','link_check'
    ) NOT NULL DEFAULT 'translation',
    original_filename VARCHAR(255),
    display_name     VARCHAR(255),
    thumbnail_path   VARCHAR(512),
    status           VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    task_dir         VARCHAR(512),
    state_json       LONGTEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       DATETIME,
    deleted_at       DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

```python
# appcore/medias.py
def parse_link_check_tasks_json(value: str | dict | None) -> dict:
    import json as _json

    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except (_json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_product_link_check_tasks(product_id: int) -> dict:
    row = get_product(product_id) or {}
    return parse_link_check_tasks_json(row.get("link_check_tasks_json"))


def set_product_link_check_task(product_id: int, lang: str, payload: dict | None) -> int:
    tasks = get_product_link_check_tasks(product_id)
    if payload:
        tasks[lang] = payload
    else:
        tasks.pop(lang, None)
    return update_product(product_id, link_check_tasks_json=(tasks or None))
```

```python
# appcore/medias.py
def update_product(product_id: int, **fields) -> int:
    import json as _json
    allowed = {
        "name", "color_people", "source", "archived",
        "importance", "trend_score", "selling_points",
        "product_code", "cover_object_key",
        "localized_links_json", "ad_supported_langs",
        "link_check_tasks_json",
    }

    def _val(k):
        v = fields[k]
        if k in {"localized_links_json", "link_check_tasks_json"} and isinstance(v, dict):
            return _json.dumps(v, ensure_ascii=False)
        return v
```

- [ ] **Step 4: 重新运行测试，确认 DAO 已经可用**

Run: `pytest tests/test_appcore_medias.py -k "link_check_tasks_json" -q`  
Expected: PASS

- [ ] **Step 5: 提交数据库与 DAO 基础改动**

```bash
git add db/migrations/2026_04_19_link_check_project_type.sql db/migrations/2026_04_19_media_products_link_check_tasks.sql db/schema.sql appcore/medias.py tests/test_appcore_medias.py
git commit -m "feat: add product link check task storage"
```

### Task 2: 让 `link_check` 任务持久化到 `projects`

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `tests/test_appcore_task_state.py`
- Modify: `tests/test_appcore_task_state_db.py`

- [ ] **Step 1: 先写失败测试，证明 `create_link_check` 目前不会进 DB**

```python
# tests/test_appcore_task_state_db.py
def test_create_link_check_persists_to_db(user_id, tmp_path):
    task_id = "test_ts_link_check"
    task_dir = str(tmp_path / task_id)

    execute("DELETE FROM projects WHERE id = %s", (task_id,))

    ts.create_link_check(
        task_id,
        task_dir,
        user_id=user_id,
        link_url="https://newjoyloo.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    row = query_one("SELECT type, status, state_json FROM projects WHERE id = %s", (task_id,))
    assert row is not None
    assert row["type"] == "link_check"
    assert row["status"] == "queued"
```

```python
# tests/test_appcore_task_state.py
def test_create_link_check_initializes_summary_and_progress(tmp_path):
    task = ts.create_link_check(
        "lc-init-1",
        str(tmp_path / "lc-init-1"),
        user_id=1,
        link_url="https://newjoyloo.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    assert task["type"] == "link_check"
    assert task["status"] == "queued"
    assert task["progress"]["total"] == 0
    assert task["summary"]["overall_decision"] == "running"
    assert task.get("_persist_state") is not False
```

- [ ] **Step 2: 运行测试，确认现状失败**

Run: `pytest tests/test_appcore_task_state.py -k "create_link_check" -q`  
Run: `pytest tests/test_appcore_task_state_db.py -k "link_check" -q`  
Expected: FAIL，DB 中不存在 `projects` 记录，或 `_persist_state` 仍为 `False`

- [ ] **Step 3: 修改 `create_link_check`，去掉跳过持久化的分支**

```python
# appcore/task_state.py
def create_link_check(task_id: str, task_dir: str, *,
                      user_id: int,
                      link_url: str,
                      target_language: str,
                      target_language_name: str,
                      reference_images: list[dict]) -> dict:
    task = {
        "id": task_id,
        "type": "link_check",
        "status": "queued",
        "task_dir": task_dir,
        "link_url": link_url,
        "resolved_url": "",
        "page_language": "",
        "target_language": target_language,
        "target_language_name": target_language_name,
        "reference_images": reference_images,
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

- [ ] **Step 4: 重新跑测试，确认 `link_check` 已可恢复**

Run: `pytest tests/test_appcore_task_state.py -k "create_link_check" -q`  
Run: `pytest tests/test_appcore_task_state_db.py -k "link_check" -q`  
Expected: PASS

- [ ] **Step 5: 提交任务持久化改动**

```bash
git add appcore/task_state.py tests/test_appcore_task_state.py tests/test_appcore_task_state_db.py
git commit -m "feat: persist link check tasks in projects"
```

### Task 3: 新增素材页专用链接检测路由与参考图收集

**Files:**
- Modify: `web/routes/medias.py`
- Modify: `web/routes/link_check.py`
- Create: `tests/test_medias_link_check_routes.py`
- Modify: `tests/test_link_check_routes.py`

- [ ] **Step 1: 先写失败测试，描述素材页需要的路由行为**

```python
# tests/test_medias_link_check_routes.py
def test_create_material_link_check_task_collects_cover_and_detail_refs(logged_in_client, monkeypatch, tmp_path):
    from appcore import medias
    from appcore.db import execute as db_execute

    code = "mat-link-check-create"
    db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))
    pid = medias.create_product(1, "mat-link-check-create", product_code=code)
    medias.set_product_cover(pid, "de", "covers/de.jpg")
    detail_id = medias.add_detail_image(pid, "de", "details/de_1.jpg")

    monkeypatch.setattr("web.routes.medias.tos_clients.download_media_file", lambda key, path: str(path))
    monkeypatch.setattr("web.routes.medias.link_check_runner.start", lambda task_id: True)

    created = {}

    def fake_create(task_id, task_dir, **kwargs):
        created.update(kwargs)
        return {"id": task_id, "type": "link_check", "_user_id": 1}

    monkeypatch.setattr("web.routes.medias.store.create_link_check", fake_create)

    response = logged_in_client.post(
        f"/medias/api/products/{pid}/link-check",
        json={"lang": "de", "link_url": "https://newjoyloo.com/de/products/demo"},
    )

    assert response.status_code == 202
    assert created["target_language"] == "de"
    assert len(created["reference_images"]) == 2
    assert any(item["filename"] == "cover_de.jpg" for item in created["reference_images"])
    assert any(item["filename"].startswith("detail_") for item in created["reference_images"])
```

```python
# tests/test_medias_link_check_routes.py
def test_create_material_link_check_task_rejects_when_no_reference_images(logged_in_client, monkeypatch):
    from appcore import medias
    pid = medias.create_product(1, "mat-link-check-empty", product_code="mat-link-check-empty")

    monkeypatch.setattr("web.routes.medias.link_check_runner.start", lambda task_id: True)

    response = logged_in_client.post(
        f"/medias/api/products/{pid}/link-check",
        json={"lang": "de", "link_url": "https://newjoyloo.com/de/products/demo"},
    )

    assert response.status_code == 400
    assert "参考图" in response.get_json()["error"]
```

```python
# tests/test_medias_link_check_routes.py
def test_get_material_link_check_summary_uses_latest_associated_task(logged_in_client, monkeypatch):
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {
        "id": pid,
        "name": "demo",
        "product_code": "demo",
        "link_check_tasks_json": '{"de":{"task_id":"lc-1","status":"done","link_url":"https://x","checked_at":"2026-04-19T22:10:00","summary":{"overall_decision":"done","pass_count":2,"replace_count":0,"review_count":0}}}',
    })
    monkeypatch.setattr("web.routes.medias.store.get", lambda tid: {
        "id": tid,
        "type": "link_check",
        "_user_id": 1,
        "status": "done",
        "summary": {"overall_decision": "done", "pass_count": 2, "replace_count": 0, "review_count": 0},
        "progress": {"total": 2},
        "items": [],
        "error": "",
    })

    response = logged_in_client.get("/medias/api/products/7/link-check/de")
    payload = response.get_json()

    assert payload["task"]["task_id"] == "lc-1"
    assert payload["task"]["summary"]["pass_count"] == 2
    assert payload["task"]["has_detail"] is True
```

- [ ] **Step 2: 运行测试，确认这些接口当前不存在**

Run: `pytest tests/test_medias_link_check_routes.py -q`  
Expected: FAIL，路由 404 或缺少 helper / serializer

- [ ] **Step 3: 在 `medias` 路由中加入引用图收集、创建任务、查询摘要和详情**

```python
# web/routes/link_check.py
def serialize_link_check_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "link_url": task["link_url"],
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
        "target_language": task["target_language"],
        "target_language_name": task["target_language_name"],
        "progress": dict(task.get("progress") or {}),
        "summary": dict(task.get("summary") or {}),
        "error": task.get("error", ""),
        "reference_images": [
            {
                "id": ref["id"],
                "filename": ref["filename"],
                "preview_url": f"/api/link-check/tasks/{task['id']}/images/reference/{ref['id']}",
            }
            for ref in task.get("reference_images", [])
        ],
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "source_url": item["source_url"],
                "site_preview_url": f"/api/link-check/tasks/{task['id']}/images/site/{item['id']}",
                "analysis": dict(item.get("analysis") or {}),
                "reference_match": dict(item.get("reference_match") or {}),
                "binary_quick_check": dict(item.get("binary_quick_check") or {}),
                "same_image_llm": dict(item.get("same_image_llm") or {}),
                "status": item.get("status") or "pending",
                "error": item.get("error") or "",
            }
            for item in task.get("items", [])
        ],
    }
```

```python
# web/routes/medias.py
def _collect_link_check_reference_images(pid: int, lang: str, task_dir: Path) -> list[dict]:
    references: list[dict] = []
    ref_dir = task_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cover_key = medias.get_product_covers(pid).get(lang)
    if cover_key:
        cover_local = ref_dir / f"cover_{lang}{Path(cover_key).suffix or '.jpg'}"
        tos_clients.download_media_file(cover_key, cover_local)
        references.append({
            "id": f"cover-{lang}",
            "filename": f"cover_{lang}{cover_local.suffix}",
            "local_path": str(cover_local),
        })

    for idx, row in enumerate(medias.list_detail_images(pid, lang), start=1):
        local = ref_dir / f"detail_{idx:03d}{Path(row['object_key']).suffix or '.jpg'}"
        tos_clients.download_media_file(row["object_key"], local)
        references.append({
            "id": f"detail-{row['id']}",
            "filename": f"detail_{idx:03d}{local.suffix}",
            "local_path": str(local),
        })

    return references
```

```python
# web/routes/medias.py
@bp.route("/api/products/<int:pid>/link-check", methods=["POST"])
@login_required
def api_product_link_check_create(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400

    link_url = (body.get("link_url") or "").strip()
    if not link_url.startswith(("http://", "https://")):
        return jsonify({"error": "请先填写有效的商品链接"}), 400

    language = medias.get_language(lang)
    task_id = str(uuid.uuid4())
    task_dir = Path(OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    references = _collect_link_check_reference_images(pid, lang, task_dir)
    if not references:
        return jsonify({"error": "当前语种缺少参考图，至少需要主图或详情图之一"}), 400

    store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=lang,
        target_language_name=language.get("name_zh") or lang,
        reference_images=references,
    )
    medias.set_product_link_check_task(pid, lang, {
        "task_id": task_id,
        "status": "queued",
        "link_url": link_url,
        "checked_at": datetime.utcnow().isoformat(),
        "summary": {"overall_decision": "running", "pass_count": 0, "replace_count": 0, "review_count": 0},
    })
    link_check_runner.start(task_id)
    return jsonify({"task_id": task_id, "status": "queued", "reference_count": len(references)}), 202
```

```python
# web/routes/medias.py
@bp.route("/api/products/<int:pid>/link-check/<lang>", methods=["GET"])
@login_required
def api_product_link_check_get(pid: int, lang: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"task": None})
    task = store.get(meta["task_id"])
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"task": None})
    summary = serialize_link_check_task(task)
    medias.set_product_link_check_task(pid, lang, {
        **meta,
        "status": task.get("status", meta.get("status", "")),
        "summary": dict(task.get("summary") or meta.get("summary") or {}),
    })
    return jsonify({
        "task": {
            "task_id": meta["task_id"],
            "status": task.get("status", meta.get("status", "")),
            "link_url": meta.get("link_url", ""),
            "checked_at": meta.get("checked_at", ""),
            "summary": dict(task.get("summary") or meta.get("summary") or {}),
            "progress": dict(task.get("progress") or {}),
            "has_detail": True,
            "resolved_url": summary.get("resolved_url", ""),
            "page_language": summary.get("page_language", ""),
        }
    })
```

```python
# web/routes/medias.py
@bp.route("/api/products/<int:pid>/link-check/<lang>/detail", methods=["GET"])
@login_required
def api_product_link_check_detail(pid: int, lang: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"error": "task not found"}), 404
    task = store.get(meta["task_id"])
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"error": "task not found"}), 404
    return jsonify(serialize_link_check_task(task))
```

- [ ] **Step 4: 重新运行路由测试**

Run: `pytest tests/test_link_check_routes.py tests/test_medias_link_check_routes.py -q`  
Expected: PASS

- [ ] **Step 5: 提交素材页链接检测后端接口**

```bash
git add web/routes/medias.py web/routes/link_check.py tests/test_medias_link_check_routes.py tests/test_link_check_routes.py
git commit -m "feat: add media editor link check routes"
```

### Task 4: 在编辑弹窗中加入链接检测按钮、摘要和详情弹层

**Files:**
- Modify: `web/templates/_medias_edit_detail_modal.html`
- Modify: `web/static/medias.js`
- Modify: `tests/test_web_routes.py`
- Create: `tests/test_medias_link_check_ui_assets.py`

- [ ] **Step 1: 先写失败测试，描述模板与 JS 资产的新入口**

```python
# tests/test_web_routes.py
def test_medias_page_contains_edit_modal_link_check_entry(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")
    body = response.get_data(as_text=True)

    assert 'id="edProductLinkCheckBtn"' in body
    assert 'id="edProductLinkCheckStatus"' in body
    assert 'id="edProductLinkCheckSummary"' in body
    assert 'id="edProductLinkCheckDetailMask"' in body
```

```python
# tests/test_medias_link_check_ui_assets.py
from pathlib import Path


def test_medias_link_check_js_contains_lang_scoped_polling_and_stale_hint():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "function edLoadLinkCheckState" in script
    assert "function edStartProductLinkCheck" in script
    assert "function edOpenProductLinkCheckDetail" in script
    assert "当前链接已修改" in script
    assert "edLinkCheckPollTimer" in script
```

- [ ] **Step 2: 运行测试，确认当前模板/脚本还没有这些节点**

Run: `pytest tests/test_web_routes.py -k "edit_modal_link_check_entry" -q`  
Run: `pytest tests/test_medias_link_check_ui_assets.py -q`  
Expected: FAIL

- [ ] **Step 3: 在模板和脚本里增加入口、摘要和详情渲染逻辑**

```html
<!-- web/templates/_medias_edit_detail_modal.html -->
<div class="oc-field" style="margin-top:var(--oc-sp-3)">
  <label class="oc-label" for="edProductUrl">
    商品链接 <span class="oc-hint" id="edProductUrlHint" style="font-weight:400;"></span>
  </label>
  <div class="oc-inline-input-row">
    <input id="edProductUrl" class="oc-input" type="url" placeholder="留空则用默认模板" autocomplete="off">
    <button type="button" class="oc-btn ghost" id="edProductLinkCheckBtn">链接检测</button>
    <span id="edProductLinkCheckStatus" class="oc-badge neutral">未检测</span>
  </div>
  <div id="edProductLinkCheckSummary" class="oc-link-check-summary" hidden></div>
</div>

<div class="oc-modal-mask oc" id="edProductLinkCheckDetailMask" hidden>
  <div class="oc-modal oc-modal-narrow" role="dialog" aria-modal="true" aria-labelledby="edProductLinkCheckDetailTitle">
    <div class="oc-modal-head">
      <h3 id="edProductLinkCheckDetailTitle">链接检测详情</h3>
      <button class="oc-icon-btn" id="edProductLinkCheckDetailClose" title="关闭" aria-label="关闭">
        <svg width="16" height="16"><use href="#ic-close"/></svg>
      </button>
    </div>
    <div class="oc-modal-body">
      <div id="edProductLinkCheckDetailContent"></div>
    </div>
  </div>
</div>
```

```javascript
// web/static/medias.js
let edLinkCheckPollTimer = null;

function edClearLinkCheckPoll() {
  if (edLinkCheckPollTimer) {
    clearTimeout(edLinkCheckPollTimer);
    edLinkCheckPollTimer = null;
  }
}

function edCurrentProductLinkValue() {
  edFlushProductUrl();
  const lang = edState.activeLang;
  const links = ((edState.productData || {}).product || {}).localized_links || {};
  return (links[lang] || _defaultProductUrl(lang, ($('edCode').value || '').trim())).trim();
}

function edRenderLinkCheckSummary(lang, task) {
  const box = $('edProductLinkCheckSummary');
  const status = $('edProductLinkCheckStatus');
  if (!box || !status) return;

  if (!task) {
    status.textContent = '未检测';
    status.className = 'oc-badge neutral';
    box.hidden = true;
    box.innerHTML = '';
    return;
  }

  const summary = task.summary || {};
  const currentUrl = edCurrentProductLinkValue();
  const stale = currentUrl && task.link_url && currentUrl !== task.link_url;
  const overall = summary.overall_decision || '';
  const statusText = task.status === 'failed'
    ? '失败'
    : (overall === 'done' ? '通过' : (task.status === 'review_ready' || overall === 'unfinished' ? '待复核' : '检测中'));

  status.textContent = statusText;
  status.className = `oc-badge ${statusText === '通过' ? 'success' : (statusText === '失败' ? 'danger' : (statusText === '待复核' ? 'warning' : 'info'))}`;

  box.hidden = false;
  box.innerHTML = `
    ${stale ? '<div class="oc-link-check-stale">当前链接已修改，下面是旧链接的检测结果，建议重新检测</div>' : ''}
    <div class="oc-link-check-summary-grid">
      <div>最近检测：${escapeHtml(task.checked_at || '-')}</div>
      <div>抓取图片：${escapeHtml((task.progress || {}).total ?? 0)}</div>
      <div>通过：${escapeHtml(summary.pass_count ?? 0)}</div>
      <div>待替换：${escapeHtml(summary.replace_count ?? 0)}</div>
      <div>待复核：${escapeHtml(summary.review_count ?? 0)}</div>
    </div>
    <div class="oc-link-check-summary-actions">
      <button type="button" class="oc-btn text sm" id="edProductLinkCheckDetailBtn">查看详情</button>
    </div>
  `;

  $('edProductLinkCheckDetailBtn')?.addEventListener('click', () => edOpenProductLinkCheckDetail(lang));
}

async function edLoadLinkCheckState(lang) {
  const pid = edState.productData && edState.productData.product && edState.productData.product.id;
  if (!pid) return;
  const data = await fetchJSON(`/medias/api/products/${pid}/link-check/${lang}`);
  edRenderLinkCheckSummary(lang, data.task);
  if (data.task && !['done', 'review_ready', 'failed'].includes(data.task.status)) {
    edClearLinkCheckPoll();
    edLinkCheckPollTimer = setTimeout(() => edLoadLinkCheckState(lang), 1000);
  }
}

async function edStartProductLinkCheck() {
  const pid = edState.productData && edState.productData.product && edState.productData.product.id;
  const lang = edState.activeLang;
  const linkUrl = edCurrentProductLinkValue();
  if (!pid || !linkUrl) {
    alert('请先填写商品链接');
    return;
  }

  $('edProductLinkCheckBtn').disabled = true;
  try {
    await fetchJSON(`/medias/api/products/${pid}/link-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lang, link_url: linkUrl }),
    });
    await edLoadLinkCheckState(lang);
  } catch (e) {
    alert('链接检测启动失败：' + (e.message || ''));
  } finally {
    $('edProductLinkCheckBtn').disabled = false;
  }
}

async function edOpenProductLinkCheckDetail(lang) {
  const pid = edState.productData && edState.productData.product && edState.productData.product.id;
  const data = await fetchJSON(`/medias/api/products/${pid}/link-check/${lang}/detail`);
  $('edProductLinkCheckDetailMask').hidden = false;
  $('edProductLinkCheckDetailContent').innerHTML = (data.items || []).map((item, index) => `
    <div class="oc-link-check-detail-card">
      <div>#${index + 1} ${escapeHtml(item.kind || '-')}</div>
      <div>结论：${escapeHtml((item.analysis || {}).decision || item.status || '-')}</div>
      <div>来源：${escapeHtml(item.source_url || '-')}</div>
    </div>
  `).join('') || '<div class="oc-empty">暂无检测结果</div>';
}
```

- [ ] **Step 4: 接入编辑弹窗生命周期，保证语种切换和弹窗关闭时清理轮询**

```javascript
// web/static/medias.js
async function openEditDetail(pid) {
  const full = await fetchJSON('/medias/api/products/' + pid);
  edState.current = full;
  edState.productData = full;
  edState.activeLang = 'en';
  $('edMask').hidden = false;
  edRenderLangTabs();
  edRenderActiveLangView();
  await edLoadLinkCheckState(edState.activeLang);
}

function edSwitchLang(lang) {
  edFlushCopywritings();
  edFlushProductUrl();
  edClearLinkCheckPoll();
  edState.activeLang = lang;
  edResetNewItemForm();
  edRenderLangTabs();
  edRenderActiveLangView();
  edLoadLinkCheckState(lang);
}

function edHide() {
  edClearLinkCheckPoll();
  $('edProductLinkCheckDetailMask') && ($('edProductLinkCheckDetailMask').hidden = true);
  $('edMask').hidden = true;
  edState.current = null;
  edState.productData = null;
}

document.addEventListener('DOMContentLoaded', () => {
  $('edProductLinkCheckBtn')?.addEventListener('click', edStartProductLinkCheck);
  $('edProductLinkCheckDetailClose')?.addEventListener('click', () => {
    $('edProductLinkCheckDetailMask').hidden = true;
  });
});
```

- [ ] **Step 5: 跑前端静态测试并提交**

Run: `pytest tests/test_web_routes.py -k "link_check_entry" -q`  
Run: `pytest tests/test_medias_link_check_ui_assets.py -q`  
Expected: PASS

```bash
git add web/templates/_medias_edit_detail_modal.html web/static/medias.js tests/test_web_routes.py tests/test_medias_link_check_ui_assets.py
git commit -m "feat: add link check UI to media editor"
```

### Task 5: 汇总验证并修正回归

**Files:**
- Modify: `web/routes/medias.py`（如需补齐序列化）
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_medias_link_check_routes.py`
- Modify: `tests/test_appcore_task_state_db.py`

- [ ] **Step 1: 增加一个失败测试，确保 `GET /medias/api/products/<pid>` 会返回 `product.link_check_tasks`**

```python
# tests/test_medias_link_check_routes.py
def test_get_product_detail_includes_link_check_tasks(logged_in_client):
    from appcore import medias
    pid = medias.create_product(1, "mat-link-check-full", product_code="mat-link-check-full")
    medias.update_product(pid, link_check_tasks_json={
        "de": {
            "task_id": "lc-123",
            "status": "done",
            "link_url": "https://newjoyloo.com/de/products/demo",
            "checked_at": "2026-04-19T22:10:00",
            "summary": {"overall_decision": "done", "pass_count": 2, "replace_count": 0, "review_count": 0},
        }
    })

    response = logged_in_client.get(f"/medias/api/products/{pid}")
    payload = response.get_json()

    assert payload["product"]["link_check_tasks"]["de"]["task_id"] == "lc-123"
```

- [ ] **Step 2: 运行整组测试，找到遗漏**

Run: `pytest tests/test_appcore_medias.py tests/test_appcore_task_state.py tests/test_appcore_task_state_db.py tests/test_link_check_routes.py tests/test_medias_link_check_routes.py tests/test_medias_link_check_ui_assets.py tests/test_web_routes.py -q`  
Expected: 至少一处 FAIL，提示 `_serialize_product` 还没暴露 `link_check_tasks` 或素材页序列化不完整

- [ ] **Step 3: 补齐产品序列化并统一字段**

```python
# web/routes/medias.py
def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None,
                       covers: dict[str, str] | None = None) -> dict:
    if covers is None:
        covers = medias.get_product_covers(p["id"])
    has_en_cover = "en" in covers
    cover_url = f"/medias/cover/{p['id']}?lang=en" if has_en_cover else (
        f"/medias/thumb/{cover_item_id}" if cover_item_id else None
    )
    raw_links = p.get("localized_links_json")
    localized_links: dict = {}
    if isinstance(raw_links, dict):
        localized_links = raw_links
    elif isinstance(raw_links, str):
        try:
            parsed = json.loads(raw_links)
            if isinstance(parsed, dict):
                localized_links = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    link_check_tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "has_en_cover": has_en_cover,
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "ad_supported_langs": p.get("ad_supported_langs") or "",
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "items_filenames": items_filenames or [],
        "cover_thumbnail_url": cover_url,
        "lang_coverage": lang_coverage or {},
        "localized_links": localized_links,
        "link_check_tasks": link_check_tasks,
    }
```

- [ ] **Step 4: 重新跑完整验证**

Run: `pytest tests/test_appcore_medias.py tests/test_appcore_task_state.py tests/test_appcore_task_state_db.py tests/test_link_check_routes.py tests/test_medias_link_check_routes.py tests/test_medias_link_check_ui_assets.py tests/test_web_routes.py -q`  
Run: `python -m py_compile appcore\medias.py appcore\task_state.py web\routes\medias.py web\routes\link_check.py`  
Expected: 全部 PASS；`py_compile` 无报错

- [ ] **Step 5: 提交收尾验证改动**

```bash
git add web/routes/medias.py tests/test_medias_link_check_routes.py tests/test_web_routes.py tests/test_appcore_task_state_db.py
git commit -m "test: cover media editor link check flow"
```

---

**Plan self-review**

- Spec coverage:
  - 编辑页按钮、状态、摘要、详情入口：Task 4 覆盖
  - 自动收集当前语种主图和详情图：Task 3 覆盖
  - `link_check` 任务持久化到 `projects`：Task 2 覆盖
  - 产品语种级最近任务关联：Task 1 + Task 3 + Task 5 覆盖
  - 页面刷新后恢复最近结果：Task 3 + Task 4 + Task 5 覆盖
- Placeholder scan:
  - 本计划没有未定义实现细节或占位语句，所有任务都给出了明确文件、代码和命令
- Type consistency:
  - 产品侧统一使用 `link_check_tasks_json`（DB）和 `link_check_tasks`（序列化）
  - 任务侧统一使用 `task_id`、`status`、`link_url`、`checked_at`、`summary`
  - 路由统一围绕 `lang` 和产品 ID 建模，没有混入裸语种数组或多任务结构

**Execution handoff**

Plan complete and saved to `docs/superpowers/plans/2026-04-19-material-editor-link-check-implementation.md`.

Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

In this session I will use **Inline Execution** and continue in the current isolated worktree, because the user explicitly asked me to do the work here and did not ask for subagents.
