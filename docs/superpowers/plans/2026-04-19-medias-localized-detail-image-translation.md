# 商品素材编辑页小语种详情图翻译 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让商品素材编辑页在 `de/fr/es/it/ja/pt` 等小语种下也能管理商品详情图，并从该页直接发起、查看、追踪“从英语版一键翻译”任务，在翻译全成功时自动整组回填到当前语种详情图。

**Architecture:** 继续复用现有 `image_translate` 任务体系，不新建第二套任务模块；通过 `medias_context` 把图片翻译任务和商品、语种、入口页面绑定起来。数据层给 `media_product_detail_images` 增加来源追踪字段，runtime 在任务全成功时把翻译产物复制进 media bucket、整组替换目标语种详情图，并把回填结果持久化到任务状态里供编辑页和任务详情页同时读取。

**Tech Stack:** Flask 3、Jinja、原生前端 JS、PyMySQL、现有 `appcore.task_state` / `appcore.image_translate_runtime` / `appcore.medias` / TOS media bucket。

**Spec:** [docs/superpowers/specs/2026-04-19-medias-localized-detail-image-translation-design.md](../specs/2026-04-19-medias-localized-detail-image-translation-design.md)

---

## 说明

- 所有路径均相对于仓库根目录 `g:/Code/AutoVideoSrt`。
- 默认在独立 worktree `g:/Code/AutoVideoSrt/.worktrees/material-editor-localized-image-translation` 执行。
- 遵循 TDD：先写失败测试，再做最小实现，再回归相关测试。
- 本计划默认沿用现有 `image_translate` 明细页和重试页，不在素材编辑页重复实现逐张重试。
- 为了让自动回填时能精确记录“这张小语种图来自哪张英语详情图”，任务 item 需要新增两个可选字段：
  - `source_bucket`
  - `source_detail_image_id`

---

### Task 1: 详情图来源追踪与整组替换 DAO

**Files:**
- Create: `db/migrations/2026_04_19_medias_detail_image_translate_provenance.sql`
- Modify: `db/schema.sql`
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias_multi_lang.py`

- [ ] **Step 1: 先写失败测试，锁定 provenance 字段和整组替换行为**

Add to `tests/test_appcore_medias_multi_lang.py`:
```python
def test_detail_images_replace_for_lang_records_translate_provenance(user_id):
    pid = medias.create_product(user_id, "详情图翻译回填测试")
    try:
        en1 = medias.add_detail_image(pid, "en", "1/medias/1/en_1.jpg")
        en2 = medias.add_detail_image(pid, "en", "1/medias/1/en_2.jpg")
        medias.add_detail_image(pid, "de", "1/medias/1/manual_old.jpg", origin_type="manual")

        new_ids = medias.replace_detail_images_for_lang(
            pid,
            "de",
            [
                {
                    "object_key": "1/medias/1/de_1.jpg",
                    "content_type": "image/png",
                    "origin_type": "image_translate",
                    "source_detail_image_id": en1,
                    "image_translate_task_id": "img-task-1",
                },
                {
                    "object_key": "1/medias/1/de_2.jpg",
                    "content_type": "image/png",
                    "origin_type": "image_translate",
                    "source_detail_image_id": en2,
                    "image_translate_task_id": "img-task-1",
                },
            ],
        )

        rows = medias.list_detail_images(pid, "de")
        assert len(rows) == 2
        assert [row["id"] for row in rows] == new_ids
        assert [row["origin_type"] for row in rows] == ["image_translate", "image_translate"]
        assert [row["source_detail_image_id"] for row in rows] == [en1, en2]
        assert all(row["image_translate_task_id"] == "img-task-1" for row in rows)
    finally:
        medias.soft_delete_product(pid)
```

- [ ] **Step 2: 跑单测，确认当前实现还不支持这些字段**

Run: `python -m pytest tests/test_appcore_medias_multi_lang.py::test_detail_images_replace_for_lang_records_translate_provenance -q`

Expected: FAIL，报错应落在以下任一处：
- `TypeError: add_detail_image() got an unexpected keyword argument 'origin_type'`
- `AttributeError: module 'appcore.medias' has no attribute 'replace_detail_images_for_lang'`
- SQL 查询结果里没有 `origin_type` / `source_detail_image_id` / `image_translate_task_id`

- [ ] **Step 3: 写 migration 和 schema，给详情图表补来源字段**

Create `db/migrations/2026_04_19_medias_detail_image_translate_provenance.sql`:
```sql
ALTER TABLE media_product_detail_images
  ADD COLUMN origin_type VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual|from_url|image_translate',
  ADD COLUMN source_detail_image_id INT NULL COMMENT '若来自英文详情图翻译，则记录源详情图 id',
  ADD COLUMN image_translate_task_id VARCHAR(64) NULL COMMENT '若来自图片翻译任务，则记录任务 id',
  ADD KEY idx_detail_image_origin_task (image_translate_task_id),
  ADD KEY idx_detail_image_source (source_detail_image_id);
