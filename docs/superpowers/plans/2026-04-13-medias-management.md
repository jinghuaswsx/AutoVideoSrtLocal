# 素材管理模块 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「素材管理」一级菜单，以产品为维度管理视频素材库，支持 TOS 独立桶 `video-save` 直传、产品文案编辑、居中弹窗编辑界面。

**Architecture:** 延续项目既有分层：`appcore/` 放数据访问和 TOS 辅助；`web/routes/medias.py` 蓝图提供页面+JSON API；`web/templates/` 放列表页与编辑弹窗；前端用原生 fetch（无框架）。TOS 走独立桶但复用账号凭证。缩略图通过已有 `pipeline/ffutil.py` 抽取。

**Tech Stack:** Flask + Jinja2 + pymysql + ve-tos-python-sdk + ffmpeg + 原生 JS/fetch

**Spec:** `docs/superpowers/specs/2026-04-13-medias-management-design.md`

---

## File Structure

**新建**
- `db/migrations/2026_04_13_add_medias_tables.sql` — 3 张表
- `appcore/medias.py` — DAO：产品/文案/素材增删改查
- `web/routes/medias.py` — 蓝图：页面 + API
- `web/templates/medias_list.html` — 列表页
- `web/templates/_medias_edit_modal.html` — 编辑弹窗模板（include）
- `web/static/medias.js` — 列表页和弹窗的前端逻辑
- `web/static/medias.css` — 模块样式
- `tests/web/test_medias_routes.py` — 路由测试
- `tests/appcore/test_medias_dao.py` — DAO 测试

**修改**
- `config.py` — 新增 `TOS_MEDIA_BUCKET`
- `.env.example` — 新增 `TOS_MEDIA_BUCKET=video-save`
- `appcore/tos_clients.py` — 新增 4 个 media 专用函数
- `web/app.py` — 注册 medias 蓝图
- `web/templates/layout.html` — 侧边栏加菜单项

---

## Task 1: 数据库迁移

**Files:**
- Create: `db/migrations/2026_04_13_add_medias_tables.sql`

- [ ] **Step 1: 编写迁移 SQL**

```sql
-- db/migrations/2026_04_13_add_medias_tables.sql
CREATE TABLE IF NOT EXISTS media_products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  color_people VARCHAR(64) DEFAULT NULL,
  source VARCHAR(64) DEFAULT NULL,
  importance TINYINT DEFAULT NULL,
  trend_score TINYINT DEFAULT NULL,
  selling_points TEXT DEFAULT NULL,
  archived TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at DATETIME DEFAULT NULL,
  KEY idx_user_deleted (user_id, deleted_at),
  KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS media_copywritings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  idx INT NOT NULL DEFAULT 1,
  title VARCHAR(500) DEFAULT NULL,
  body TEXT DEFAULT NULL,
  description VARCHAR(500) DEFAULT NULL,
  ad_carrier VARCHAR(255) DEFAULT NULL,
  ad_copy TEXT DEFAULT NULL,
  ad_keywords VARCHAR(500) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_product_idx (product_id, idx)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS media_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  user_id INT NOT NULL,
  filename VARCHAR(500) NOT NULL,
  display_name VARCHAR(255) DEFAULT NULL,
  object_key VARCHAR(500) NOT NULL,
  file_url VARCHAR(1000) DEFAULT NULL,
  thumbnail_path VARCHAR(500) DEFAULT NULL,
  duration_seconds FLOAT DEFAULT NULL,
  file_size BIGINT DEFAULT NULL,
  play_count INT NOT NULL DEFAULT 0,
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME DEFAULT NULL,
  KEY idx_product_deleted (product_id, deleted_at),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 应用迁移**

Run: `mysql -h <host> -u <user> -p <db> < db/migrations/2026_04_13_add_medias_tables.sql`
（本机走现有连接，生产走部署流程；Claude 可用 python + pymysql 直接执行 SQL 验证。）

Expected: 三张表出现在 `SHOW TABLES`。

- [ ] **Step 3: 验证**

```python
from appcore.db import query
print(query("SHOW TABLES LIKE 'media_%'"))
```
Expected: 3 行。

- [ ] **Step 4: Commit**

```bash
git add db/migrations/2026_04_13_add_medias_tables.sql
git commit -m "feat(medias): 新增 media_products/copywritings/items 三张表"
```

---

## Task 2: 配置项 + TOS 辅助函数

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `appcore/tos_clients.py`

- [ ] **Step 1: 加配置**

在 `config.py` 第 47 行附近（`TOS_BUCKET` 下一行）加：
```python
TOS_MEDIA_BUCKET = _env("TOS_MEDIA_BUCKET", "video-save")
```

`.env.example` 加一行：
```
TOS_MEDIA_BUCKET=video-save
```

- [ ] **Step 2: 扩展 tos_clients.py**

在 `appcore/tos_clients.py` 末尾（文件最后）加：

```python
def is_media_bucket_configured() -> bool:
    return is_tos_configured() and bool(config.TOS_MEDIA_BUCKET)


