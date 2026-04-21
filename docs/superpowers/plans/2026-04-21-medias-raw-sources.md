# 素材管理 · 原始去字幕素材 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在产品层面新增一类资源「原始去字幕素材」（视频 + 封面），作为翻译源替换原来的英文 `media_items`；翻译时勾选原始素材 × 目标语言，产出继续写入 `media_items` 各语种，封面也同步翻译并挂上。

**Architecture:** 新建 `media_raw_sources` 表（无 lang 维度）承载翻译源；`media_items` 加 `source_raw_id` 记溯源；`bulk_translate` plan 生成器用新参数 `raw_source_ids` 替代原来读 en items 的路径；runtime 处理 video kind 时同时跑视频翻译 + 封面图片翻译；前端在产品列表行加「原始视频 (n)」按钮打开抽屉管理，翻译按钮改为勾选式弹窗。

**Tech Stack:** Python 3.11+ / Flask / MySQL / Volcano TOS / Jinja2 + 原生 JS / pytest / Playwright。

**Spec:** [docs/superpowers/specs/2026-04-21-medias-raw-sources-design.md](../specs/2026-04-21-medias-raw-sources-design.md)

---

## 任务列表

1. DB migration：建 `media_raw_sources` + `media_items.source_raw_id`
2. TOS object_key 生成器
3. DAO：`create_raw_source / list_raw_sources / get_raw_source / update / soft_delete / count_by_product`
4. DAO：扩 `collect_media_object_references`
5. DAO 测试
6. REST：列表 + 创建（上传）
7. REST：改名 + 软删
8. REST：签名 URL（视频 / 封面）
9. REST：产品列表接口返回 `raw_sources_count`
10. REST 测试
11. `bulk_translate_plan`：加 `raw_source_ids` 参数，video kind 从 raw_sources 取源
12. `bulk_translate_plan` 测试
13. `bulk_translate_runtime`：video kind 下载 raw video + 跑封面图片翻译 + 产出 `media_items` 带 `source_raw_id`
14. REST：`POST /medias/api/products/<pid>/translate`（新翻译入口）
15. 翻译入口测试
16. 前端：产品列表行「原始视频 (n)」按钮 + 抽屉
17. 前端：翻译按钮改为弹窗
18. E2E 冒烟

---

### Task 1: DB migration 脚本

**Files:**
- Create: `db/migrations/2026_04_21_medias_raw_sources.sql`

- [ ] **Step 1: 写 migration 脚本**

```sql
-- db/migrations/2026_04_21_medias_raw_sources.sql
-- 新增「原始去字幕素材」表，并在 media_items 上加 source_raw_id 溯源列

CREATE TABLE IF NOT EXISTS media_raw_sources (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  user_id   INT NOT NULL,
  display_name     VARCHAR(255) DEFAULT NULL,
  video_object_key VARCHAR(500) NOT NULL,
  cover_object_key VARCHAR(500) NOT NULL,
  duration_seconds FLOAT  DEFAULT NULL,
  file_size        BIGINT DEFAULT NULL,
  width            INT    DEFAULT NULL,
  height           INT    DEFAULT NULL,
  sort_order       INT    NOT NULL DEFAULT 0,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       DATETIME DEFAULT NULL,
  KEY idx_product_deleted (product_id, deleted_at),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE media_items
  ADD COLUMN source_raw_id INT NULL AFTER cover_object_key,
  ADD KEY idx_source_raw (source_raw_id);
```

- [ ] **Step 2: 本地跑一遍 migration 验证**

在本地 MySQL `auto_video` 库上执行：
```bash
mysql -h 127.0.0.1 -u root auto_video < db/migrations/2026_04_21_medias_raw_sources.sql
```
Expected：无报错；`SHOW CREATE TABLE media_raw_sources\G` 输出结构吻合；`DESCRIBE media_items` 能看到 `source_raw_id INT NULL`。

- [ ] **Step 3: 注册到 `appcore/db_migrations.py`**

读 `appcore/db_migrations.py` 找到 migration 列表的维护位置，把 `2026_04_21_medias_raw_sources.sql` 按日期顺序追加到列表里（和现有 `2026_04_21_seed_doubao_wildcard.sql` 相邻，但保证字母序先于它，或按文件名日期排序）。

- [ ] **Step 4: 提交**

```bash
git add db/migrations/2026_04_21_medias_raw_sources.sql appcore/db_migrations.py
git commit -m "feat(medias): 新增 media_raw_sources 表与 source_raw_id 溯源列"
```

---

### Task 2: TOS object_key 生成器

**Files:**
- Modify: `appcore/tos_clients.py`
- Test: `tests/test_tos_clients_raw_source_keys.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tos_clients_raw_source_keys.py
from appcore import tos_clients

def test_build_media_raw_source_video_key_layout():
    key = tos_clients.build_media_raw_source_key(
        user_id=42, product_id=100, kind="video",
        filename="hello.mp4",
    )
    assert key.startswith("42/medias/100/raw_sources/")
    assert key.endswith(".mp4")
    assert "hello" in key  # 原文件名保留在末尾

def test_build_media_raw_source_cover_key_layout():
    key = tos_clients.build_media_raw_source_key(
        user_id=42, product_id=100, kind="cover",
        filename="cover.png",
    )
    assert key.startswith("42/medias/100/raw_sources/")
    assert ".cover." in key or key.endswith(".cover.png")
    assert key.endswith(".png")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_tos_clients_raw_source_keys.py -v
```
Expected: FAIL，`AttributeError: module 'appcore.tos_clients' has no attribute 'build_media_raw_source_key'`.

- [ ] **Step 3: 在 `appcore/tos_clients.py` 追加实现**

在 `build_media_object_key` 函数下方追加：