```

Modify `db/schema.sql` 中 `media_product_detail_images` 定义，追加：
```sql
  origin_type   VARCHAR(32)  NOT NULL DEFAULT 'manual',
  source_detail_image_id INT DEFAULT NULL,
  image_translate_task_id VARCHAR(64) DEFAULT NULL,
  KEY idx_detail_image_origin_task (image_translate_task_id),
  KEY idx_detail_image_source (source_detail_image_id),
```

- [ ] **Step 4: 扩展 DAO 签名，并新增按语种整组替换辅助函数**

Modify `appcore/medias.py`:
```python
def add_detail_image(
    product_id: int,
    lang: str,
    object_key: str,
    *,
    content_type: str | None = None,
    file_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    origin_type: str = "manual",
    source_detail_image_id: int | None = None,
    image_translate_task_id: str | None = None,
) -> int:
    sort_order = _next_detail_image_sort_order(product_id, lang)
    return execute(
        "INSERT INTO media_product_detail_images "
        "(product_id, lang, sort_order, object_key, content_type, file_size, width, height, "
        " origin_type, source_detail_image_id, image_translate_task_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            product_id, lang, sort_order, object_key, content_type, file_size, width, height,
            origin_type, source_detail_image_id, image_translate_task_id,
        ),
    )


def soft_delete_detail_images_by_lang(product_id: int, lang: str) -> int:
    return execute(
        "UPDATE media_product_detail_images "
        "SET deleted_at=NOW() "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL",
        (product_id, lang),
    )


def replace_detail_images_for_lang(product_id: int, lang: str, images: list[dict]) -> list[int]:
    soft_delete_detail_images_by_lang(product_id, lang)
    created_ids: list[int] = []
    for image in images:
        created_ids.append(
            add_detail_image(
                product_id,
                lang,
                image["object_key"],
                content_type=image.get("content_type"),
                file_size=image.get("file_size"),
                width=image.get("width"),
                height=image.get("height"),
                origin_type=image.get("origin_type") or "manual",
                source_detail_image_id=image.get("source_detail_image_id"),
                image_translate_task_id=image.get("image_translate_task_id"),
            )
        )
    return created_ids
```

同时修改 `list_detail_images()` / `get_detail_image()` 查询列，把这三个新字段返回出来。

- [ ] **Step 5: 回归 DAO 测试**

Run:
- `python -m pytest tests/test_appcore_medias_multi_lang.py::test_detail_images_replace_for_lang_records_translate_provenance -q`
- `python -m pytest tests/test_appcore_medias.py -q`

Expected:
- 新增测试 PASS
- 老的 medias DAO 测试仍 PASS

- [ ] **Step 6: 提交**

```bash
git add db/migrations/2026_04_19_medias_detail_image_translate_provenance.sql db/schema.sql appcore/medias.py tests/test_appcore_medias_multi_lang.py
git commit -m "feat(medias): add detail image provenance and replace helpers"
```

---

### Task 2: 图片翻译任务增加 medias_context，并支持自动回填

**Files:**
- Modify: `appcore/task_state.py`
- Modify: `web/routes/image_translate.py`
- Modify: `appcore/image_translate_runtime.py`
- Test: `tests/test_appcore_task_state.py`
- Test: `tests/test_image_translate_routes.py`
- Test: `tests/test_image_translate_runtime.py`

- [ ] **Step 1: 先写 task_state 和 state payload 的失败测试**

Add to `tests/test_appcore_task_state.py`:
```python
def test_create_image_translate_persists_medias_context(tmp_path):
    task = ts.create_image_translate(
        "img-medias-1",
        str(tmp_path / "img-medias-1"),
        user_id=1,
        preset="detail",
        target_language="de",
        target_language_name="德语",
        model_id="gemini-3-pro-image-preview",
        prompt="translate to de",
        items=[
            {
                "idx": 0,
                "filename": "en_1.jpg",
                "src_tos_key": "1/medias/1/en_1.jpg",
                "source_bucket": "media",
                "source_detail_image_id": 11,
            }
        ],
        medias_context={
            "entry": "medias_edit_detail",
            "product_id": 123,
            "source_lang": "en",
            "target_lang": "de",
            "source_bucket": "media",
            "auto_apply_detail_images": True,
            "apply_status": "pending",
            "source_detail_image_ids": [11],
        },
    )
    assert task["items"][0]["source_bucket"] == "media"
    assert task["items"][0]["source_detail_image_id"] == 11
    assert task["medias_context"]["product_id"] == 123
    assert task["medias_context"]["apply_status"] == "pending"