def build_media_object_key(user_id: int, product_id: int, filename: str) -> str:
    import uuid
    from datetime import datetime
    name = Path(filename or "media.bin").name
    date = datetime.now().strftime("%Y%m%d")
    return f"{user_id}/medias/{product_id}/{date}_{uuid.uuid4().hex[:8]}_{name}"


def generate_signed_media_upload_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Put,
        config.TOS_MEDIA_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def generate_signed_media_download_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Get,
        config.TOS_MEDIA_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def media_object_exists(object_key: str) -> bool:
    if not object_key:
        return False
    try:
        get_server_client().head_object(config.TOS_MEDIA_BUCKET, object_key)
    except Exception:
        return False
    return True


def head_media_object(object_key: str):
    return get_server_client().head_object(config.TOS_MEDIA_BUCKET, object_key)


def delete_media_object(object_key: str) -> None:
    if not object_key:
        return
    try:
        get_server_client().delete_object(config.TOS_MEDIA_BUCKET, object_key)
    except Exception:
        pass


def download_media_file(object_key: str, local_path: str) -> str:
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    get_server_client().get_object_to_file(config.TOS_MEDIA_BUCKET, object_key, str(destination))
    return str(destination)
```

- [ ] **Step 3: 烟测（无需正式单测）**

```python
from appcore import tos_clients
print(tos_clients.is_media_bucket_configured())
print(tos_clients.build_media_object_key(1, 42, "demo.mp4"))
```
Expected: 返回 True（若环境有 TOS 配置），路径格式 `1/medias/42/YYYYMMDD_xxxxxxxx_demo.mp4`。

- [ ] **Step 4: Commit**

```bash
git add config.py .env.example appcore/tos_clients.py
git commit -m "feat(medias): 新增 TOS 独立桶 video-save 配置与签名工具函数"
```

---

## Task 3: DAO 层 (appcore/medias.py)

**Files:**
- Create: `appcore/medias.py`
- Test: `tests/appcore/test_medias_dao.py`

- [ ] **Step 1: 写 DAO**

```python
# appcore/medias.py
"""素材管理 DAO：产品/文案/素材三张表的增删改查。"""
from __future__ import annotations
from typing import Any
from appcore.db import query, query_one, execute


# ---------- 产品 ----------

def create_product(user_id: int, name: str, color_people: str | None = None,
                   source: str | None = None) -> int:
    return execute(
        "INSERT INTO media_products (user_id, name, color_people, source) VALUES (%s,%s,%s,%s)",
        (user_id, name, color_people, source),
    )


def get_product(product_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE id=%s AND deleted_at IS NULL",
        (product_id,),
    )


def list_products(user_id: int | None, keyword: str = "", archived: bool = False,
                  offset: int = 0, limit: int = 20) -> tuple[list[dict], int]:
    where = ["deleted_at IS NULL"]
    args: list[Any] = []
    if user_id is not None:
        where.append("user_id=%s")
        args.append(user_id)
    where.append("archived=%s")
    args.append(1 if archived else 0)
    if keyword:
        where.append("(name LIKE %s OR color_people LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like])
    where_sql = " AND ".join(where)

    total_row = query_one(f"SELECT COUNT(*) AS c FROM media_products WHERE {where_sql}", tuple(args))
    total = int((total_row or {}).get("c") or 0)

    rows = query(
        f"SELECT * FROM media_products WHERE {where_sql} "
        "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        tuple(args + [limit, offset]),
    )
    return rows, total


def update_product(product_id: int, **fields) -> int:
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(fields[k] for k in keys) + (product_id,)
    return execute(f"UPDATE media_products SET {set_sql} WHERE id=%s", args)


def soft_delete_product(product_id: int) -> int:
    execute("UPDATE media_items SET deleted_at=NOW() WHERE product_id=%s AND deleted_at IS NULL",
            (product_id,))
    return execute("UPDATE media_products SET deleted_at=NOW() WHERE id=%s", (product_id,))


# ---------- 文案 ----------

def list_copywritings(product_id: int) -> list[dict]:
    return query(
        "SELECT * FROM media_copywritings WHERE product_id=%s ORDER BY idx ASC, id ASC",
        (product_id,),
    )


def replace_copywritings(product_id: int, items: list[dict]) -> None:
    """整体替换：删除所有旧文案，插入新列表。调用方保证事务语义足够弱。"""
    execute("DELETE FROM media_copywritings WHERE product_id=%s", (product_id,))
    for idx, item in enumerate(items, start=1):
        execute(
            "INSERT INTO media_copywritings "
            "(product_id, idx, title, body, description, ad_carrier, ad_copy, ad_keywords) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (product_id, idx,
             item.get("title"), item.get("body"), item.get("description"),
             item.get("ad_carrier"), item.get("ad_copy"), item.get("ad_keywords")),
        )


# ---------- 素材 ----------