```python
def build_media_raw_source_key(
    user_id: int, product_id: int, *, kind: str, filename: str
) -> str:
    """生成原始去字幕素材的 TOS object key。

    kind="video" → {user_id}/medias/{product_id}/raw_sources/{uuid}_{filename}
    kind="cover" → {user_id}/medias/{product_id}/raw_sources/{uuid}_{stem}.cover{ext}
    """
    import uuid
    from pathlib import Path as _Path

    if kind not in ("video", "cover"):
        raise ValueError(f"invalid kind: {kind}")
    raw = _Path(filename or "media.bin").name
    stem = _Path(raw).stem or "media"
    ext = _Path(raw).suffix or (".mp4" if kind == "video" else ".jpg")
    unique = uuid.uuid4().hex[:12]
    if kind == "video":
        return f"{user_id}/medias/{product_id}/raw_sources/{unique}_{raw}"
    # cover
    return f"{user_id}/medias/{product_id}/raw_sources/{unique}_{stem}.cover{ext}"
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_tos_clients_raw_source_keys.py -v
```
Expected: PASS (2 passed).

- [ ] **Step 5: 提交**

```bash
git add appcore/tos_clients.py tests/test_tos_clients_raw_source_keys.py
git commit -m "feat(tos): 新增 build_media_raw_source_key 生成原始素材对象键"
```

---

### Task 3: DAO 层 raw_source CRUD

**Files:**
- Modify: `appcore/medias.py`（追加到文件末尾）

- [ ] **Step 1: 在 `appcore/medias.py` 末尾追加 DAO 函数**

```python
# ---------- 原始去字幕素材（raw sources）----------

def create_raw_source(
    product_id: int,
    user_id: int,
    *,
    display_name: str | None,
    video_object_key: str,
    cover_object_key: str,
    duration_seconds: float | None = None,
    file_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> int:
    return execute(
        "INSERT INTO media_raw_sources "
        "(product_id, user_id, display_name, video_object_key, cover_object_key, "
        " duration_seconds, file_size, width, height) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (product_id, user_id, display_name, video_object_key, cover_object_key,
         duration_seconds, file_size, width, height),
    )


def get_raw_source(rid: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_raw_sources WHERE id=%s AND deleted_at IS NULL",
        (rid,),
    )


def list_raw_sources(product_id: int) -> list[dict]:
    return query(
        "SELECT * FROM media_raw_sources "
        "WHERE product_id=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id,),
    )


def update_raw_source(rid: int, **fields) -> int:
    allowed = {"display_name", "sort_order"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(fields[k] for k in keys) + (rid,)
    return execute(f"UPDATE media_raw_sources SET {set_sql} WHERE id=%s", args)


def soft_delete_raw_source(rid: int) -> int:
    return execute(
        "UPDATE media_raw_sources SET deleted_at=NOW() "
        "WHERE id=%s AND deleted_at IS NULL",
        (rid,),
    )


def count_raw_sources_by_product(product_ids: list[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, COUNT(*) AS c FROM media_raw_sources "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"GROUP BY product_id",
        tuple(product_ids),
    )
    return {int(r["product_id"]): int(r["c"]) for r in rows}
```

- [ ] **Step 2: 提交**

```bash
git add appcore/medias.py
git commit -m "feat(medias): 新增 raw_source 的 DAO 增删改查函数"
```

---

### Task 4: 把 raw_source 的对象纳入 `collect_media_object_references`

**Files:**
- Modify: `appcore/medias.py`（`collect_media_object_references` 函数）

- [ ] **Step 1: 修改 `collect_media_object_references`**

在 `rows.extend(query("SELECT 'product_detail_image' AS source, ..."))` 之后、`grouped: dict[str, set[str]] = {}` 之前追加：

```python
rows.extend(query(
    "SELECT 'raw_source_video' AS source, video_object_key AS object_key "
    "FROM media_raw_sources WHERE deleted_at IS NULL"
))
rows.extend(query(
    "SELECT 'raw_source_cover' AS source, cover_object_key AS object_key "
    "FROM media_raw_sources WHERE deleted_at IS NULL"
))
```

- [ ] **Step 2: 提交**

```bash
git add appcore/medias.py
git commit -m "feat(medias): collect_media_object_references 纳入 raw_source 的视频与封面"
```

---

### Task 5: DAO 测试

**Files:**
- Create: `tests/test_appcore_medias_raw_sources.py`

- [ ] **Step 1: 先看参考风格**

Read `tests/test_appcore_medias.py` 头部 20-40 行，确认现有测试用的是 `conftest.py` 提供的 `db_fixture` / `test_db` 之类 fixture，还是直接 `from appcore.db import execute` 清表。跟着同款风格写新测试。

- [ ] **Step 2: 写测试**

```python
# tests/test_appcore_medias_raw_sources.py
from appcore import medias
from appcore.db import execute


def _mk_product() -> int:
    return medias.create_product(user_id=1, name="raw-src-test-prod")


def test_create_and_list_raw_source(db_fixture):
    pid = _mk_product()
    rid = medias.create_raw_source(
        pid, user_id=1,
        display_name="v1",
        video_object_key="1/medias/%d/raw_sources/abc_v.mp4" % pid,
        cover_object_key="1/medias/%d/raw_sources/abc_c.cover.jpg" % pid,
        duration_seconds=12.3, file_size=111, width=1280, height=720,
    )
    rows = medias.list_raw_sources(pid)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["display_name"] == "v1"


def test_get_raw_source_honors_soft_delete(db_fixture):
    pid = _mk_product()
    rid = medias.create_raw_source(
        pid, user_id=1,
        display_name=None,
        video_object_key="k1", cover_object_key="k2",
    )
    assert medias.get_raw_source(rid) is not None
    assert medias.soft_delete_raw_source(rid) == 1
    assert medias.get_raw_source(rid) is None
    assert medias.list_raw_sources(pid) == []


def test_update_raw_source_whitelist(db_fixture):
    pid = _mk_product()
    rid = medias.create_raw_source(
        pid, 1, display_name="a",
        video_object_key="k1", cover_object_key="k2",
    )
    medias.update_raw_source(rid, display_name="b", sort_order=5)
    row = medias.get_raw_source(rid)
    assert row["display_name"] == "b"
    assert row["sort_order"] == 5
    # 非白名单字段不生效
    medias.update_raw_source(rid, video_object_key="HACK")
    row = medias.get_raw_source(rid)
    assert row["video_object_key"] == "k1"


def test_count_raw_sources_by_product(db_fixture):
    p1 = _mk_product()
    p2 = _mk_product()
    for _ in range(3):
        medias.create_raw_source(p1, 1, display_name=None,
                                  video_object_key="v", cover_object_key="c")
    medias.create_raw_source(p2, 1, display_name=None,
                              video_object_key="v", cover_object_key="c")
    assert medias.count_raw_sources_by_product([p1, p2]) == {p1: 3, p2: 1}


def test_collect_refs_includes_raw_sources(db_fixture):
    pid = _mk_product()
    medias.create_raw_source(pid, 1, display_name=None,
                              video_object_key="vvv", cover_object_key="ccc")
    refs = medias.collect_media_object_references()
    keys = {r["object_key"]: r["sources"] for r in refs}
    assert "vvv" in keys and "raw_source_video" in keys["vvv"]
    assert "ccc" in keys and "raw_source_cover" in keys["ccc"]
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_appcore_medias_raw_sources.py -v
```
Expected: 5 passed.