```

Add to `tests/test_image_translate_routes.py`:
```python
def test_get_state_includes_medias_context(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    task = {
        "id": "img-state-1",
        "type": "image_translate",
        "status": "done",
        "preset": "detail",
        "target_language": "de",
        "target_language_name": "德语",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x",
        "product_name": "测试商品",
        "project_name": "测试项目",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "done"},
        "error": "",
        "medias_context": {"entry": "medias_edit_detail", "product_id": 123, "target_lang": "de"},
        "_user_id": 1,
    }
    monkeypatch.setattr(r, "_get_owned_task", lambda task_id: task)
    resp = authed_client_no_db.get("/api/image-translate/img-state-1")
    assert resp.status_code == 200
    assert resp.get_json()["medias_context"]["product_id"] == 123
```

- [ ] **Step 2: 先写 runtime 的失败测试，锁定 media bucket 读源图和自动回填规则**

Add to `tests/test_image_translate_runtime.py`:
```python
def test_runtime_downloads_media_bucket_source_and_auto_applies(monkeypatch, tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([
        {
            **_item(0, src="1/medias/1/en_1.jpg"),
            "source_bucket": "media",
            "source_detail_image_id": 11,
        }
    ])
    task["preset"] = "detail"
    task["medias_context"] = {
        "entry": "medias_edit_detail",
        "product_id": 100,
        "source_lang": "en",
        "target_lang": "de",
        "source_bucket": "media",
        "source_detail_image_ids": [11],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
    }

    applied = {}

    def fake_download_media(key, local_path):
        open(local_path, "wb").write(b"EN")
        return local_path

    def fake_upload_media_object(object_key, data, content_type=None, bucket=None):
        applied["uploaded_key"] = object_key

    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, images)
        return [901]

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_media_file", side_effect=fake_download_media), \
         patch.object(rt.tos_clients, "download_file", side_effect=AssertionError("should not use upload bucket")), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.tos_clients, "upload_media_object", side_effect=fake_upload_media_object), \
         patch.object(rt.tos_clients, "build_media_object_key", return_value="1/medias/100/de_1.png"), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")), \
         patch.object(rt.medias, "replace_detail_images_for_lang", side_effect=fake_replace):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert applied["replace"][0] == 100
    assert applied["replace"][1] == "de"
    assert applied["replace"][2][0]["origin_type"] == "image_translate"
    assert task["medias_context"]["apply_status"] == "applied"
    assert task["medias_context"]["applied_detail_image_ids"] == [901]


def test_runtime_skips_auto_apply_when_any_item_failed(monkeypatch):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0), _item(1)])
    task["items"][0]["status"] = "done"
    task["items"][0]["dst_tos_key"] = "artifacts/image_translate/1/t-img-1/out_0.png"
    task["items"][1]["status"] = "failed"
    task["medias_context"] = {
        "entry": "medias_edit_detail",
        "product_id": 100,
        "target_lang": "de",
        "auto_apply_detail_images": True,
        "apply_status": "pending",
    }

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.medias, "replace_detail_images_for_lang", side_effect=AssertionError("must not apply")):
        runtime._finalize_auto_apply(task)

    assert task["medias_context"]["apply_status"] == "skipped_failed"
```

- [ ] **Step 3: 跑这些测试，确认当前缺字段、缺回填逻辑**

Run:
- `python -m pytest tests/test_appcore_task_state.py::test_create_image_translate_persists_medias_context -q`
- `python -m pytest tests/test_image_translate_routes.py::test_get_state_includes_medias_context -q`
- `python -m pytest tests/test_image_translate_runtime.py::test_runtime_downloads_media_bucket_source_and_auto_applies -q`

Expected: FAIL，原因应为：
- `create_image_translate()` 不接受 `medias_context`
- `_state_payload()` 不返回 `medias_context`
- runtime 没有 `download_media_file` 路径，也没有自动回填函数

- [ ] **Step 4: 扩展 create_image_translate/item shape，保证任务状态完整持久化**

Modify `appcore/task_state.py`:
```python
def create_image_translate(
    task_id: str,
    task_dir: str,
    *,
    user_id: int,
    preset: str,
    target_language: str,
    target_language_name: str,
    model_id: str,
    prompt: str,
    items: list[dict],
    product_name: str = "",
    project_name: str = "",
    medias_context: dict | None = None,
) -> dict:
    normalized_items = []
    for idx, raw in enumerate(items):
        normalized_items.append({
            "idx": int(raw.get("idx", idx)),
            "filename": str(raw.get("filename") or ""),
            "src_tos_key": str(raw.get("src_tos_key") or ""),
            "source_bucket": str(raw.get("source_bucket") or "upload"),
            "source_detail_image_id": raw.get("source_detail_image_id"),
            "dst_tos_key": "",
            "status": "pending",
            "attempts": 0,
            "error": "",
        })
    task = {
        ...
        "items": normalized_items,
        "medias_context": dict(medias_context or {}),
        ...
    }
```

Modify `web/routes/image_translate.py` `_state_payload()`：
```python
def _state_payload(task: dict) -> dict:
    return {
        ...
        "medias_context": dict(task.get("medias_context") or {}),
    }