def create_item(product_id: int, user_id: int, filename: str, object_key: str,
                display_name: str | None = None, file_url: str | None = None,
                thumbnail_path: str | None = None, duration_seconds: float | None = None,
                file_size: int | None = None) -> int:
    return execute(
        "INSERT INTO media_items "
        "(product_id, user_id, filename, display_name, object_key, file_url, "
        " thumbnail_path, duration_seconds, file_size) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (product_id, user_id, filename, display_name or filename, object_key,
         file_url, thumbnail_path, duration_seconds, file_size),
    )


def list_items(product_id: int) -> list[dict]:
    return query(
        "SELECT * FROM media_items WHERE product_id=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id,),
    )


def get_item(item_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_items WHERE id=%s AND deleted_at IS NULL",
        (item_id,),
    )


def soft_delete_item(item_id: int) -> int:
    return execute("UPDATE media_items SET deleted_at=NOW() WHERE id=%s", (item_id,))


def count_items_by_product(product_ids: list[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, COUNT(*) AS c FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"GROUP BY product_id",
        tuple(product_ids),
    )
    return {int(r["product_id"]): int(r["c"]) for r in rows}
```

- [ ] **Step 2: 写测试**

```python
# tests/appcore/test_medias_dao.py
import pytest
from appcore import medias


@pytest.fixture
def user_id():
    return 999001  # 测试专用，假设 users 表有该行或迁移时插入


def test_create_and_list_product(user_id):
    pid = medias.create_product(user_id, "测试产品 A", color_people="张三")
    assert pid > 0
    p = medias.get_product(pid)
    assert p["name"] == "测试产品 A"
    rows, total = medias.list_products(user_id, keyword="测试")
    assert any(r["id"] == pid for r in rows)
    assert total >= 1
    medias.soft_delete_product(pid)
    assert medias.get_product(pid) is None


def test_replace_copywritings(user_id):
    pid = medias.create_product(user_id, "文案测试")
    medias.replace_copywritings(pid, [
        {"title": "T1", "body": "B1"},
        {"title": "T2", "body": "B2"},
    ])
    cs = medias.list_copywritings(pid)
    assert [c["title"] for c in cs] == ["T1", "T2"]
    medias.replace_copywritings(pid, [{"title": "TOnly", "body": "BOnly"}])
    cs = medias.list_copywritings(pid)
    assert len(cs) == 1 and cs[0]["title"] == "TOnly"
    medias.soft_delete_product(pid)


def test_soft_delete_product_cascades_items(user_id):
    pid = medias.create_product(user_id, "级联测试")
    medias.create_item(pid, user_id, "a.mp4", "key/a")
    medias.create_item(pid, user_id, "b.mp4", "key/b")
    assert len(medias.list_items(pid)) == 2
    medias.soft_delete_product(pid)
    assert medias.list_items(pid) == []


def test_count_items_by_product(user_id):
    pid = medias.create_product(user_id, "计数测试")
    medias.create_item(pid, user_id, "a.mp4", "k1")
    medias.create_item(pid, user_id, "b.mp4", "k2")
    counts = medias.count_items_by_product([pid])
    assert counts[pid] == 2
    medias.soft_delete_product(pid)
```

- [ ] **Step 3: 跑测试**

Run: `pytest tests/appcore/test_medias_dao.py -v`
Expected: 4 passed。

若 `user_id=999001` 不存在且有外键/存在性约束导致失败，改用 `appcore.db` 直接 `SELECT id FROM users LIMIT 1` 取一个真实 id。

- [ ] **Step 4: Commit**

```bash
git add appcore/medias.py tests/appcore/test_medias_dao.py
git commit -m "feat(medias): 新增素材管理 DAO 与单元测试"
```

---

## Task 4: 后端路由蓝图

**Files:**
- Create: `web/routes/medias.py`
- Modify: `web/app.py`

- [ ] **Step 1: 写蓝图**

```python
# web/routes/medias.py
from __future__ import annotations

import os
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify, abort, send_file
from flask_login import login_required, current_user

from appcore import medias, tos_clients
from appcore.db import query_one
from config import OUTPUT_DIR, TOS_MEDIA_BUCKET, TOS_REGION, TOS_PUBLIC_ENDPOINT, TOS_SIGNED_URL_EXPIRES
from pipeline.ffutil import extract_thumbnail, get_media_duration

bp = Blueprint("medias", __name__, url_prefix="/medias")

THUMB_DIR = Path(OUTPUT_DIR) / "media_thumbs"


def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


def _can_access_product(product: dict, write: bool = False) -> bool:
    if not product:
        return False
    if product["user_id"] == current_user.id:
        return True
    if _is_admin() and not write:
        return True
    return False


def _serialize_product(p: dict, items_count: int | None = None) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
    }


def _serialize_item(it: dict) -> dict:
    return {
        "id": it["id"],
        "filename": it["filename"],
        "display_name": it.get("display_name") or it["filename"],
        "object_key": it["object_key"],
        "thumbnail_url": f"/medias/thumb/{it['id']}" if it.get("thumbnail_path") else None,
        "duration_seconds": it.get("duration_seconds"),
        "file_size": it.get("file_size"),
        "created_at": it["created_at"].isoformat() if it.get("created_at") else None,
    }