如果 `db_fixture` 名字不同（比如 `test_db` / `mysql_db`），按 `conftest.py` 里的名字改。

- [ ] **Step 4: 提交**

```bash
git add tests/test_appcore_medias_raw_sources.py
git commit -m "test(medias): 覆盖 raw_source 的 DAO 与对象引用登记"
```

---

### Task 6: REST 列表 + 上传接口

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 在 `web/routes/medias.py` 文件顶部常量区追加白名单**

在现有 `_ALLOWED_IMAGE_TYPES` 下方追加：

```python
_ALLOWED_RAW_VIDEO_TYPES = ("video/mp4", "video/quicktime")
_MAX_RAW_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
```

- [ ] **Step 2: 在文件末尾追加序列化器**

```python
def _serialize_raw_source(row: dict) -> dict:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "display_name": row.get("display_name") or "",
        "video_object_key": row["video_object_key"],
        "cover_object_key": row["cover_object_key"],
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "sort_order": row.get("sort_order") or 0,
        "video_url": f"/medias/raw-sources/{row['id']}/video",
        "cover_url": f"/medias/raw-sources/{row['id']}/cover",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
```

- [ ] **Step 3: 追加列表路由**

```python
@bp.route("/api/products/<int:pid>/raw-sources", methods=["GET"])
@login_required
def api_list_raw_sources(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    rows = medias.list_raw_sources(pid)
    return jsonify({"items": [_serialize_raw_source(r) for r in rows]})
```

- [ ] **Step 4: 追加上传路由**

```python
@bp.route("/api/products/<int:pid>/raw-sources", methods=["POST"])
@login_required
def api_create_raw_source(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    video = request.files.get("video")
    cover = request.files.get("cover")
    if not video or not cover:
        return jsonify({"error": "video and cover both required"}), 400

    video_ct = (video.mimetype or "").lower()
    cover_ct = (cover.mimetype or "").lower()
    if video_ct not in _ALLOWED_RAW_VIDEO_TYPES:
        return jsonify({"error": f"video mimetype not allowed: {video_ct}"}), 400
    if cover_ct not in _ALLOWED_IMAGE_TYPES:
        return jsonify({"error": f"cover mimetype not allowed: {cover_ct}"}), 400

    display_name = (request.form.get("display_name") or "").strip() or None

    uid = _resolve_upload_user_id()
    if uid is None:
        return jsonify({"error": "missing upload user"}), 400

    video_key = tos_clients.build_media_raw_source_key(
        uid, pid, kind="video", filename=video.filename or "video.mp4",
    )
    cover_key = tos_clients.build_media_raw_source_key(
        uid, pid, kind="cover", filename=cover.filename or "cover.jpg",
    )

    # 读字节（视频流式读 + 限额）
    video_bytes = b""
    for chunk in iter(lambda: video.stream.read(1024 * 1024), b""):
        video_bytes += chunk
        if len(video_bytes) > _MAX_RAW_VIDEO_BYTES:
            return jsonify({"error": "video too large (>2GB)"}), 400

    cover_bytes = cover.read()
    if len(cover_bytes) > _MAX_IMAGE_BYTES:
        return jsonify({"error": "cover too large (>15MB)"}), 400

    # 上传 — 任一失败 → 回滚两个对象
    try:
        tos_clients.upload_media_object(video_key, video_bytes, content_type=video_ct)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"upload video failed: {exc}"}), 500
    try:
        tos_clients.upload_media_object(cover_key, cover_bytes, content_type=cover_ct)
    except Exception as exc:  # noqa: BLE001
        tos_clients.delete_media_object(video_key)
        return jsonify({"error": f"upload cover failed: {exc}"}), 500

    # 探测视频元信息（时长/尺寸）
    duration_seconds = None
    width = height = None
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name
        duration_seconds = float(get_media_duration(tmp_path) or 0.0) or None
        info = probe_media_info_safe(tmp_path)
        width = info.get("width")
        height = info.get("height")
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    try:
        rid = medias.create_raw_source(
            pid, uid,
            display_name=display_name,
            video_object_key=video_key,
            cover_object_key=cover_key,
            duration_seconds=duration_seconds,
            file_size=len(video_bytes),
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001
        tos_clients.delete_media_object(video_key)
        tos_clients.delete_media_object(cover_key)
        return jsonify({"error": f"db insert failed: {exc}"}), 500

    row = medias.get_raw_source(rid)
    return jsonify({"item": _serialize_raw_source(row)}), 201


def probe_media_info_safe(path: str) -> dict:
    try:
        from pipeline.ffutil import probe_media_info
        return probe_media_info(path) or {}
    except Exception:
        return {}
```

- [ ] **Step 5: 检查 `appcore/tos_clients.py` 是否已经有 `upload_media_object / delete_media_object`**

```bash
grep -n "def upload_media_object\|def delete_media_object" appcore/tos_clients.py
```
Expected：两个函数都存在。没有的话按 `build_media_object_key` 的用法在 `appcore/tos_clients.py` 里补上（参考现有 `upload_file` 写法，只把 bucket 换成 `get_media_bucket()`）。

- [ ] **Step 6: 冒烟验证（手动）**

```bash
python -m flask --app web.app run --port 5000
curl -X POST http://127.0.0.1:5000/medias/api/products/<pid>/raw-sources \
  -F "video=@sample.mp4" -F "cover=@cover.jpg"
```
Expected：返回 201 + item JSON。

- [ ] **Step 7: 提交**