```

- [ ] **Step 5: 在 runtime 里补两段核心逻辑：按 bucket 下载源图、全成功后自动回填**

Modify `appcore/image_translate_runtime.py`，新增两个辅助函数并在 `start()` 结束前调用：
```python
def _download_source_image(self, task: dict, item: dict, local_path: str) -> str:
    source_bucket = (item.get("source_bucket") or (task.get("medias_context") or {}).get("source_bucket") or "upload").strip()
    if source_bucket == "media":
        return tos_clients.download_media_file(item["src_tos_key"], local_path)
    return tos_clients.download_file(item["src_tos_key"], local_path)


def _finalize_auto_apply(self, task: dict) -> None:
    ctx = task.get("medias_context") or {}
    if not ctx.get("auto_apply_detail_images"):
        return
    if any((it.get("status") or "") != "done" for it in (task.get("items") or [])):
        ctx["apply_status"] = "skipped_failed"
        store.update(task["id"], medias_context=ctx)
        return

    images = []
    applied_ids = []
    for item in task.get("items") or []:
        fd, tmp_path = tempfile.mkstemp(suffix=self._ext_from_mime("image/png") or ".png", prefix="it_apply_")
        os.close(fd)
        try:
            tos_clients.download_file(item["dst_tos_key"], tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
            filename = f"detail_translate_{item['idx']}.png"
            object_key = tos_clients.build_media_object_key(task.get("_user_id") or 0, ctx["product_id"], filename)
            tos_clients.upload_media_object(object_key, data, content_type="image/png")
            images.append({
                "object_key": object_key,
                "content_type": "image/png",
                "origin_type": "image_translate",
                "source_detail_image_id": item.get("source_detail_image_id"),
                "image_translate_task_id": task["id"],
            })
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    applied_ids = medias.replace_detail_images_for_lang(ctx["product_id"], ctx["target_lang"], images)
    ctx["apply_status"] = "applied"
    ctx["applied_at"] = datetime.now().isoformat()
    ctx["applied_detail_image_ids"] = applied_ids
    ctx["last_apply_error"] = ""
    store.update(task["id"], medias_context=ctx)
```

并在 `start()` 末尾：
```python
if task["status"] == "done":
    try:
        self._finalize_auto_apply(task)
    except Exception as exc:
        ctx = dict(task.get("medias_context") or {})
        if ctx:
            ctx["apply_status"] = "apply_error"
            ctx["last_apply_error"] = str(exc)
            store.update(task_id, medias_context=ctx)
```

- [ ] **Step 6: 跑回归测试**

Run:
- `python -m pytest tests/test_appcore_task_state.py -q`
- `python -m pytest tests/test_image_translate_routes.py -q`
- `python -m pytest tests/test_image_translate_runtime.py -q`

Expected:
- 新增 `medias_context` / runtime 自动回填测试 PASS
- 既有图片翻译测试仍 PASS

- [ ] **Step 7: 提交**

```bash
git add appcore/task_state.py web/routes/image_translate.py appcore/image_translate_runtime.py tests/test_appcore_task_state.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py
git commit -m "feat(image-translate): persist medias context and auto-apply detail images"
```

---

### Task 3: 素材页新增“从英语版一键翻译”入口与任务历史 API

**Files:**
- Create: `tests/test_medias_routes.py`
- Modify: `web/routes/medias.py`
- Modify: `web/routes/image_translate.py`

- [ ] **Step 1: 先写素材路由失败测试，锁定入口、历史、跳转字段**

Create `tests/test_medias_routes.py`:
```python
import json


def test_detail_images_translate_from_en_creates_bound_task(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "飞机玩具", "product_code": "plane-toy"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [
        {"id": 11, "object_key": "1/medias/1/en_1.jpg", "content_type": "image/jpeg"},
        {"id": 12, "object_key": "1/medias/1/en_2.jpg", "content_type": "image/jpeg"},
    ] if lang == "en" else [])
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: {"de": "德语"}.get(lang, lang))
    monkeypatch.setattr(r.task_state, "create_image_translate", lambda task_id, task_dir, **kw: created.update({"task_id": task_id, **kw}) or {"id": task_id})
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post("/medias/api/products/123/detail-images/translate-from-en", json={"lang": "de"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["task_id"]
    assert data["detail_url"] == f"/image-translate/{data['task_id']}"
    assert created["preset"] == "detail"
    assert created["medias_context"]["entry"] == "medias_edit_detail"
    assert created["medias_context"]["product_id"] == 123
    assert created["medias_context"]["target_lang"] == "de"
    assert created["items"][0]["source_bucket"] == "media"
    assert created["items"][0]["source_detail_image_id"] == 11


def test_detail_image_translate_tasks_filters_current_product_and_lang(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "飞机玩具"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r,
        "db_query",
        lambda sql, args=(): [
            {
                "id": "img-1",
                "created_at": None,
                "updated_at": None,
                "state_json": json.dumps({
                    "type": "image_translate",
                    "status": "done",
                    "preset": "detail",
                    "progress": {"total": 2, "done": 2, "failed": 0, "running": 0},
                    "medias_context": {
                        "entry": "medias_edit_detail",
                        "product_id": 123,
                        "target_lang": "de",
                        "apply_status": "applied",
                    },
                }, ensure_ascii=False),
            }
        ],
    )

    resp = authed_client_no_db.get("/medias/api/products/123/detail-image-translate-tasks?lang=de")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["items"]) == 1
    assert data["items"][0]["task_id"] == "img-1"
    assert data["items"][0]["apply_status"] == "applied"
    assert data["items"][0]["detail_url"] == "/image-translate/img-1"
```

- [ ] **Step 2: 跑路由测试，确认接口尚不存在**

Run:
- `python -m pytest tests/test_medias_routes.py::test_detail_images_translate_from_en_creates_bound_task -q`
- `python -m pytest tests/test_medias_routes.py::test_detail_image_translate_tasks_filters_current_product_and_lang -q`

Expected: FAIL，至少有一个是 404 或 attribute missing。

- [ ] **Step 3: 在 medias 路由里补启动翻译任务、列历史、汇总状态的后端接口**

Modify `web/routes/medias.py`，先在顶部补依赖：
```python
from uuid import uuid4
from appcore.db import query as db_query
from web.routes import image_translate as image_translate_routes


def _start_image_translate_runner(task_id: str, user_id: int) -> None:
    image_translate_routes._start_runner(task_id, user_id)
```

再新增创建入口：
```python
@bp.route("/api/products/<int:pid>/detail-images/translate-from-en", methods=["POST"])
@login_required
def api_detail_images_translate_from_en(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "英文详情图不需要从英语版翻译"}), 400

    source_rows = medias.list_detail_images(pid, "en")
    if not source_rows:
        return jsonify({"error": "请先准备英语版商品详情图"}), 409

    task_id = uuid4().hex
    items = [
        {
            "idx": idx,
            "filename": os.path.basename(row["object_key"]) or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        }
        for idx, row in enumerate(source_rows)
    ]
    lang_name = medias.get_language_name(lang)
    medias_context = {
        "entry": "medias_edit_detail",
        "product_id": pid,
        "source_lang": "en",
        "target_lang": lang,
        "source_bucket": "media",
        "source_detail_image_ids": [row["id"] for row in source_rows],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
        "applied_at": "",
        "applied_detail_image_ids": [],
        "last_apply_error": "",
    }
    prompt = its.get_prompts_for_lang(lang)["detail"]
    task_state.create_image_translate(
        task_id,
        os.path.join(OUTPUT_DIR, task_id),
        user_id=current_user.id,
        preset="detail",
        target_language=lang,
        target_language_name=lang_name,
        model_id=body.get("model_id") or image_translate_routes._default_model_id(current_user.id),
        prompt=prompt.replace("{target_language_name}", lang_name),
        items=items,
        product_name=p.get("name") or "",
        project_name=image_translate_routes._compose_project_name(p.get("name") or "", "detail", lang_name),
        medias_context=medias_context,
    )
    _start_image_translate_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "detail_url": f"/image-translate/{task_id}"}), 201