# ---------- 页面 ----------

@bp.route("/")
@login_required
def index():
    return render_template(
        "medias_list.html",
        tos_ready=tos_clients.is_media_bucket_configured(),
        is_admin=_is_admin(),
    )


# ---------- 产品 API ----------

@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    scope_all = request.args.get("scope") == "all" and _is_admin()
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    user_id = None if scope_all else current_user.id
    rows, total = medias.list_products(user_id, keyword=keyword, archived=archived,
                                        offset=offset, limit=limit)
    counts = medias.count_items_by_product([r["id"] for r in rows])
    data = [_serialize_product(r, counts.get(r["id"], 0)) for r in rows]
    return jsonify({"items": data, "total": total, "page": page, "page_size": limit})


@bp.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    pid = medias.create_product(
        current_user.id, name,
        color_people=(body.get("color_people") or None),
        source=(body.get("source") or None),
    )
    return jsonify({"id": pid}), 201


@bp.route("/api/products/<int:pid>", methods=["GET"])
@login_required
def api_get_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    return jsonify({
        "product": _serialize_product(p, None),
        "copywritings": medias.list_copywritings(pid),
        "items": [_serialize_item(i) for i in medias.list_items(pid)],
    })


@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    medias.update_product(pid,
                          name=body.get("name") or p["name"],
                          color_people=body.get("color_people"),
                          source=body.get("source"))
    if isinstance(body.get("copywritings"), list):
        medias.replace_copywritings(pid, body["copywritings"])
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    medias.soft_delete_product(pid)
    return jsonify({"ok": True})


# ---------- 素材上传 ----------