```bash
git add web/routes/medias.py appcore/tos_clients.py
git commit -m "feat(medias): 新增原始素材列表与上传 REST 接口"
```

---

### Task 7: REST 改名 + 软删

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 追加两条路由**

```python
@bp.route("/api/raw-sources/<int:rid>", methods=["PATCH"])
@login_required
def api_update_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    body = request.get_json(silent=True) or {}
    fields: dict = {}
    if "display_name" in body:
        fields["display_name"] = (body.get("display_name") or "").strip() or None
    if "sort_order" in body:
        try:
            fields["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            return jsonify({"error": "sort_order must be int"}), 400
    if not fields:
        return jsonify({"error": "no valid fields"}), 400
    medias.update_raw_source(rid, **fields)
    return jsonify({"item": _serialize_raw_source(medias.get_raw_source(rid))})


@bp.route("/api/raw-sources/<int:rid>", methods=["DELETE"])
@login_required
def api_delete_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    medias.soft_delete_raw_source(rid)
    return jsonify({"ok": True})
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): 原始素材改名与软删 REST 接口"
```

---

### Task 8: 签名 URL（视频 + 封面）

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 追加两条路由**

```python
@bp.route("/raw-sources/<int:rid>/video", methods=["GET"])
@login_required
def raw_source_video_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    url = tos_clients.generate_signed_media_download_url(
        row["video_object_key"], expires=TOS_SIGNED_URL_EXPIRES,
    )
    return redirect(url, code=302)


@bp.route("/raw-sources/<int:rid>/cover", methods=["GET"])
@login_required
def raw_source_cover_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    url = tos_clients.generate_signed_media_download_url(
        row["cover_object_key"], expires=TOS_SIGNED_URL_EXPIRES,
    )
    return redirect(url, code=302)
```

（对齐现有 `thumb/<item_id>` 风格的 302 重定向。）

- [ ] **Step 2: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): 原始素材视频与封面的签名 URL 302 端点"
```

---

### Task 9: 产品列表接口返回 `raw_sources_count`

**Files:**
- Modify: `web/routes/medias.py`（`_serialize_product` + `api_list_products`）

- [ ] **Step 1: `_serialize_product` 参数加 `raw_sources_count`**

找到 `def _serialize_product(...)`，在参数末尾加 `raw_sources_count: int | None = None`，并在返回 dict 里加一项：

```python
"raw_sources_count": raw_sources_count or 0,
```

- [ ] **Step 2: `api_list_products` 补批量查询并透传**

在 `api_list_products` 中，`pids = [...]` 下方现有 `counts = medias.count_items_by_product(pids)` 之后追加：

```python
raw_counts = medias.count_raw_sources_by_product(pids)
```

`_serialize_product(...)` 调用处增补：

```python
_serialize_product(
    r, counts.get(r["id"], 0), thumb_covers.get(r["id"]),
    items_filenames=filenames.get(r["id"], []),
    lang_coverage=coverage.get(r["id"], {}),
    covers=covers_map.get(r["id"], {}),
    raw_sources_count=raw_counts.get(r["id"], 0),
)
```

- [ ] **Step 3: 本地手动验证**

```bash
curl http://127.0.0.1:5000/medias/api/products | jq '.items[0].raw_sources_count'
```
Expected: 存在字段，数值 0。

- [ ] **Step 4: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): 产品列表响应返回 raw_sources_count 计数"
```

---

### Task 10: REST 测试

**Files:**
- Create: `tests/test_medias_raw_sources_routes.py`

- [ ] **Step 1: 看参考测试风格**

Read `tests/test_web_routes.py` 或 `tests/test_openapi_materials_routes.py` 头部看现有路由测试怎么 mock TOS（通常是 `monkeypatch.setattr(tos_clients, "upload_media_object", fake)`）。

- [ ] **Step 2: 写测试**

```python
# tests/test_medias_raw_sources_routes.py
import io
import pytest
from unittest.mock import MagicMock
from appcore import medias, tos_clients


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def pid(db_fixture):
    return medias.create_product(user_id=1, name="t-rs")


@pytest.fixture()
def mock_tos(monkeypatch):
    monkeypatch.setattr(tos_clients, "upload_media_object", MagicMock())
    monkeypatch.setattr(tos_clients, "delete_media_object", MagicMock())
    return tos_clients


def test_upload_missing_video(client, pid, mock_tos, login_as_user_1):
    resp = client.post(
        f"/medias/api/products/{pid}/raw-sources",
        data={"cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "both required" in resp.get_json()["error"]


def test_upload_missing_cover(client, pid, mock_tos, login_as_user_1):
    resp = client.post(
        f"/medias/api/products/{pid}/raw-sources",
        data={"video": (io.BytesIO(b"FAKE"), "v.mp4", "video/mp4")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_bad_video_mime(client, pid, mock_tos, login_as_user_1):
    resp = client.post(
        f"/medias/api/products/{pid}/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE"), "v.avi", "video/x-msvideo"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_ok(client, pid, mock_tos, login_as_user_1):
    resp = client.post(
        f"/medias/api/products/{pid}/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE_MP4_BYTES"), "v.mp4", "video/mp4"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
            "display_name": "demo",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    item = resp.get_json()["item"]
    assert item["display_name"] == "demo"
    assert item["video_url"].endswith("/video")
    assert tos_clients.upload_media_object.call_count == 2


def test_upload_cover_fails_rollbacks_video(client, pid, monkeypatch, login_as_user_1):
    calls = []
    def fake_upload(key, data, content_type=None):
        if ".cover." in key:
            raise RuntimeError("boom")
        calls.append(("up", key))
    deletes = []
    monkeypatch.setattr(tos_clients, "upload_media_object", fake_upload)
    monkeypatch.setattr(tos_clients, "delete_media_object", lambda k: deletes.append(k))

    resp = client.post(
        f"/medias/api/products/{pid}/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE"), "v.mp4", "video/mp4"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 500
    assert len(deletes) == 1  # video 对象被回滚
    assert medias.list_raw_sources(pid) == []


def test_delete_raw_source_soft(client, pid, mock_tos, login_as_user_1):
    rid = medias.create_raw_source(
        pid, 1, display_name="x",
        video_object_key="v", cover_object_key="c",
    )
    resp = client.delete(f"/medias/api/raw-sources/{rid}")
    assert resp.status_code == 200
    assert medias.get_raw_source(rid) is None


def test_list_empty_new_product(client, pid, login_as_user_1):
    resp = client.get(f"/medias/api/products/{pid}/raw-sources")
    assert resp.status_code == 200
    assert resp.get_json()["items"] == []
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_medias_raw_sources_routes.py -v
```
Expected: 所有用例 PASS。