```

再新增历史接口：
```python
@bp.route("/api/products/<int:pid>/detail-image-translate-tasks", methods=["GET"])
@login_required
def api_detail_image_translate_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

    rows = db_query(
        "SELECT id, created_at, updated_at, state_json FROM projects "
        "WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 50",
        (current_user.id,),
    )
    items = []
    for row in rows:
        state = json.loads(row["state_json"] or "{}")
        ctx = state.get("medias_context") or {}
        if state.get("preset") != "detail":
            continue
        if ctx.get("entry") != "medias_edit_detail":
            continue
        if int(ctx.get("product_id") or 0) != pid:
            continue
        if (ctx.get("target_lang") or "") != lang:
            continue
        progress = state.get("progress") or {}
        items.append({
            "task_id": row["id"],
            "status": state.get("status") or "queued",
            "apply_status": ctx.get("apply_status") or "",
            "applied_detail_image_ids": list(ctx.get("applied_detail_image_ids") or []),
            "progress": progress,
            "detail_url": f"/image-translate/{row['id']}",
            "created_at": row["created_at"].isoformat() if row.get("created_at") else "",
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else "",
        })
    return jsonify({"items": items})
```

- [ ] **Step 4: 图片翻译详情页补一层公开 helper，避免 medias 路由直接绑死私有函数**

Modify `web/routes/image_translate.py`：
```python
def start_image_translate_runner(task_id: str, user_id: int) -> None:
    _start_runner(task_id, user_id)