@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    filename = os.path.basename((body.get("filename") or "").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = tos_clients.build_media_object_key(current_user.id, pid, filename)
    return jsonify({
        "object_key": object_key,
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
        "bucket": TOS_MEDIA_BUCKET,
        "region": TOS_REGION,
        "endpoint": TOS_PUBLIC_ENDPOINT,
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })


@bp.route("/api/products/<int:pid>/items/complete", methods=["POST"])
@login_required
def api_item_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key = (body.get("object_key") or "").strip()
    filename = (body.get("filename") or "").strip()
    file_size = int(body.get("file_size") or 0)
    if not object_key or not filename:
        return jsonify({"error": "object_key and filename required"}), 400
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    # 先入库拿到 item_id，再抽缩略图
    item_id = medias.create_item(
        pid, current_user.id, filename, object_key,
        file_size=file_size or None,
    )

    # 抽缩略图（下载到临时文件）
    try:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(exist_ok=True)
        tmp_video = product_dir / f"tmp_{item_id}_{Path(filename).name}"
        tos_clients.download_media_file(object_key, str(tmp_video))

        duration = get_media_duration(str(tmp_video))
        thumb = extract_thumbnail(str(tmp_video), str(product_dir), scale="360:-1")
        if thumb:
            final = product_dir / f"{item_id}.jpg"
            os.replace(thumb, final)
            from appcore.db import execute as _exec
            _exec(
                "UPDATE media_items SET thumbnail_path=%s, duration_seconds=%s WHERE id=%s",
                (str(final.relative_to(OUTPUT_DIR)), duration or None, item_id),
            )
        try:
            tmp_video.unlink()
        except Exception:
            pass
    except Exception:
        pass  # 缩略图失败不阻断入库

    return jsonify({"id": item_id}), 201


@bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_item(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p, write=True):
        abort(404)
    medias.soft_delete_item(item_id)
    try:
        tos_clients.delete_media_object(it["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


# ---------- 缩略图代理 ----------

@bp.route("/thumb/<int:item_id>")
@login_required
def thumb(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    if not it.get("thumbnail_path"):
        abort(404)
    full = Path(OUTPUT_DIR) / it["thumbnail_path"]
    if not full.exists():
        abort(404)
    return send_file(str(full), mimetype="image/jpeg")


# ---------- 签名下载（播放） ----------

@bp.route("/api/items/<int:item_id>/play_url")
@login_required
def api_play_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    url = tos_clients.generate_signed_media_download_url(it["object_key"])
    return jsonify({"url": url})
```

- [ ] **Step 2: 注册蓝图**

在 `web/app.py` 第 38 行附近加 import：
```python
from web.routes.medias import bp as medias_bp
```
在第 79 行 `app.register_blueprint(fr_translate_bp)` 下方加：
```python
app.register_blueprint(medias_bp)
```

- [ ] **Step 3: 冒烟**

启动应用（用户现有启动方式，如 `python main.py` 或 `flask run`），访问 `/medias/api/products`：

```bash
curl -b "session=<cookie>" http://localhost:5000/medias/api/products
```
Expected: `{"items": [], "total": 0, "page": 1, "page_size": 20}`

- [ ] **Step 4: Commit**

```bash
git add web/routes/medias.py web/app.py
git commit -m "feat(medias): 新增素材管理后端蓝图与 API"
```

---

## Task 5: 前端列表页 + 样式

**Files:**
- Create: `web/templates/medias_list.html`
- Create: `web/static/medias.css`
- Create: `web/static/medias.js`

- [ ] **Step 1: 写 medias_list.html**

```html
{% extends "layout.html" %}
{% block title %}素材管理 - AutoVideoSrt{% endblock %}
{% block page_title %}素材管理{% endblock %}
{% block extra_style %}
<link rel="stylesheet" href="{{ url_for('static', filename='medias.css') }}">
{% endblock %}
{% block content %}
<div class="medias-wrap">
  {% if not tos_ready %}
  <div class="medias-banner">素材上传需要先配置 TOS_MEDIA_BUCKET，请联系管理员。</div>
  {% endif %}

  <div class="medias-toolbar">
    <input id="kw" class="medias-input" placeholder="关键词：产品名/色号人">
    <label class="medias-check"><input type="checkbox" id="archived"> 已归档</label>
    {% if is_admin %}
    <label class="medias-check"><input type="checkbox" id="scopeAll"> 查看全部</label>
    {% endif %}
    <button class="btn btn-ghost" id="searchBtn">搜索</button>
    <span style="flex:1"></span>
    <button class="btn btn-primary" id="createBtn">+ 添加产品素材</button>
  </div>

  <table class="medias-table">
    <thead>
      <tr>
        <th>ID</th><th>产品名称</th><th>素材数</th><th>来源</th>
        <th>创建时间</th><th>修改时间</th><th>操作</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  <div id="pager" class="medias-pager"></div>
</div>

{% include "_medias_edit_modal.html" %}

<script>
  window.MEDIAS_TOS_READY = {{ 'true' if tos_ready else 'false' }};
  window.MEDIAS_IS_ADMIN = {{ 'true' if is_admin else 'false' }};
</script>
<script src="{{ url_for('static', filename='medias.js') }}"></script>
{% endblock %}
```

- [ ] **Step 2: 写 _medias_edit_modal.html**

```html
<!-- web/templates/_medias_edit_modal.html -->
<div class="medias-modal-mask" id="editMask" style="display:none">
  <div class="medias-modal">
    <div class="medias-modal-head">
      <span id="modalTitle">编辑素材</span>
      <button class="medias-modal-close" id="modalClose">×</button>
    </div>
    <div class="medias-modal-body">
      <label class="medias-label">产品名称 *</label>
      <input id="mName" class="medias-input">

      <div class="medias-row">
        <div class="medias-col">
          <label class="medias-label">色号人</label>
          <input id="mColor" class="medias-input">
        </div>
        <div class="medias-col">
          <label class="medias-label">来源</label>
          <input id="mSource" class="medias-input">
        </div>
      </div>

      <label class="medias-label">文案</label>
      <div id="cwList" class="medias-cw-list"></div>
      <button class="btn btn-ghost btn-sm" id="cwAddBtn">+ 添加文案条目</button>

      <label class="medias-label" style="margin-top:18px">视频素材</label>
      <div id="itemsGrid" class="medias-items-grid"></div>
      <div id="uploadArea">
        <input type="file" id="fileInput" multiple accept="video/*" style="display:none">
        <button class="btn btn-ghost" id="uploadBtn">+ 上传更多素材</button>
        <div id="uploadProgress" class="medias-upload-progress"></div>
      </div>
    </div>
    <div class="medias-modal-foot">
      <button class="btn btn-ghost" id="cancelBtn">取消</button>
      <button class="btn btn-primary" id="saveBtn">保存</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 写 medias.css**

```css
/* web/static/medias.css */
.medias-wrap { display:flex; flex-direction:column; gap:16px; }
.medias-banner { background:#fff7ed; color:#9a3412; padding:10px 14px; border-radius:10px; border:1px solid #fed7aa; }
.medias-toolbar { display:flex; gap:10px; align-items:center; background:var(--bg-card); padding:12px 14px; border-radius:12px; border:1px solid var(--border-main); }
.medias-input { padding:8px 12px; border:1px solid var(--border-main); border-radius:8px; background:var(--bg-card); color:var(--text-main); font-size:13px; min-width:220px; }
.medias-check { font-size:13px; color:var(--text-main); display:flex; align-items:center; gap:6px; cursor:pointer; }
.medias-table { width:100%; border-collapse:collapse; background:var(--bg-card); border-radius:12px; overflow:hidden; border:1px solid var(--border-main); }
.medias-table th, .medias-table td { padding:10px 12px; text-align:left; font-size:13px; border-bottom:1px solid var(--border-main); }
.medias-table th { background:var(--bg-user-badge); color:var(--text-user-badge); font-weight:600; }
.medias-table tbody tr:hover { background:rgba(14,165,233,0.05); }
.medias-pill { display:inline-block; padding:2px 10px; border-radius:999px; background:#dcfce7; color:#15803d; font-size:12px; }
.medias-count { display:inline-flex; min-width:28px; height:24px; align-items:center; justify-content:center; border-radius:12px; background:#dbeafe; color:#1d4ed8; font-weight:700; font-size:12px; padding:0 8px; }
.medias-pager { display:flex; justify-content:center; gap:6px; padding:10px 0; }
.medias-pager button { padding:4px 10px; border:1px solid var(--border-main); background:var(--bg-card); color:var(--text-main); border-radius:6px; cursor:pointer; font-size:13px; }
.medias-pager button.active { background:var(--primary-color); color:#fff; border-color:var(--primary-color); }

/* Modal */
.medias-modal-mask { position:fixed; inset:0; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:200; }
.medias-modal { background:var(--bg-card); color:var(--text-main); width:800px; max-width:92vw; max-height:90vh; display:flex; flex-direction:column; border-radius:14px; overflow:hidden; box-shadow:0 20px 60px rgba(0,0,0,0.3); }
.medias-modal-head { padding:16px 20px; border-bottom:1px solid var(--border-main); display:flex; justify-content:space-between; align-items:center; font-size:16px; font-weight:700; }
.medias-modal-close { background:transparent; border:none; font-size:22px; cursor:pointer; color:var(--text-main); }
.medias-modal-body { padding:18px 22px; overflow-y:auto; flex:1; }
.medias-modal-foot { padding:12px 20px; border-top:1px solid var(--border-main); display:flex; justify-content:flex-end; gap:10px; }
.medias-label { display:block; font-size:12px; font-weight:600; color:var(--text-user-badge); margin:14px 0 6px; }
.medias-row { display:flex; gap:12px; }
.medias-col { flex:1; }
.medias-cw-list { display:flex; flex-direction:column; gap:12px; }
.medias-cw-card { border:1px solid var(--border-main); border-radius:10px; padding:12px; background:var(--bg-body); position:relative; }
.medias-cw-card .medias-cw-remove { position:absolute; top:8px; right:8px; background:#fee2e2; color:#dc2626; border:none; border-radius:50%; width:24px; height:24px; cursor:pointer; }
.medias-cw-card .medias-cw-index { font-weight:700; color:var(--primary-color); margin-bottom:6px; }
.medias-cw-card input, .medias-cw-card textarea { width:100%; padding:6px 10px; border:1px solid var(--border-main); border-radius:6px; font-size:13px; margin-bottom:6px; background:var(--bg-card); color:var(--text-main); font-family:inherit; box-sizing:border-box; }
.medias-cw-card textarea { min-height:56px; resize:vertical; }
.medias-items-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:10px; margin-bottom:10px; }
.medias-item-card { position:relative; border:1px solid var(--border-main); border-radius:8px; overflow:hidden; background:var(--bg-body); }
.medias-item-card img { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; background:#000; }
.medias-item-card .medias-item-name { font-size:12px; padding:6px 8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.medias-item-card .medias-item-remove { position:absolute; top:4px; right:4px; background:rgba(0,0,0,0.6); color:#fff; border:none; border-radius:50%; width:22px; height:22px; cursor:pointer; }
.medias-upload-progress { display:flex; flex-direction:column; gap:4px; margin-top:8px; font-size:12px; color:var(--text-user-badge); }
```

- [ ] **Step 4: 写 medias.js**

```javascript
// web/static/medias.js
(function() {
  const state = { page: 1, current: null };  // current = { product, copywritings, items }

  const $ = (id) => document.getElementById(id);

  async function fetchJSON(url, opts) {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function fmtDate(s) {
    if (!s) return '';
    const d = new Date(s);
    return d.toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-');
  }

  async function loadList() {
    const kw = $('kw').value.trim();
    const archived = $('archived').checked;
    const scopeAll = window.MEDIAS_IS_ADMIN && $('scopeAll') && $('scopeAll').checked;
    const params = new URLSearchParams({ page: state.page });
    if (kw) params.set('keyword', kw);
    if (archived) params.set('archived', '1');
    if (scopeAll) params.set('scope', 'all');
    const data = await fetchJSON('/medias/api/products?' + params);
    renderRows(data.items);
    renderPager(data.total, data.page, data.page_size);
  }

  function renderRows(items) {
    const tb = $('tbody');
    tb.innerHTML = items.map(p => `
      <tr>
        <td>${p.id}</td>
        <td><div>${escapeHtml(p.name)}</div><div style="color:#9ca3af;font-size:12px">色号人: ${escapeHtml(p.color_people || '-')}</div></td>
        <td><span class="medias-count">${p.items_count || 0}</span></td>
        <td>${p.source ? `<span class="medias-pill">${escapeHtml(p.source)}</span>` : '-'}</td>
        <td>${fmtDate(p.created_at)}</td>
        <td>${fmtDate(p.updated_at)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" data-edit="${p.id}">编辑</button>
          <button class="btn btn-sm" style="background:#fee2e2;color:#dc2626" data-del="${p.id}">删除</button>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="7" style="text-align:center;padding:40px;color:#9ca3af">暂无产品</td></tr>';
    tb.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => openEdit(+b.dataset.edit)));
    tb.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => deleteProduct(+b.dataset.del)));
  }

  function renderPager(total, page, pageSize) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const p = $('pager');
    let html = '';
    for (let i = 1; i <= pages; i++) {
      html += `<button class="${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`;
    }
    p.innerHTML = html;
    p.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => {
      state.page = +b.dataset.page; loadList();
    }));
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function deleteProduct(pid) {
    if (!confirm('确认删除该产品及其所有素材？')) return;
    await fetch('/medias/api/products/' + pid, { method: 'DELETE' });
    loadList();
  }

  // ========== 编辑弹窗 ==========

  async function openEdit(pid) {
    const data = await fetchJSON('/medias/api/products/' + pid);
    state.current = data;
    $('modalTitle').textContent = '编辑素材';
    $('mName').value = data.product.name || '';
    $('mColor').value = data.product.color_people || '';
    $('mSource').value = data.product.source || '';
    renderCopywritings(data.copywritings);
    renderItems(data.items);
    $('editMask').style.display = 'flex';
  }

  function openCreate() {
    state.current = { product: null, copywritings: [], items: [] };
    $('modalTitle').textContent = '添加产品素材';
    $('mName').value = ''; $('mColor').value = ''; $('mSource').value = '';
    renderCopywritings([]);
    renderItems([]);
    $('editMask').style.display = 'flex';
  }

  function closeModal() { $('editMask').style.display = 'none'; state.current = null; }

  function renderCopywritings(list) {
    const box = $('cwList');
    box.innerHTML = '';
    list.forEach((c, i) => box.appendChild(cwCard(c, i + 1)));
  }

  function cwCard(c, idx) {
    const d = document.createElement('div');
    d.className = 'medias-cw-card';
    d.innerHTML = `
      <div class="medias-cw-index">#${idx}</div>
      <button class="medias-cw-remove" type="button">×</button>
      <input data-field="title" placeholder="标题" value="${escapeHtml(c.title || '')}">
      <textarea data-field="body" placeholder="正文">${escapeHtml(c.body || '')}</textarea>
      <input data-field="description" placeholder="描述" value="${escapeHtml(c.description || '')}">
      <input data-field="ad_carrier" placeholder="广告媒体库" value="${escapeHtml(c.ad_carrier || '')}">
      <textarea data-field="ad_copy" placeholder="广告文案">${escapeHtml(c.ad_copy || '')}</textarea>
      <input data-field="ad_keywords" placeholder="广告词" value="${escapeHtml(c.ad_keywords || '')}">
    `;
    d.querySelector('.medias-cw-remove').addEventListener('click', () => { d.remove(); reindexCw(); });
    return d;
  }

  function reindexCw() {
    [...document.querySelectorAll('.medias-cw-card .medias-cw-index')].forEach((e, i) => e.textContent = '#' + (i + 1));
  }

  function collectCopywritings() {
    return [...document.querySelectorAll('.medias-cw-card')].map(card => {
      const o = {};
      card.querySelectorAll('[data-field]').forEach(el => { o[el.dataset.field] = el.value || null; });
      return o;
    });
  }

  function renderItems(items) {
    const g = $('itemsGrid');
    g.innerHTML = items.map(it => `
      <div class="medias-item-card" data-item="${it.id}">
        ${it.thumbnail_url ? `<img src="${it.thumbnail_url}">` : `<div style="aspect-ratio:16/9;background:#1f2937;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:12px">视频</div>`}
        <div class="medias-item-name">${escapeHtml(it.display_name || it.filename)}</div>
        <button class="medias-item-remove" type="button" title="删除">×</button>
      </div>
    `).join('');
    g.querySelectorAll('[data-item]').forEach(card => {
      card.querySelector('.medias-item-remove').addEventListener('click', () => removeItem(+card.dataset.item, card));
    });
  }

  async function removeItem(itemId, card) {
    if (!confirm('确认删除该素材？')) return;
    await fetch('/medias/api/items/' + itemId, { method: 'DELETE' });
    card.remove();
  }

  // ========== 上传 ==========

  async function ensureProductIdForUpload() {
    if (state.current && state.current.product && state.current.product.id) return state.current.product.id;
    const name = $('mName').value.trim();
    if (!name) { alert('请先填写产品名称'); return null; }
    const res = await fetchJSON('/medias/api/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, color_people: $('mColor').value || null, source: $('mSource').value || null,
      }),
    });
    // 转为编辑态
    const full = await fetchJSON('/medias/api/products/' + res.id);
    state.current = full;
    $('modalTitle').textContent = '编辑素材';
    return res.id;
  }

  async function uploadFiles(files) {
    if (!window.MEDIAS_TOS_READY) { alert('TOS 未配置，无法上传'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    const box = $('uploadProgress'); box.innerHTML = '';
    for (const f of files) {
      const row = document.createElement('div'); row.textContent = `${f.name} · 上传中…`; box.appendChild(row);
      try {
        const boot = await fetchJSON(`/medias/api/products/${pid}/items/bootstrap`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: f.name }),
        });
        const putRes = await fetch(boot.upload_url, { method: 'PUT', body: f });
        if (!putRes.ok) throw new Error('TOS 上传失败');
        await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ object_key: boot.object_key, filename: f.name, file_size: f.size }),
        });
        row.textContent = `${f.name} · 完成`;
      } catch (e) {
        row.textContent = `${f.name} · 失败：${e.message}`;
      }
    }
    // 刷新素材列表
    const full = await fetchJSON('/medias/api/products/' + pid);
    state.current = full;
    renderItems(full.items);
    loadList();
  }

  // ========== 保存 ==========

  async function save() {
    const name = $('mName').value.trim();
    if (!name) { alert('产品名称必填'); return; }
    const pid = await ensureProductIdForUpload();
    if (!pid) return;
    await fetchJSON('/medias/api/products/' + pid, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, color_people: $('mColor').value || null, source: $('mSource').value || null,
        copywritings: collectCopywritings(),
      }),
    });
    closeModal();
    loadList();
  }

  // ========== 绑定 ==========

  document.addEventListener('DOMContentLoaded', () => {
    $('searchBtn').addEventListener('click', () => { state.page = 1; loadList(); });
    $('kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; loadList(); } });
    $('archived').addEventListener('change', () => { state.page = 1; loadList(); });
    if ($('scopeAll')) $('scopeAll').addEventListener('change', () => { state.page = 1; loadList(); });
    $('createBtn').addEventListener('click', openCreate);
    $('modalClose').addEventListener('click', closeModal);
    $('cancelBtn').addEventListener('click', closeModal);
    $('saveBtn').addEventListener('click', save);
    $('cwAddBtn').addEventListener('click', () => {
      $('cwList').appendChild(cwCard({}, $('cwList').children.length + 1));
    });
    $('uploadBtn').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', (e) => {
      const files = [...e.target.files];
      e.target.value = '';
      if (files.length) uploadFiles(files);
    });
    loadList();
  });
})();
```

- [ ] **Step 5: 侧边栏加菜单**

修改 `web/templates/layout.html` 第 314 行「视频评分」`<a>` 之后加：

```html
<a href="/medias" {% if request.path.startswith('/medias') %}class="active"{% endif %}>
  <span class="nav-icon">📦</span> 素材管理