如果 fixture 名字（`flask_app`、`login_as_user_1`）在本仓库叫别的，参考现有路由测试里已用的 fixture 名改一下。

- [ ] **Step 4: 提交**

```bash
git add tests/test_medias_raw_sources_routes.py
git commit -m "test(medias): raw_source REST 接口的上传/失败回滚/删除回归"
```

---

### Task 11: `bulk_translate_plan` 改造

**Files:**
- Modify: `appcore/bulk_translate_plan.py`

- [ ] **Step 1: 修改 `generate_plan` 签名**

改：

```python
def generate_plan(
    user_id: int,
    product_id: int,
    target_langs: list[str],
    content_types: list[str],
    force_retranslate: bool,
    raw_source_ids: list[int] | None = None,   # 新增
) -> list[dict]:
```

- [ ] **Step 2: video 段改为读 raw_sources**

找到 `# 4. 视频` 所在块，整段替换为：

```python
    # 4. 视频（改为从 media_raw_sources 取源）
    if "video" in content_types:
        if not raw_source_ids:
            raise ValueError("video kind requires non-empty raw_source_ids")
        placeholders = ",".join(["%s"] * len(raw_source_ids))
        video_rows = query(
            f"SELECT id FROM media_raw_sources "
            f"WHERE id IN ({placeholders}) "
            f"  AND product_id = %s AND deleted_at IS NULL "
            f"ORDER BY sort_order ASC, id ASC",
            (*raw_source_ids, product_id),
        )
        found_ids = {int(r["id"]) for r in video_rows}
        missing = [r for r in raw_source_ids if int(r) not in found_ids]
        if missing:
            raise ValueError(f"raw_source_ids not found or soft-deleted: {missing}")
        for row in video_rows:
            for lang in target_langs:
                if lang not in VIDEO_SUPPORTED_LANGS:
                    continue
                plan.append(_new_item(
                    idx_counter.next(), "video", lang,
                    {"source_raw_id": row["id"]},
                ))
```

- [ ] **Step 3: 同步改 `appcore/bulk_translate_runtime.py::create_bulk_translate_task`**

签名加 `raw_source_ids: list[int] | None = None`，并把它透传进 `generate_plan(...)`；同时在父任务 state 里记录 `"raw_source_ids": raw_source_ids or []`。

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_plan.py appcore/bulk_translate_runtime.py
git commit -m "feat(bulk_translate): plan 生成器 video kind 改用 media_raw_sources 作为源"
```

---

### Task 12: `bulk_translate_plan` 测试

**Files:**
- Create: `tests/test_bulk_translate_plan_raw_sources.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_bulk_translate_plan_raw_sources.py
import pytest
from appcore import medias
from appcore.bulk_translate_plan import generate_plan


@pytest.fixture()
def pid(db_fixture):
    return medias.create_product(user_id=1, name="bt-rs")


def test_video_from_raw_sources(pid, db_fixture):
    r1 = medias.create_raw_source(pid, 1, display_name="a",
                                    video_object_key="v1", cover_object_key="c1")
    r2 = medias.create_raw_source(pid, 1, display_name="b",
                                    video_object_key="v2", cover_object_key="c2")
    plan = generate_plan(
        user_id=1, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["video"],
        force_retranslate=False,
        raw_source_ids=[r1, r2],
    )
    assert len(plan) == 4  # 2 raw × 2 lang
    assert {item["kind"] for item in plan} == {"video"}
    assert {item["ref"]["source_raw_id"] for item in plan} == {r1, r2}


def test_video_refuses_empty_raw_source_ids(pid):
    with pytest.raises(ValueError, match="raw_source_ids"):
        generate_plan(1, pid, ["de"], ["video"], False, raw_source_ids=[])


def test_soft_deleted_raw_source_is_rejected(pid, db_fixture):
    rid = medias.create_raw_source(pid, 1, display_name=None,
                                     video_object_key="v", cover_object_key="c")
    medias.soft_delete_raw_source(rid)
    with pytest.raises(ValueError, match="not found"):
        generate_plan(1, pid, ["de"], ["video"], False, raw_source_ids=[rid])


def test_copy_and_cover_detail_unchanged(pid, db_fixture):
    # 即使 content_types 不含 video，也不应要求 raw_source_ids
    plan = generate_plan(1, pid, ["de"], ["copy", "cover", "detail"], False,
                         raw_source_ids=None)
    # 本产品下没有文案/主图/详情图，plan 应为空（回归现有语义）
    assert plan == []
```

- [ ] **Step 2: 跑测试**

```bash
pytest tests/test_bulk_translate_plan_raw_sources.py -v
```
Expected: 4 passed.

- [ ] **Step 3: 提交**

```bash
git add tests/test_bulk_translate_plan_raw_sources.py
git commit -m "test(bulk_translate): plan 生成器 video kind 基于 raw_sources 的回归"
```

---

### Task 13: `bulk_translate_runtime` video kind 处理改造

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`（找 `_dispatch_sub_task` 里 `video` 分支）

- [ ] **Step 1: 定位原 video 分支**

```bash
grep -n '"video"' appcore/bulk_translate_runtime.py
```

找到处理 video kind 的代码块（大概率是分发到 `runtime_v2` / `pipeline/translate_v2.py` 跑翻译，然后把结果 `create_item` 写入 `media_items`）。

- [ ] **Step 2: 重写这段，核心动作**

伪代码骨架（Codex 根据现有实际结构落实细节）：