```

然后把 `medias.py` 里的 `_start_image_translate_runner()` 改成调这个公开 helper。

- [ ] **Step 5: 回归路由测试**

Run:
- `python -m pytest tests/test_medias_routes.py -q`
- `python -m pytest tests/test_image_translate_routes.py -q`

Expected:
- 新增 medias 路由测试 PASS
- 原有图片翻译路由测试仍 PASS

- [ ] **Step 6: 提交**

```bash
git add web/routes/medias.py web/routes/image_translate.py tests/test_medias_routes.py
git commit -m "feat(medias): add translate-from-en detail image task routes"
```

---

### Task 4: 图片翻译详情页显示素材入口上下文

**Files:**
- Modify: `web/templates/image_translate_detail.html`
- Modify: `web/templates/_image_translate_scripts.html`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: 先写模板失败测试，锁定上下文展示和回填结果提示**

Add to `tests/test_web_routes.py`:
```python
def test_image_translate_detail_template_contains_medias_context_block():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "image_translate_detail.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_image_translate_scripts.html").read_text(encoding="utf-8")

    assert "itMediasContextCard" in template
    assert "商品素材编辑页" in template
    assert "window.renderImageTranslateMediasContext" in scripts
    assert "medias_context" in scripts
```

- [ ] **Step 2: 跑模板测试，确认页面还没有这个块**

Run: `python -m pytest tests/test_web_routes.py::test_image_translate_detail_template_contains_medias_context_block -q`

Expected: FAIL，因为模板和脚本里还没有 `itMediasContextCard`。

- [ ] **Step 3: 给详情页增加上下文卡片和回填状态文案**

Modify `web/templates/image_translate_detail.html`，在任务摘要区附近加入：
```html
<section class="oc-card" id="itMediasContextCard" hidden>
  <div class="oc-card-title">来源上下文</div>
  <div class="oc-kv-grid">
    <div><span>入口</span><strong id="itMediasEntry">商品素材编辑页</strong></div>
    <div><span>商品</span><strong id="itMediasProductName">-</strong></div>
    <div><span>目标语种</span><strong id="itMediasTargetLang">-</strong></div>
    <div><span>回填状态</span><strong id="itMediasApplyStatus">-</strong></div>
  </div>
</section>
```

Modify `web/templates/_image_translate_scripts.html`：
```html
<script>
  window.renderImageTranslateMediasContext = function renderImageTranslateMediasContext(state) {
    const ctx = (state && state.medias_context) || {};
    const card = document.getElementById("itMediasContextCard");
    if (!card) return;
    if (ctx.entry !== "medias_edit_detail") {
        card.hidden = true;
        return;
    }
    card.hidden = false;
    document.getElementById("itMediasEntry").textContent = "商品素材编辑页";
    document.getElementById("itMediasProductName").textContent = state.product_name || "-";
    document.getElementById("itMediasTargetLang").textContent = state.target_language_name || state.target_language || "-";
    document.getElementById("itMediasApplyStatus").textContent =
      ctx.apply_status === "applied" ? "已回填到当前语种详情图" :
      ctx.apply_status === "skipped_failed" ? "任务未全成功，未回填" :
      ctx.apply_status === "apply_error" ? `回填失败：${ctx.last_apply_error || "未知错误"}` :
      "等待回填";
  };
</script>
```

并在现有页面初始化流程里调用 `window.renderImageTranslateMediasContext(state)`。

- [ ] **Step 4: 跑模板回归测试**

Run:
- `python -m pytest tests/test_web_routes.py::test_image_translate_detail_template_contains_medias_context_block -q`
- `python -m pytest tests/test_web_routes.py -q`

Expected:
- 新增模板测试 PASS
- 其余页面模板测试不回退

- [ ] **Step 5: 提交**

```bash
git add web/templates/image_translate_detail.html web/templates/_image_translate_scripts.html tests/test_web_routes.py
git commit -m "feat(image-translate): show medias detail-image context on detail page"
```

---

### Task 5: 素材编辑弹窗展示小语种详情图、翻译任务记录与跳转入口

**Files:**
- Modify: `web/templates/_medias_edit_detail_modal.html`
- Modify: `web/static/medias.js`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: 先写前端模板失败测试，锁定新按钮、状态条、历史列表容器**

Add to `tests/test_web_routes.py`:
```python
def test_medias_edit_modal_contains_detail_image_translation_controls():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edDetailImagesTranslateBtn"' in template
    assert 'id="edDetailTranslateStatus"' in template
    assert 'id="edDetailTranslateHistory"' in template
    assert "detail-image-translate-tasks" in scripts
    assert "detail-images/translate-from-en" in scripts