</a>
```

- [ ] **Step 6: Commit**

```bash
git add web/templates/medias_list.html web/templates/_medias_edit_modal.html \
        web/static/medias.css web/static/medias.js web/templates/layout.html
git commit -m "feat(medias): 新增素材管理前端页面与侧边栏入口"
```

---

## Task 6: 冒烟测试与收尾

- [ ] **Step 1: 启动应用**

运行项目现有启动方式（`python main.py` 或部署脚本）。

- [ ] **Step 2: 手工验证清单**

访问 `http://localhost:<port>/medias`，逐项确认：

1. ✅ 侧边栏出现「📦 素材管理」，点击进入 `/medias`
2. ✅ 空列表时显示「暂无产品」
3. ✅ 若 TOS_MEDIA_BUCKET 未配置，顶部有黄色横幅提示
4. ✅ 点击「+ 添加产品素材」→ 弹窗出现（居中、半透明遮罩）
5. ✅ 填写产品名 + 选 1 个 mp4 → 上传进度显示「上传中…」→「完成」
6. ✅ 保存后列表出现新行，素材数 = 1
7. ✅ 点编辑 → 弹窗回填所有字段 → 缩略图显示
8. ✅ 加 1 条文案 → 保存 → 重新打开文案还在
9. ✅ 删除素材 → 弹窗确认 → 缩略图消失
10. ✅ 删除产品 → 列表行消失
11. ✅ 搜索关键词 → 命中 → 重置后回到全量
12. ✅ 管理员账号登录 → 勾选「查看全部」→ 其他用户的产品出现