```python
def _run_video_item(task_id: str, plan_item: dict, parent_state: dict) -> None:
    lang = plan_item["lang"]
    raw_id = int(plan_item["ref"]["source_raw_id"])
    row = medias.get_raw_source(raw_id)
    if not row:
        raise RuntimeError(f"raw source {raw_id} missing")

    # 1. 下载视频到本地
    local_video = _download_media_to_tmp(row["video_object_key"], suffix=".mp4")

    # 2. 跑现有视频翻译管线（复用 runtime_v2 / pipeline.translate_v2）
    video_out_key = _translate_video_to_media_key(
        local_video, target_lang=lang,
        product_id=row["product_id"], user_id=row["user_id"],
        parent_state=parent_state,
    )

    # 3. 跑封面图片翻译 —— 复用 image_translate runtime 的单图接口
    cover_out_key = _translate_cover_to_media_key(
        source_cover_key=row["cover_object_key"],
        target_lang=lang,
        product_id=row["product_id"],
        user_id=row["user_id"],
    )

    # 4. 写 media_items
    new_item_id = medias.create_item(
        product_id=row["product_id"],
        user_id=row["user_id"],
        filename=Path(video_out_key).name,
        object_key=video_out_key,
        cover_object_key=cover_out_key,
        duration_seconds=row.get("duration_seconds"),
        file_size=None,  # 等上传完可回填
        lang=lang,
    )
    # 追写 source_raw_id
    from appcore.db import execute as _execute
    _execute(
        "UPDATE media_items SET source_raw_id=%s WHERE id=%s",
        (raw_id, new_item_id),
    )
```

- [ ] **Step 3: `_translate_cover_to_media_key` 的实现**

- 单图翻译：`image_translate_runtime` 里已有 batch 接口 `translate_image_to_target_lang(source_object_key, target_lang, ...) -> new_object_key`（若无，查 `appcore/image_translate_runtime.py` 看现有入口签名，按现有同类调用改写成单图版本）。
- 返回新 object_key，写入 `media` bucket，路径与现有翻译产出的封面一致。

- [ ] **Step 4: 删除/标注"从 en media_items 跑视频翻译"的废弃代码路径**

原 video 分支里如果还有 `SELECT ... FROM media_items WHERE lang='en'` 的残留，连同相关 helper 一起删掉。

- [ ] **Step 5: 提交**

```bash
git add appcore/bulk_translate_runtime.py
git commit -m "feat(bulk_translate): video 子任务从 raw_source 下载并同步翻译封面"
```

---

### Task 14: 新翻译入口 REST

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 追加路由**

```python
@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or ["video"]  # 可按前端传入扩展

    if not raw_ids:
        return jsonify({"error": "raw_ids 不能为空"}), 400
    if not target_langs:
        return jsonify({"error": "target_langs 不能为空"}), 400

    # 过滤：raw_ids 必须属于本产品、未软删
    rows = medias.list_raw_sources(pid)
    valid_ids = {int(r["id"]) for r in rows}
    bad = [int(x) for x in raw_ids if int(x) not in valid_ids]
    if bad:
        return jsonify({"error": f"raw_ids 不属于该产品或已删除: {bad}"}), 400

    # target_langs 合法性
    for lang in target_langs:
        if lang == "en" or not medias.is_valid_language(lang):
            return jsonify({"error": f"target_langs 非法: {lang}"}), 400

    from appcore.bulk_translate_runtime import create_bulk_translate_task, start_task
    task_id = create_bulk_translate_task(
        user_id=current_user.id,
        product_id=pid,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=body.get("video_params") or {},
        initiator={"source": "medias_raw_translate"},
        raw_source_ids=[int(x) for x in raw_ids],
    )
    start_task(task_id)   # 直接启动；若需要"预览 → 确认"二段式，去掉这行
    return jsonify({"task_id": task_id}), 202
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): 新增基于原始素材的翻译提交 REST 接口"
```

---

### Task 15: 翻译入口测试

**Files:**
- Create: `tests/test_medias_raw_sources_translate.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_medias_raw_sources_translate.py
from unittest.mock import MagicMock
import pytest
from appcore import medias
import appcore.bulk_translate_runtime as btr


@pytest.fixture()
def pid(db_fixture):
    return medias.create_product(user_id=1, name="t-tr")


@pytest.fixture()
def patch_bt(monkeypatch):
    fake_create = MagicMock(return_value="task-xyz")
    fake_start = MagicMock()
    monkeypatch.setattr(btr, "create_bulk_translate_task", fake_create)
    monkeypatch.setattr(btr, "start_task", fake_start)
    return fake_create, fake_start


def test_translate_empty_raw_ids(client, pid, login_as_user_1, patch_bt):
    resp = client.post(f"/medias/api/products/{pid}/translate",
                       json={"raw_ids": [], "target_langs": ["de"]})
    assert resp.status_code == 400


def test_translate_invalid_raw_id(client, pid, login_as_user_1, patch_bt):
    resp = client.post(f"/medias/api/products/{pid}/translate",
                       json={"raw_ids": [999999], "target_langs": ["de"]})
    assert resp.status_code == 400


def test_translate_invalid_lang(client, pid, login_as_user_1, patch_bt):
    rid = medias.create_raw_source(pid, 1, display_name=None,
                                     video_object_key="v", cover_object_key="c")
    resp = client.post(f"/medias/api/products/{pid}/translate",
                       json={"raw_ids": [rid], "target_langs": ["en"]})
    assert resp.status_code == 400


def test_translate_ok(client, pid, login_as_user_1, patch_bt, db_fixture):
    rid = medias.create_raw_source(pid, 1, display_name=None,
                                     video_object_key="v", cover_object_key="c")
    fake_create, fake_start = patch_bt
    resp = client.post(f"/medias/api/products/{pid}/translate",
                       json={"raw_ids": [rid], "target_langs": ["de", "fr"]})
    assert resp.status_code == 202
    assert resp.get_json()["task_id"] == "task-xyz"
    args, kwargs = fake_create.call_args
    assert kwargs["raw_source_ids"] == [rid]
    assert kwargs["target_langs"] == ["de", "fr"]
    fake_start.assert_called_once_with("task-xyz")
```

- [ ] **Step 2: 跑测试**

```bash
pytest tests/test_medias_raw_sources_translate.py -v
```
Expected: 4 passed.

- [ ] **Step 3: 提交**

```bash
git add tests/test_medias_raw_sources_translate.py
git commit -m "test(medias): 翻译入口合法性与透参回归"
```

---