```

- [ ] **Step 2: 跑模板测试，确认当前编辑页还没有翻译入口**

Run: `python -m pytest tests/test_web_routes.py::test_medias_edit_modal_contains_detail_image_translation_controls -q`

Expected: FAIL，因为模板和脚本里还没有这些节点和接口调用。

- [ ] **Step 3: 扩展编辑弹窗结构，给小语种详情图区域补状态、按钮、历史列表和任务弹窗**

Modify `web/templates/_medias_edit_detail_modal.html`，把“仅英语可编辑”的说明替换为通用结构：
```html
<section class="oc-section oc-section-card" id="edDetailImagesSection">
  <div class="oc-section-title">
    <span id="edDetailImagesTitle">商品详情图</span>
    <span class="optional" id="edDetailImagesSubtitle">英语原始版，用于后续图片翻译</span>
    <span class="count" id="edDetailImagesBadge">0</span>
  </div>
  <div class="oc-inline-notice" id="edDetailTranslateStatus" hidden></div>
  <div id="edDetailImagesGrid" class="oc-detail-images-grid"></div>
  <div class="oc-detail-images-actions">
    <button type="button" class="oc-btn ghost sm" id="edDetailImagesPickBtn">选择图片批量上传</button>
    <button type="button" class="oc-btn ghost sm" id="edDetailImagesFromUrlBtn">从商品链接一键下载</button>
    <button type="button" class="oc-btn ghost sm" id="edDetailImagesTranslateBtn" hidden>从英语版一键翻译</button>
    <span class="oc-hint">JPG / PNG / WebP，单次 ≤ 20 张，单张 ≤ 15MB</span>
  </div>
  <div class="oc-subsection" id="edDetailTranslateHistoryWrap" hidden>
    <div class="oc-subsection-title">翻译任务记录</div>
    <div id="edDetailTranslateHistory" class="oc-history-list"></div>
  </div>
</section>

<div class="oc-modal-mask oc" id="edDetailTranslateTaskMask" hidden>
  <div class="oc-modal oc-modal-narrow" role="dialog" aria-modal="true">
    <div class="oc-modal-head">
      <h3>从英语版一键翻译</h3>
      <button class="oc-icon-btn" id="edDetailTranslateTaskClose" aria-label="关闭">×</button>
    </div>
    <div class="oc-modal-body">
      <div id="edDetailTranslateTaskMsg">准备中...</div>
      <div id="edDetailTranslateTaskMeta"></div>
      <a class="oc-btn primary" id="edDetailTranslateTaskLink" href="#" target="_blank" rel="noopener">查看任务详情</a>
    </div>
  </div>
</div>
```

- [ ] **Step 4: 在 `medias.js` 中让详情图区块对所有语种可见，并补任务历史/状态渲染**

Modify `web/static/medias.js`：
```javascript
async function edLoadDetailTranslateTasks(pid, lang) {
  if (!pid || !lang || lang === "en") return [];
  const data = await fetchJSON(`/medias/api/products/${pid}/detail-image-translate-tasks?lang=${encodeURIComponent(lang)}`);
  return Array.isArray(data.items) ? data.items : [];
}

function edRenderDetailTranslateState(lang, tasks, detailItems) {
  const title = $("edDetailImagesTitle");
  const subtitle = $("edDetailImagesSubtitle");
  const status = $("edDetailTranslateStatus");
  const historyWrap = $("edDetailTranslateHistoryWrap");
  const translateBtn = $("edDetailImagesTranslateBtn");
  const langName = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();

  if (title) title.textContent = "商品详情图";
  if (subtitle) subtitle.textContent = lang === "en"
    ? "英语原始版，用于后续图片翻译"
    : `${langName} 版本，可上传、从商品链接下载，或从英语版一键翻译`;
  if (translateBtn) translateBtn.hidden = lang === "en";
  if (historyWrap) historyWrap.hidden = lang === "en";

  if (!status) return;
  const latest = tasks[0] || null;
  const applied = Array.isArray(detailItems) && detailItems.some(item => item.origin_type === "image_translate");
  if (lang === "en") {
    status.hidden = true;
    return;
  }
  status.hidden = false;
  status.innerHTML = applied && latest
    ? `当前语种详情图已由英语版翻译任务回填。<a href="${latest.detail_url}" target="_blank" rel="noopener">查看任务详情</a>`
    : latest
      ? `最近一次翻译状态：${latest.status} / ${latest.apply_status || "pending"}。<a href="${latest.detail_url}" target="_blank" rel="noopener">查看任务详情</a>`
      : "当前语种还没有执行过从英语版一键翻译。";
}