- [ ] **Step 3: 如有 bug 修复再 commit**

- [ ] **Step 4: 收尾 commit（若有剩余 UI 微调）**

```bash
git add -A
git commit -m "fix(medias): 冒烟测试发现的 UI/逻辑问题修复"
```

---

## Self-Review

**Spec 覆盖核对：**

| Spec 要求 | 对应 Task |
|---|---|
| 3 张数据表 | Task 1 |
| TOS_MEDIA_BUCKET 独立桶 + 4 个 media 函数 | Task 2 |
| DAO 层（产品/文案/素材） | Task 3 |
| 8 个路由接口 | Task 4 |
| 缩略图抽取（ffutil 复用） | Task 4 Step 1（api_item_complete） |
| 权限（普通 vs admin + scope=all） | Task 4（`_can_access_product`, `scope_all`） |
| TOS 未配置时横幅降级 | Task 4 `index()` + Task 5 modal |
| 列表页 + 居中弹窗 | Task 5 |
| 侧边栏菜单项 | Task 5 Step 5 |
| 冒烟 12 项 | Task 6 |

所有 spec 章节都有对应任务。

**类型一致性：** 后端 `_serialize_item` 的键 (`id/filename/display_name/object_key/thumbnail_url/duration_seconds/file_size/created_at`) 与前端 `renderItems` 使用的字段一致。产品序列化键同理。

**无占位符：** 每步都有具体代码或具体命令。