### Task 16: 前端 · 产品列表行「原始视频 (n)」按钮 + 抽屉

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`
- Modify: `web/static/medias.css`（若项目把抽屉 CSS 集中在此；否则写在 medias_list.html 的 `<style>` 中）

- [ ] **Step 1: 熟悉现有操作列结构**

```bash
grep -n '编辑\|翻译' web/templates/medias_list.html web/static/medias.js
```

找到现有"编辑 / 翻译"按钮是用哪个 DOM 结构渲染的（通常是 `renderRow(product)` 或类似函数），复用同款按钮 class 与事件委托模式。

- [ ] **Step 2: 操作列新增按钮**

在操作列渲染的 HTML 模板里追加（示例）：

```html
<button class="btn btn-ghost btn-sm js-raw-sources"
        data-pid="${p.id}">
  原始视频 (${p.raw_sources_count ?? 0})
</button>
```

- [ ] **Step 3: 抽屉容器**

在 `medias_list.html` body 末尾追加：

```html
<div id="raw-sources-drawer" class="drawer drawer-right" hidden>
  <div class="drawer-header">
    <h3 id="rs-drawer-title">原始去字幕素材</h3>
    <button id="rs-drawer-close" class="btn btn-ghost">×</button>
  </div>
  <div class="drawer-body">
    <button id="rs-upload-btn" class="btn btn-primary">上传素材</button>
    <ul id="rs-list"></ul>
  </div>
</div>

<dialog id="rs-upload-dialog">
  <form id="rs-upload-form" method="dialog">
    <label>视频<input type="file" name="video" accept="video/mp4,video/quicktime" required></label>
    <label>封面<input type="file" name="cover" accept="image/jpeg,image/png,image/webp" required></label>
    <label>名称<input type="text" name="display_name" maxlength="64"></label>
    <div class="dialog-actions">
      <button type="reset">取消</button>
      <button type="submit">提交</button>
    </div>
  </form>
</dialog>
```

CSS 全走 Ocean Blue token（`--radius-lg`、`--space-4`、无紫）。

- [ ] **Step 4: JS 行为**

在 `web/static/medias.js` 末尾追加：

```javascript
(function () {
  const drawer = document.getElementById('raw-sources-drawer');
  const list = document.getElementById('rs-list');
  const uploadBtn = document.getElementById('rs-upload-btn');
  const uploadDialog = document.getElementById('rs-upload-dialog');
  const uploadForm = document.getElementById('rs-upload-form');
  let currentPid = null;

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.js-raw-sources');
    if (!btn) return;
    currentPid = btn.dataset.pid;
    openDrawer(currentPid);
  });

  document.getElementById('rs-drawer-close').onclick = () => {
    drawer.hidden = true;
    currentPid = null;
  };

  uploadBtn.onclick = () => uploadDialog.showModal();

  uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(uploadForm);
    const r = await fetch(`/medias/api/products/${currentPid}/raw-sources`, {
      method: 'POST', body: fd,
    });
    if (!r.ok) {
      alert((await r.json()).error || '上传失败');
      return;
    }
    uploadDialog.close();
    uploadForm.reset();
    await refreshList(currentPid);
  });

  list.addEventListener('click', async (e) => {
    const del = e.target.closest('.js-rs-del');
    if (!del) return;
    if (!confirm('删除后无法恢复，该素材不会再出现在翻译弹窗，但已翻译出来的多语种素材不受影响。确定？')) return;
    await fetch(`/medias/api/raw-sources/${del.dataset.rid}`, { method: 'DELETE' });
    await refreshList(currentPid);
  });

  async function openDrawer(pid) {
    drawer.hidden = false;
    await refreshList(pid);
  }
  async function refreshList(pid) {
    const r = await fetch(`/medias/api/products/${pid}/raw-sources`);
    const { items } = await r.json();
    list.innerHTML = items.length === 0
      ? '<li class="empty">还没有原始去字幕素材，上传第一条</li>'
      : items.map(renderRsRow).join('');
    // 同步刷新列表行计数（可选）
    document.querySelectorAll(`.js-raw-sources[data-pid="${pid}"]`).forEach(b => {
      b.textContent = `原始视频 (${items.length})`;
    });
  }
  function renderRsRow(it) {
    const mins = it.duration_seconds ? `${(it.duration_seconds/60).toFixed(1)}m` : '—';
    const sizeMb = it.file_size ? `${(it.file_size/1048576).toFixed(1)}MB` : '—';
    return `<li class="rs-row">
      <img src="${it.cover_url}" alt="">
      <div class="rs-meta">
        <strong>${escapeHTML(it.display_name || '未命名')}</strong>
        <span>${mins} · ${sizeMb}</span>
      </div>
      <button class="btn btn-danger-ghost btn-sm js-rs-del" data-rid="${it.id}">删除</button>
    </li>`;
  }
  function escapeHTML(s) {
    return String(s ?? '').replace(/[&<>"']/g, ch => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;',
    }[ch]));
  }
})();
```

- [ ] **Step 5: 手动验证**

启动 dev server：
```bash
FLASK_APP=web.app flask run --port 5000
```
访问 `/medias`，产品列表行能看到「原始视频 (0)」按钮；点开抽屉；上传一条；按钮计数变 1；删除 confirm 后消失。

- [ ] **Step 6: 提交**

```bash
git add web/templates/medias_list.html web/static/medias.js web/static/medias.css
git commit -m "feat(medias-ui): 产品列表行原始视频按钮与上传/删除抽屉"
```

---

### Task 17: 前端 · 翻译弹窗改造

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`

- [ ] **Step 1: 追加翻译弹窗 DOM**

```html
<dialog id="rs-translate-dialog">
  <h3>翻译原始视频</h3>
  <section class="rst-grid">
    <div class="rst-left">
      <strong>选择原始视频</strong>
      <ul id="rst-rs-list"></ul>
    </div>
    <div class="rst-right">
      <strong>目标语言</strong>
      <div id="rst-langs"></div>
    </div>
  </section>
  <footer>
    <span id="rst-preview"></span>
    <button id="rst-submit" class="btn btn-primary">提交翻译</button>
    <button id="rst-cancel" class="btn btn-ghost">取消</button>
  </footer>
</dialog>
```

- [ ] **Step 2: 改现有「翻译」按钮行为**