function edRenderDetailTranslateHistory(tasks) {
  const box = $("edDetailTranslateHistory");
  if (!box) return;
  if (!tasks.length) {
    box.innerHTML = '<div class="oc-empty-inline">暂无翻译任务记录</div>';
    return;
  }
  box.innerHTML = tasks.map(task => `
    <div class="oc-history-row">
      <div>
        <strong>${task.status}</strong>
        <span>${task.progress.done || 0}/${task.progress.total || 0}</span>
        <span>${task.apply_status || "pending"}</span>
      </div>
      <div class="oc-history-actions">
        <a class="oc-btn ghost xs" href="${task.detail_url}" target="_blank" rel="noopener">查看详情</a>
        <button type="button" class="oc-btn ghost xs" data-retranslate-lang="${edState.activeLang}">重新翻译</button>
      </div>
    </div>
  `).join("");
}
```

并把 `edRenderActiveLangView()` 中的旧逻辑：
```javascript
if (lang === 'en') {
  ctrl.show();
  if (pid) ctrl.load(pid);
} else {
  ctrl.hide();
}
```
替换成：
```javascript
ctrl.show();
if (pid) {
  await ctrl.load(pid);
  const tasks = await edLoadDetailTranslateTasks(pid, lang);
  edRenderDetailTranslateState(lang, tasks, ctrl.items ? ctrl.items() : []);
  edRenderDetailTranslateHistory(tasks);
}
```

如果 `createDetailImagesController()` 当前没有暴露 `items()`，就在 controller 返回对象里补一个只读 getter：
```javascript
items: () => items.slice(),
```

- [ ] **Step 5: 补“从英语版一键翻译”按钮点击、任务弹窗、历史刷新和重新翻译**

继续修改 `web/static/medias.js`：
```javascript
async function edStartDetailTranslate() {
  const pid = edState.productData && edState.productData.product && edState.productData.product.id;
  const lang = edState.activeLang;
  if (!pid || !lang || lang === "en") return;

  const mask = $("edDetailTranslateTaskMask");
  const msg = $("edDetailTranslateTaskMsg");
  const meta = $("edDetailTranslateTaskMeta");
  const link = $("edDetailTranslateTaskLink");
  if (mask) mask.hidden = false;
  if (msg) msg.textContent = "正在创建翻译任务...";
  if (meta) meta.textContent = `${lang.toUpperCase()} · 商品详情图`;

  const data = await fetchJSON(`/medias/api/products/${pid}/detail-images/translate-from-en`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lang }),
  });
  if (msg) msg.textContent = "翻译任务已创建，可以继续留在当前页，也可以打开详情页查看进度。";
  if (link) {
    link.href = data.detail_url;
    link.dataset.taskId = data.task_id;
  }

  const tasks = await edLoadDetailTranslateTasks(pid, lang);
  edRenderDetailTranslateHistory(tasks);
  edRenderDetailTranslateState(lang, tasks, ensureEdDetailImagesCtrl().items());
}

$("edDetailImagesTranslateBtn")?.addEventListener("click", edStartDetailTranslate);
$("edDetailTranslateHistory")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-retranslate-lang]");
  if (!btn) return;
  edStartDetailTranslate();
});
```

- [ ] **Step 6: 跑前端模板回归测试**

Run:
- `python -m pytest tests/test_web_routes.py::test_medias_edit_modal_contains_detail_image_translation_controls -q`
- `python -m pytest tests/test_web_routes.py -q`

Expected:
- 新增素材编辑弹窗测试 PASS
- 既有模板测试不回退

- [ ] **Step 7: 手动走一遍最小验收**

Run:
- `python web/app.py` 或项目当前本地启动命令

Manual verification:
- 打开 `/medias`
- 进入某个已有 EN 详情图的商品编辑弹窗
- 切到 `DE`
- 确认详情图区块可见，并同时看到：
  - `从商品链接一键下载`
  - `从英语版一键翻译`
  - `翻译任务记录`
- 点击 `从英语版一键翻译` 后看到弹窗，可跳转到 `/image-translate/<task_id>`
- 回到弹窗后能看到新任务记录

- [ ] **Step 8: 提交**

```bash
git add web/templates/_medias_edit_detail_modal.html web/static/medias.js tests/test_web_routes.py
git commit -m "feat(medias): surface localized detail image translate workflow"
```

---

## 自检

### 1. Spec 覆盖检查

- 小语种详情图和英语版一致可管理：Task 5
- 从商品链接下载改为按当前语种商品页抓图：现有接口已支持，Task 5 会解除前端隐藏并直接复用
- 从英语版一键翻译：Task 3 + Task 5
- 任务要和当前商品/语种/入口关联：Task 2 + Task 3
- 可从当前入口跳转任务详情：Task 3 + Task 5
- 页面能看到历史记录和结果：Task 3 + Task 5
- 翻译全成功自动整组覆盖，否则不覆盖：Task 2
- 当前详情图是否来自翻译结果可识别：Task 1 + Task 5
- 任务详情页能显示它来自素材编辑入口：Task 4

### 2. Placeholder 扫描

- 没有保留 `TODO` / `TBD`
- 每个修改步骤都给了明确路径、代码块和命令
- “从商品链接一键下载”的现有能力明确写成“前端解隐藏并复用”，避免遗漏

### 3. 类型/命名一致性

- `medias_context.entry` 固定为 `medias_edit_detail`
- 目标语种字段统一使用 `target_lang`
- 回填状态字段统一使用 `apply_status`
- 任务 item 中的源图字段统一使用 `source_bucket` / `source_detail_image_id`
- 详情图来源字段统一使用 `origin_type` / `source_detail_image_id` / `image_translate_task_id`

---

Plan complete and saved to `docs/superpowers/plans/2026-04-19-medias-localized-detail-image-translation.md`.

两种执行方式：

1. `Subagent-Driven (recommended in the abstract)`：需要你明确授权我用并行子代理拆任务执行。
2. `Inline Execution`：我就在当前会话里按这个计划顺序继续实现。

当前这条线程里你还没要求我启用子代理，所以默认下一步应走 `Inline Execution`。