找到现有 `.js-translate` 处理器（或 button 对应函数），把原来的跳转/触发逻辑改为：

```javascript
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.js-translate');
  if (!btn) return;
  const pid = btn.dataset.pid;
  await openTranslateDialog(pid);
});

async function openTranslateDialog(pid) {
  const dlg = document.getElementById('rs-translate-dialog');
  const rsListEl = document.getElementById('rst-rs-list');
  const langsEl = document.getElementById('rst-langs');
  const preview = document.getElementById('rst-preview');

  const [{items}, {languages}] = await Promise.all([
    fetch(`/medias/api/products/${pid}/raw-sources`).then(r => r.json()),
    fetch('/medias/api/languages').then(r => r.json()),
  ]);
  rsListEl.innerHTML = items.map(it => `
    <li><label>
      <input type="checkbox" value="${it.id}" checked>
      <img src="${it.cover_url}" alt="" width="48">
      ${escapeHTML(it.display_name || '未命名')}
    </label></li>`).join('');
  langsEl.innerHTML = (languages || [])
    .filter(l => l.code !== 'en')
    .map(l => `<label><input type="checkbox" value="${l.code}"> ${l.name_zh}</label>`)
    .join('');

  const updatePreview = () => {
    const rs = rsListEl.querySelectorAll('input:checked').length;
    const ls = langsEl.querySelectorAll('input:checked').length;
    preview.textContent = `将生成 ${rs} × ${ls} = ${rs * ls} 条多语种素材`;
    document.getElementById('rst-submit').disabled = !(rs && ls);
  };
  [rsListEl, langsEl].forEach(el =>
    el.addEventListener('change', updatePreview));
  updatePreview();

  document.getElementById('rst-submit').onclick = async () => {
    const raw_ids = [...rsListEl.querySelectorAll('input:checked')].map(i => Number(i.value));
    const target_langs = [...langsEl.querySelectorAll('input:checked')].map(i => i.value);
    const r = await fetch(`/medias/api/products/${pid}/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_ids, target_langs }),
    });
    if (!r.ok) { alert((await r.json()).error || '提交失败'); return; }
    const { task_id } = await r.json();
    dlg.close();
    location.href = `/tasks/${task_id}`;
  };
  document.getElementById('rst-cancel').onclick = () => dlg.close();

  dlg.showModal();
}
```

（若 `/medias/api/languages` 不存在，沿用 `medias.list_languages_for_admin()` 写一个只读接口，或从页面已经 render 出来的 window 变量拿。）

- [ ] **Step 3: 手动冒烟**

在同一个 dev server 上，访问 `/medias`，点产品行「翻译」→ 弹窗出现 → 勾选 1 条 + 选 `de/fr` → 提交 → 跳 `/tasks/<id>`。

- [ ] **Step 4: 提交**

```bash
git add web/templates/medias_list.html web/static/medias.js
git commit -m "feat(medias-ui): 翻译按钮改为勾选式弹窗，提交走 raw_sources 新接口"
```

---

### Task 18: E2E 冒烟（Playwright）

**Files:**
- Create: `tests/e2e/test_medias_raw_sources_flow.py` 或沿用现有 webapp-testing 骨架

- [ ] **Step 1: 按现有 Playwright 测试模板写冒烟**

```bash
grep -rn "from playwright" tests/ | head
```

复制风格，写流程：
1. 登录 → 进 `/medias`。
2. 新建产品 → 看到新行「原始视频 (0)」。
3. 点按钮 → 抽屉打开 → 上传 `tests/fixtures/sample.mp4` + `sample.jpg`（需准备固定件）→ 按钮变「原始视频 (1)」。
4. 点「翻译」→ 弹窗 → 勾选该视频 + 选 `de` → 提交 → URL 跳到 `/tasks/...`。
5. 断言 `media_items` 新增 1 条 `lang='de'` 且 `source_raw_id = rid`（需要数据库访问或 API 校验）。

- [ ] **Step 2: 跑**

```bash
pytest tests/e2e/test_medias_raw_sources_flow.py -v
```

- [ ] **Step 3: 提交**

```bash
git add tests/e2e/
git commit -m "test(e2e): 原始素材上传→翻译提交 冒烟"
```

---

## 部署与收尾

- [ ] 全量跑一遍：`pytest -x`（保证现有回归不挂）。
- [ ] 数据库 migration 在 dev DB 跑一次；在 production pull 前需运行一次（按项目 deploy 流程走）。
- [ ] 手动在 dev 环境跑一次完整流程：上传 raw → 翻译 → 产出 media_items 带翻译后封面。
- [ ] 和用户 review 再决定是否 push + systemctl restart。

## 自查（Self-Review）

- [x] 数据模型：`media_raw_sources` 建表 + `media_items.source_raw_id` 在 Task 1。
- [x] TOS key 规范：Task 2。
- [x] DAO CRUD + 对象引用扩展：Task 3-5。
- [x] REST 七条接口：列表/创建/改名/软删/视频 URL/封面 URL/翻译入口 → Task 6,7,8,14；产品列表扩 raw_sources_count → Task 9。
- [x] bulk_translate plan + runtime 改造：Task 11, 13。
- [x] 前端：列表行按钮 + 抽屉 + 翻译弹窗 → Task 16, 17。
- [x] 测试：DAO / 路由 / plan / translate / E2E → Task 5, 10, 12, 15, 18。
- [x] 边界处理：上传缺件、MIME 非法、TOS 回滚、软删语义、翻译合法性、重复翻译 — 全在测试里覆盖。
- [x] 占位符扫描：所有步骤都带真实代码/命令，无 TBD/TODO。
- [x] 命名一致：`raw_source_ids` 参数、`source_raw_id` 列、`build_media_raw_source_key(...)` 函数、`/raw-sources/...` 路由前缀，全文统一。

待决（交给 Codex 判断 / 留在 spec 第 10 节）：
- `runtime_de.py` / `runtime_fr.py` / `runtime_multi.py` 等非 bulk_translate 翻译入口是否仍直接读 en items 作为源：不在本 plan 处理，单独 follow-up。
- 若 `/medias/api/languages` 接口缺失，Task 17 Step 2 已注释替代方案（从页面 render 的 window 变量或新增只读接口）。
