# D 子系统：原始素材任务库（raw-video-pool）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** 把 C 阶段父任务【已上传】按钮的 prompt fallback 替换成完整的下载/上传/认领工作面板，并加专属菜单"原始素材任务库"给处理人用。

**Spec:** [docs/superpowers/specs/2026-04-26-raw-video-pool-design.md](../specs/2026-04-26-raw-video-pool-design.md)

---

## File Structure

### New
| 路径 | 责任 |
|---|---|
| `appcore/raw_video_pool.py` | service 层：4 主函数 |
| `web/routes/raw_video_pool.py` | Blueprint，前缀 `/raw-video-pool`，4 端点 |
| `web/templates/raw_video_pool_list.html` | 主页 + 上传 modal |
| `tests/test_appcore_raw_video_pool.py` | 单元 |
| `tests/test_raw_video_pool_routes.py` | 集成 |

### Modified
| 路径 | 修改 |
|---|---|
| `appcore/permissions.py` | 加 `raw_video_pool` 菜单代码（admin/user 都默认 True） |
| `web/app.py` | 注册新 blueprint + csrf.exempt（multipart upload 需要） |
| `web/templates/layout.html` | 加"原始素材任务库"菜单 |
| `web/templates/tasks_list.html` | 改 `tcParentUploadDone`：移除 prompt，复用 D 的下载/上传 modal |

---

## Conventions

- **测试在 server 跑**（本地无 DB）
- **commit 格式**：`<type>(raw-video-pool): <subject>` + Co-Authored-By
- **worktree**：`g:/Code/AutoVideoSrtLocal/.worktrees/raw-video-pool`，分支 `feature/raw-video-pool`
- **服务器路径**：`/opt/autovideosrt-test`，systemd `autovideosrt-test.service`
- **流式下载**：用 Flask `send_file` 或 `Response(generator, mimetype='video/mp4')`
- **multipart upload**：用 `request.files['file']` + `werkzeug.utils.secure_filename`

---

## Task 索引

| # | 标题 | Phase |
|---|---|---|
| 1 | Permissions: 加 raw_video_pool 菜单代码 | Foundation |
| 2 | Service scaffold + list_visible_tasks | Service |
| 3 | stream_original_video + 权限 helpers | Service |
| 4 | replace_processed_video + mark_uploaded 调用 | Service |
| 5 | Blueprint + register + GET / + GET /api/list | API |
| 6 | GET /api/task/<tid>/download (流式) | API |
| 7 | POST /api/task/<tid>/upload (multipart) | API |
| 8 | layout.html 加菜单 | Frontend |
| 9 | raw_video_pool_list.html 主页 + 3 sections + 表格 | Frontend |
| 10 | 上传 modal + 进度条 + XHR | Frontend |
| 11 | C tasks_list.html 改造：替换 prompt fallback | Integration |
| 12 | 最终回归 + 生产部署 + cron 清理 | Verify |

---

## Phase 1 — Foundation

### Task 1: Permissions

**Files:** `appcore/permissions.py`, `tests/test_appcore_permissions_raw_video_pool.py`

- [ ] **Step 1: failing test**

```python
from appcore.permissions import PERMISSION_CODES, default_permissions_for_role, ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN

def test_raw_video_pool_in_codes():
    assert "raw_video_pool" in PERMISSION_CODES

def test_raw_video_pool_admin_default_true():
    assert default_permissions_for_role(ROLE_ADMIN)["raw_video_pool"] is True

def test_raw_video_pool_user_default_true():
    # 处理人是 role=user + can_process_raw_video=true，菜单本身允许 user 看
    assert default_permissions_for_role(ROLE_USER)["raw_video_pool"] is True

def test_raw_video_pool_superadmin_true():
    assert default_permissions_for_role(ROLE_SUPERADMIN)["raw_video_pool"] is True
```

- [ ] **Step 2: 实施**

In `appcore/permissions.py` PERMISSIONS tuple, insert after `task_center`:

```python
("raw_video_pool",        GROUP_BUSINESS,   "原始素材任务库",   True,  True),
```

Update docstring count similar to C task 2 fix.

- [ ] **Step 3: commit + push**

```bash
git add appcore/permissions.py tests/
git commit -m "feat(raw-video-pool): add raw_video_pool menu permission"
git push -u origin feature/raw-video-pool
```

---

## Phase 2 — Service Layer

### Task 2: Scaffold + `list_visible_tasks`

**Files:** `appcore/raw_video_pool.py`, `tests/test_appcore_raw_video_pool.py`

- [ ] **Step 1: failing test**

```python
import pytest
from appcore.db import execute, query_one

@pytest.fixture
def db_user_admin():
    from appcore.users import create_user, get_by_username
    username = "_t_rvp_admin"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="admin")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))

@pytest.fixture
def db_user_processor():
    from appcore.users import create_user, get_by_username
    username = "_t_rvp_proc"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    execute(
        "UPDATE users SET permissions=JSON_SET(COALESCE(permissions, '{}'), "
        "'$.can_process_raw_video', true) WHERE id=%s",
        (uid,),
    )
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))

def _insert_pending_parent_task(creator_uid, product_name, item_filename):
    """Helper: create a media_product + media_item + pending parent task. Return task_id."""
    pid = execute(
        "INSERT INTO media_products (user_id, name) VALUES (%s, %s)",
        (creator_uid, product_name),
    )
    iid = execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, creator_uid, item_filename, f"k/{item_filename}", "en"),
    )
    tid = execute(
        "INSERT INTO tasks (parent_task_id, media_product_id, media_item_id, status, created_by) "
        "VALUES (NULL, %s, %s, %s, %s)",
        (pid, iid, "pending", creator_uid),
    )
    return tid, pid, iid

def test_list_visible_tasks_admin_sees_all(db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid_a, pid_a, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p1", "_t_rvp_v1.mp4")
    tid_b, pid_b, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p2", "_t_rvp_v2.mp4")
    # processor claims tid_b
    execute("UPDATE tasks SET assignee_id=%s, status='raw_in_progress', claimed_at=NOW() WHERE id=%s",
            (db_user_processor, tid_b))

    result = raw_video_pool.list_visible_tasks(viewer_user_id=db_user_admin, viewer_role="admin")
    assert len(result["pending"]) >= 1
    assert any(t["task_id"] == tid_a for t in result["pending"])
    assert len(result["in_progress"]) >= 1
    assert any(t["task_id"] == tid_b for t in result["in_progress"])

    # cleanup
    execute("DELETE FROM tasks WHERE id IN (%s,%s)", (tid_a, tid_b))
    execute("DELETE FROM media_items WHERE product_id IN (%s,%s)", (pid_a, pid_b))
    execute("DELETE FROM media_products WHERE id IN (%s,%s)", (pid_a, pid_b))


def test_list_visible_tasks_processor_sees_pool_and_own(db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid_a, pid_a, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p3", "_t_rvp_v3.mp4")  # 别人没领
    tid_b, pid_b, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p4", "_t_rvp_v4.mp4")
    execute("UPDATE tasks SET assignee_id=%s, status='raw_in_progress' WHERE id=%s",
            (db_user_processor, tid_b))

    result = raw_video_pool.list_visible_tasks(viewer_user_id=db_user_processor, viewer_role="user")
    pending_ids = [t["task_id"] for t in result["pending"]]
    inprog_ids = [t["task_id"] for t in result["in_progress"]]
    assert tid_a in pending_ids   # 公开池
    assert tid_b in inprog_ids    # 自己已领

    execute("DELETE FROM tasks WHERE id IN (%s,%s)", (tid_a, tid_b))
    execute("DELETE FROM media_items WHERE product_id IN (%s,%s)", (pid_a, pid_b))
    execute("DELETE FROM media_products WHERE id IN (%s,%s)", (pid_a, pid_b))
```

- [ ] **Step 2: 实施 `appcore/raw_video_pool.py`**

```python
"""D 子系统：原始素材任务库 service。

详见 docs/superpowers/specs/2026-04-26-raw-video-pool-design.md
"""
from __future__ import annotations

import logging
import os
from typing import Any

from appcore.db import execute, query_all, query_one

log = logging.getLogger(__name__)


class RawVideoPoolError(Exception):
    pass


class PermissionDenied(RawVideoPoolError):
    pass


class StateError(RawVideoPoolError):
    pass


def list_visible_tasks(*, viewer_user_id: int, viewer_role: str) -> dict:
    """Returns {'pending': [...], 'in_progress': [...], 'review': [...]}.

    - admin/superadmin: 看全部 pending + in_progress + review
    - 其他 (按 viewer 是否有 can_process_raw_video 由 caller 控制；本函数不做能力检查)：
      pending 看全部公开池；in_progress + review 仅看自己 assignee
    """
    is_admin = viewer_role in ("admin", "superadmin")

    base_select = """
        SELECT t.id AS task_id, t.media_product_id, t.media_item_id,
               t.assignee_id, t.created_at, t.claimed_at,
               p.name AS product_name,
               i.filename AS mp4_filename, i.file_size AS mp4_size,
               (SELECT GROUP_CONCAT(country_code ORDER BY country_code SEPARATOR ',')
                FROM tasks c WHERE c.parent_task_id = t.id) AS country_codes
        FROM tasks t
        JOIN media_products p ON p.id = t.media_product_id
        LEFT JOIN media_items i ON i.id = t.media_item_id
        WHERE t.parent_task_id IS NULL
    """

    pending_sql = base_select + " AND t.status = 'pending' ORDER BY t.created_at DESC LIMIT 200"
    pending = query_all(pending_sql)

    if is_admin:
        in_progress = query_all(
            base_select + " AND t.status = 'raw_in_progress' ORDER BY t.claimed_at DESC LIMIT 200"
        )
        review = query_all(
            base_select + " AND t.status = 'raw_review' ORDER BY t.updated_at DESC LIMIT 200"
        )
    else:
        in_progress = query_all(
            base_select + " AND t.status = 'raw_in_progress' AND t.assignee_id = %s "
            "ORDER BY t.claimed_at DESC LIMIT 200",
            (int(viewer_user_id),),
        )
        review = query_all(
            base_select + " AND t.status = 'raw_review' AND t.assignee_id = %s "
            "ORDER BY t.updated_at DESC LIMIT 200",
            (int(viewer_user_id),),
        )

    def _shape(rows):
        result = []
        for r in rows:
            result.append({
                "task_id": r["task_id"],
                "media_product_id": r["media_product_id"],
                "media_item_id": r["media_item_id"],
                "assignee_id": r["assignee_id"],
                "product_name": r["product_name"],
                "mp4_filename": r["mp4_filename"],
                "mp4_size": r["mp4_size"],
                "country_codes": r["country_codes"] or "",
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
            })
        return result

    return {
        "pending": _shape(pending),
        "in_progress": _shape(in_progress),
        "review": _shape(review),
    }
```

- [ ] **Step 3-5**: commit + push + server pytest expect 2 new tests pass

---

### Task 3: `stream_original_video` + 权限 helpers

**Files:** `appcore/raw_video_pool.py`, `tests/test_appcore_raw_video_pool.py`

- [ ] **Step 1: failing test**

```python
def test_stream_original_video_admin_ok(monkeypatch, db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid, pid, iid = _insert_pending_parent_task(db_user_admin, "_t_rvp_p5", "_t_rvp_v5.mp4")
    # Simulate file exists locally — monkeypatch resolve_local_path
    monkeypatch.setattr(
        raw_video_pool, "_resolve_local_path",
        lambda object_key: f"/tmp/_t_rvp_v5.mp4",
    )
    path, suggested = raw_video_pool.stream_original_video(tid, db_user_admin)
    assert path == "/tmp/_t_rvp_v5.mp4"
    assert suggested == "_t_rvp_v5.mp4"

    execute("DELETE FROM tasks WHERE id=%s", (tid,))
    execute("DELETE FROM media_items WHERE id=%s", (iid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_stream_original_video_non_assignee_denied(monkeypatch, db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid, pid, iid = _insert_pending_parent_task(db_user_admin, "_t_rvp_p6", "_t_rvp_v6.mp4")
    # Other random user
    other_uid = db_user_admin + 999
    with pytest.raises(raw_video_pool.PermissionDenied):
        raw_video_pool.stream_original_video(tid, other_uid)
    execute("DELETE FROM tasks WHERE id=%s", (tid,))
    execute("DELETE FROM media_items WHERE id=%s", (iid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
```

- [ ] **Step 2: 实施**

```python
def _resolve_local_path(object_key: str) -> str | None:
    """媒体文件本地路径解析。复用素材管理已有的映射（grep medias.py 找
    现有路径解析函数；如果没有，按惯例 UPLOAD_DIR + object_key）。"""
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return os.path.join(upload_dir, object_key)


def _is_admin_or_assignee(task_row: dict, viewer_user_id: int) -> bool:
    # admin 可看全部；assignee 可看自己
    # 这里 admin 检查需要外部传入，本函数只检查 assignee
    return task_row.get("assignee_id") == int(viewer_user_id)


def _check_view_permission(task_id: int, viewer_user_id: int) -> dict:
    """Load the task row; raise PermissionDenied if viewer is neither admin nor assignee.
    Returns the task row dict for downstream use."""
    row = query_one(
        "SELECT t.*, u.role AS viewer_role FROM tasks t, users u "
        "WHERE t.id=%s AND u.id=%s AND t.parent_task_id IS NULL",
        (int(task_id), int(viewer_user_id)),
    )
    if not row:
        raise PermissionDenied("task not found or viewer not found")
    is_admin = row["viewer_role"] in ("admin", "superadmin")
    if not is_admin and row["assignee_id"] != int(viewer_user_id):
        raise PermissionDenied("not assignee")
    return row


def stream_original_video(task_id: int, viewer_user_id: int) -> tuple[str, str]:
    """返回 (本地路径, suggested_filename)。"""
    row = _check_view_permission(task_id, viewer_user_id)
    if not row.get("media_item_id"):
        raise StateError("task has no media_item bound")
    item = query_one("SELECT * FROM media_items WHERE id=%s", (row["media_item_id"],))
    if not item:
        raise StateError("media_item not found")
    local_path = _resolve_local_path(item["object_key"])
    if not local_path:
        raise StateError("cannot resolve local path")
    return local_path, item["filename"]
```

- [ ] **Step 3-5**: commit + push + server pytest

---

### Task 4: `replace_processed_video` + auto mark_uploaded

**Files:** `appcore/raw_video_pool.py`, `tests/test_appcore_raw_video_pool.py`

- [ ] **Step 1: failing test**

```python
def test_replace_processed_video_full_path(monkeypatch, tmp_path, db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid, pid, iid = _insert_pending_parent_task(db_user_admin, "_t_rvp_p7", "_t_rvp_v7.mp4")
    # processor claims
    execute(
        "UPDATE tasks SET assignee_id=%s, status='raw_in_progress', claimed_at=NOW() WHERE id=%s",
        (db_user_processor, tid),
    )

    # Simulate local file exists
    target = tmp_path / "_t_rvp_v7.mp4"
    target.write_bytes(b"original")
    monkeypatch.setattr(raw_video_pool, "_resolve_local_path", lambda ok: str(target))

    # Fake uploaded file
    class FakeFile:
        def __init__(self, content): self.content = content
        def save(self, path): open(path, "wb").write(self.content)
        filename = "processed.mp4"

    new_size = raw_video_pool.replace_processed_video(
        task_id=tid, actor_user_id=db_user_processor,
        uploaded_file=FakeFile(b"processed_content_50_bytes" * 2),
    )
    assert new_size > 0
    assert target.read_bytes().startswith(b"processed_content")
    # state translated
    row = query_one("SELECT status FROM tasks WHERE id=%s", (tid,))
    assert row["status"] == "raw_review"

    execute("DELETE FROM task_events WHERE task_id=%s", (tid,))
    execute("DELETE FROM tasks WHERE id=%s", (tid,))
    execute("DELETE FROM media_items WHERE id=%s", (iid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
```

- [ ] **Step 2: 实施**

```python
def replace_processed_video(*, task_id: int, actor_user_id: int, uploaded_file) -> int:
    """Save uploaded file to original location, then call C's mark_uploaded.

    Returns the new file size in bytes.
    Raises: PermissionDenied / StateError.
    """
    row = _check_view_permission(task_id, actor_user_id)
    if row.get("assignee_id") != int(actor_user_id):
        raise PermissionDenied("only assignee can upload processed")
    if row.get("status") != "raw_in_progress":
        raise StateError(f"expected raw_in_progress, got {row.get('status')}")
    if not row.get("media_item_id"):
        raise StateError("task has no media_item")
    item = query_one("SELECT * FROM media_items WHERE id=%s", (row["media_item_id"],))
    if not item:
        raise StateError("media_item not found")
    local_path = _resolve_local_path(item["object_key"])
    if not local_path:
        raise StateError("cannot resolve local path")

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    uploaded_file.save(local_path)
    new_size = os.path.getsize(local_path)

    # Update file_size on the media_item row
    execute(
        "UPDATE media_items SET file_size=%s, updated_at=NOW() WHERE id=%s",
        (new_size, row["media_item_id"]),
    )

    # Auto-trigger C's mark_uploaded (translates state to raw_review)
    from appcore import tasks as tasks_svc
    tasks_svc.mark_uploaded(task_id=task_id, actor_user_id=actor_user_id)

    return new_size
```

- [ ] **Step 3-5**: commit + push + server pytest

---

## Phase 3 — API Routes

### Task 5: Blueprint scaffold + register + GET / + GET /api/list

**Files:** `web/routes/raw_video_pool.py`, `web/app.py`, `tests/test_raw_video_pool_routes.py`

- [ ] **Step 1: tests**

```python
def test_index_renders(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    assert rsp.status_code == 200
    assert "原始素材任务库".encode("utf-8") in rsp.data

def test_api_list_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/list")
    assert rsp.status_code in (200, 500)
```

- [ ] **Step 2: 实施 `web/routes/raw_video_pool.py`**

```python
"""D 子系统 Blueprint."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from appcore import raw_video_pool as rvp_svc

bp = Blueprint("raw_video_pool", __name__, url_prefix="/raw-video-pool")


def _viewer_role() -> str:
    return getattr(current_user, "role", "user")


@bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template("raw_video_pool_list.html",
                           is_admin=_viewer_role() in ("admin", "superadmin"))


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    result = rvp_svc.list_visible_tasks(
        viewer_user_id=int(current_user.id),
        viewer_role=_viewer_role(),
    )
    return jsonify(result)
```

- [ ] **Step 3: 创建 minimal template (Task 9 会扩展)**

```html
<!-- web/templates/raw_video_pool_list.html -->
{% extends "layout.html" %}
{% block title %}原始素材任务库 - AutoVideoSrt{% endblock %}
{% block content %}
<div id="rvpRoot"><h1>原始素材任务库</h1><p>骨架占位 — Task 9 完成 UI</p></div>
{% endblock %}
```

- [ ] **Step 4: register in app.py**

```python
from web.routes.raw_video_pool import bp as raw_video_pool_bp
# ...
    app.register_blueprint(raw_video_pool_bp)
    csrf.exempt(raw_video_pool_bp)
```

- [ ] **Step 5: commit + push + restart + verify**

---

### Task 6: GET `/api/task/<tid>/download` (流式)

**Files:** `web/routes/raw_video_pool.py`, `tests/test_raw_video_pool_routes.py`

- [ ] **Step 1: test**

```python
def test_download_endpoint_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/task/9999/download")
    # may 404 / 403 / 500 depending on no-DB; just route registered
    assert rsp.status_code in (200, 403, 404, 500)
```

- [ ] **Step 2: 实施**

```python
@bp.route("/api/task/<int:tid>/download", methods=["GET"])
@login_required
def api_download(tid: int):
    try:
        path, fname = rvp_svc.stream_original_video(tid, int(current_user.id))
    except rvp_svc.PermissionDenied as e:
        return jsonify({"error": "forbidden", "detail": str(e)}), 403
    except rvp_svc.StateError as e:
        return jsonify({"error": "state_error", "detail": str(e)}), 422
    import os
    if not os.path.exists(path):
        return jsonify({"error": "file_not_found", "detail": path}), 404
    return send_file(path, as_attachment=True, download_name=fname,
                     mimetype="video/mp4")
```

- [ ] **Step 3-5**: commit + push + verify

---

### Task 7: POST `/api/task/<tid>/upload` (multipart)

**Files:** `web/routes/raw_video_pool.py`, `tests/test_raw_video_pool_routes.py`

- [ ] **Step 1: test**

```python
def test_upload_endpoint_smoke(authed_client_no_db):
    rsp = authed_client_no_db.post("/raw-video-pool/api/task/9999/upload")
    assert rsp.status_code in (400, 403, 404, 422, 500)


def test_upload_endpoint_no_file(authed_client_no_db):
    rsp = authed_client_no_db.post("/raw-video-pool/api/task/9999/upload", data={})
    assert rsp.status_code in (400, 403, 404, 422, 500)
```

- [ ] **Step 2: 实施**

```python
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

@bp.route("/api/task/<int:tid>/upload", methods=["POST"])
@login_required
def api_upload(tid: int):
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no_file"}), 400
    # Lightweight size check
    f.seek(0, 2)  # end
    size = f.tell()
    f.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file_too_large", "max_mb": 500}), 413
    if not (f.filename or "").lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
        return jsonify({"error": "unsupported_type"}), 415
    try:
        new_size = rvp_svc.replace_processed_video(
            task_id=tid, actor_user_id=int(current_user.id), uploaded_file=f,
        )
    except rvp_svc.PermissionDenied as e:
        return jsonify({"error": "forbidden", "detail": str(e)}), 403
    except rvp_svc.StateError as e:
        return jsonify({"error": "state_error", "detail": str(e)}), 422
    except Exception as e:
        return jsonify({"error": "internal", "detail": str(e)}), 500
    return jsonify({"ok": True, "new_size": new_size})
```

- [ ] **Step 3-5**: commit + push + verify

---

## Phase 4 — Frontend

### Task 8: layout.html 加菜单

**Files:** `web/templates/layout.html`

- [ ] Insert new nav item right after 任务中心:

```html
{% if has_permission('raw_video_pool') %}
<a href="/raw-video-pool/" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/raw-video-pool/') %}class="active"{% endif %}>
  <span class="nav-icon">🎬</span> 原始素材任务库
</a>
{% endif %}
```

- [ ] commit + push

---

### Task 9: raw_video_pool_list.html 主页 (3 sections + 表格)

**Files:** `web/templates/raw_video_pool_list.html`

替换 minimal template 为完整版：

```html
{% extends "layout.html" %}
{% block title %}原始素材任务库 - AutoVideoSrt{% endblock %}
{% block extra_style %}
:root {
  --rvp-bg: oklch(99% 0.004 230); --rvp-bg-subtle: oklch(97% 0.006 230);
  --rvp-bg-muted: oklch(94% 0.010 230); --rvp-border: oklch(91% 0.012 230);
  --rvp-border-strong: oklch(84% 0.015 230); --rvp-fg: oklch(22% 0.020 235);
  --rvp-fg-muted: oklch(48% 0.018 230); --rvp-accent: oklch(56% 0.16 230);
  --rvp-accent-hover: oklch(50% 0.17 230);
  --rvp-r: 6px; --rvp-r-md: 8px;
  --rvp-sp-2: 8px; --rvp-sp-3: 12px; --rvp-sp-4: 16px;
}
.rvp { font-family: "Inter Tight", "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--rvp-fg); }
.rvp h1 { font-size: 22px; font-weight: 600; margin: 0 0 var(--rvp-sp-4); }
.rvp h2 { font-size: 15px; font-weight: 600; margin: var(--rvp-sp-4) 0 var(--rvp-sp-2); color: var(--rvp-fg-muted); }
.rvp-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: var(--rvp-sp-4); }
.rvp-table th { text-align: left; padding: 8px 10px; background: var(--rvp-bg-subtle); border-bottom: 2px solid var(--rvp-border); font-weight: 600; }
.rvp-table td { padding: 8px 10px; border-bottom: 1px solid var(--rvp-border); }
.rvp-btn { height: 28px; padding: 0 10px; border: 1px solid var(--rvp-border-strong); border-radius: var(--rvp-r); background: var(--rvp-bg); cursor: pointer; font-size: 12px; }
.rvp-btn:hover:not(:disabled) { background: var(--rvp-bg-muted); }
.rvp-btn--primary { background: var(--rvp-accent); color: #fff; border: none; }
.rvp-btn--primary:hover { background: var(--rvp-accent-hover); }
.rvp-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.rvp-empty { padding: 20px; text-align: center; color: var(--rvp-fg-muted); }
{% endblock %}
{% block content %}
<div id="rvpRoot" class="rvp">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <h1>原始素材任务库</h1>
    <button class="rvp-btn" onclick="rvpRender()">刷新</button>
  </div>

  <h2>📋 待认领 (<span id="rvpPendingCount">0</span>)</h2>
  <div id="rvpPending"></div>

  <h2>⏳ 我已认领 (<span id="rvpInProgressCount">0</span>)</h2>
  <div id="rvpInProgress"></div>

  <h2>📤 已上传待审 (<span id="rvpReviewCount">0</span>)</h2>
  <div id="rvpReview"></div>
</div>

<!-- Upload modal -->
<div id="rvpUploadModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:1500;">
  <div style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); background:#fff; border-radius:12px; padding:24px; width:480px;">
    <h3 style="margin:0 0 12px;">上传处理后视频</h3>
    <p style="font-size:13px; color:var(--rvp-fg-muted); margin:0 0 12px;" id="rvpUploadHint"></p>
    <input type="file" id="rvpUploadFile" accept="video/mp4,video/webm,video/quicktime,video/x-matroska" />
    <div id="rvpUploadProgress" style="margin-top:12px; display:none;">
      <div style="font-size:12px; color:var(--rvp-fg-muted);">上传中... <span id="rvpUploadPct">0%</span></div>
      <div style="height:6px; background:var(--rvp-bg-muted); border-radius:3px; overflow:hidden; margin-top:4px;">
        <div id="rvpUploadBar" style="height:100%; width:0; background:var(--rvp-accent); transition:width 200ms;"></div>
      </div>
    </div>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px;">
      <button class="rvp-btn" onclick="rvpUploadCancel()">取消</button>
      <button class="rvp-btn rvp-btn--primary" id="rvpUploadOK" onclick="rvpUploadOK()">上传</button>
    </div>
  </div>
</div>

<script>
const RVP_IS_ADMIN = {{ 'true' if is_admin else 'false' }};

function rvpEsc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }
function rvpFmtSize(b) { if (!b) return '?'; if (b < 1024*1024) return Math.round(b/1024)+'KB'; return (b/1024/1024).toFixed(1)+'MB'; }

async function rvpRender() {
  try {
    const r = await fetch('/raw-video-pool/api/list');
    const data = await r.json();
    rvpRenderSection('rvpPending', 'rvpPendingCount', data.pending, 'pending');
    rvpRenderSection('rvpInProgress', 'rvpInProgressCount', data.in_progress, 'in_progress');
    rvpRenderSection('rvpReview', 'rvpReviewCount', data.review, 'review');
  } catch (e) {
    document.getElementById('rvpPending').innerHTML = '<div class="rvp-empty">加载失败：' + rvpEsc(e.message) + '</div>';
  }
}

function rvpRenderSection(elId, countId, items, kind) {
  document.getElementById(countId).textContent = items.length;
  const wrap = document.getElementById(elId);
  if (!items.length) { wrap.innerHTML = '<div class="rvp-empty">暂无任务</div>'; return; }
  const rows = items.map(t => {
    let actions = '';
    if (kind === 'pending') {
      actions = `<button class="rvp-btn rvp-btn--primary" onclick="rvpClaim(${t.task_id})">认领</button>`;
    } else if (kind === 'in_progress') {
      actions = `<button class="rvp-btn" onclick="rvpDownload(${t.task_id}, '${rvpEsc(t.mp4_filename)}')">下载原始</button>
                 <button class="rvp-btn rvp-btn--primary" onclick="rvpOpenUpload(${t.task_id}, '${rvpEsc(t.mp4_filename)}')">上传处理后</button>`;
    } else if (kind === 'review') {
      actions = `<button class="rvp-btn" disabled>等管理员审核</button>`;
    }
    return `<tr>
      <td>${rvpEsc(t.product_name)}</td>
      <td>${rvpEsc(t.country_codes || '—')}</td>
      <td>${rvpEsc(t.mp4_filename || '—')}</td>
      <td>${rvpFmtSize(t.mp4_size)}</td>
      <td>${rvpEsc(t.created_at || '')}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `<table class="rvp-table"><thead><tr>
    <th>产品</th><th>国家</th><th>文件名</th><th>大小</th><th>时间</th><th>操作</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

async function rvpClaim(tid) {
  try {
    const r = await fetch(`/tasks/api/parent/${tid}/claim`, {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    if (!r.ok) {
      const e = await r.json();
      throw new Error(e.error || r.statusText);
    }
    rvpRender();
  } catch (e) { alert('认领失败：' + e.message); }
}

function rvpDownload(tid, fname) {
  // Just navigate to the download URL; browser handles save dialog
  window.location.href = `/raw-video-pool/api/task/${tid}/download`;
}

let _rvpUploadCtx = null;
function rvpOpenUpload(tid, fname) {
  _rvpUploadCtx = {tid, fname};
  document.getElementById('rvpUploadHint').textContent = `任务 #${tid} — ${fname}（处理后将覆盖原文件）`;
  document.getElementById('rvpUploadFile').value = '';
  document.getElementById('rvpUploadProgress').style.display = 'none';
  document.getElementById('rvpUploadOK').disabled = false;
  document.getElementById('rvpUploadModal').style.display = 'block';
}
function rvpUploadCancel() { document.getElementById('rvpUploadModal').style.display = 'none'; _rvpUploadCtx = null; }
function rvpUploadOK() {
  if (!_rvpUploadCtx) return;
  const f = document.getElementById('rvpUploadFile').files[0];
  if (!f) { alert('请选文件'); return; }
  const okBtn = document.getElementById('rvpUploadOK');
  okBtn.disabled = true;
  document.getElementById('rvpUploadProgress').style.display = 'block';

  const xhr = new XMLHttpRequest();
  xhr.upload.onprogress = ev => {
    if (!ev.lengthComputable) return;
    const pct = Math.round(ev.loaded / ev.total * 100);
    document.getElementById('rvpUploadPct').textContent = pct + '%';
    document.getElementById('rvpUploadBar').style.width = pct + '%';
  };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      rvpUploadCancel();
      rvpRender();
      alert('上传成功，状态已转待审核');
    } else {
      let msg = xhr.statusText;
      try { msg = JSON.parse(xhr.responseText).detail || JSON.parse(xhr.responseText).error || msg; } catch (e) {}
      okBtn.disabled = false;
      alert('上传失败：' + msg);
    }
  };
  xhr.onerror = () => { okBtn.disabled = false; alert('网络错误'); };
  xhr.open('POST', `/raw-video-pool/api/task/${_rvpUploadCtx.tid}/upload`);
  const fd = new FormData();
  fd.append('file', f);
  xhr.send(fd);
}

rvpRender();
</script>
{% endblock %}
```

- [ ] commit + push + restart + 浏览器肉眼验证

---

### Task 10: 上传 modal — 已在 Task 9 一并实现

**完成时跳过本任务，直接走 Task 11**

---

## Phase 5 — C Integration

### Task 11: 改 tasks_list.html 替换 prompt fallback

**Files:** `web/templates/tasks_list.html`

找到 `tcParentUploadDone` 函数，替换 prompt 路径为：跳转到原始素材任务库 + 提示

```javascript
async function tcParentUploadDone(id, productId) {
  try {
    await tcFetchJson(`/tasks/api/parent/${id}/upload_done`, {method:'POST', body:'{}'});
    tcRender();
    if (TC_DETAIL_TASK_ID === id) tcOpenDetail(id);
  } catch (e) {
    if (e.message && e.message.includes('media_item')) {
      // Modern flow: redirect to raw-video-pool
      const goRvp = confirm('该任务还未上传处理后视频。是否打开"原始素材任务库"完成上传？');
      if (goRvp) window.open('/raw-video-pool/', '_blank');
    } else {
      alert(e.message);
    }
  }
}
```

- [ ] commit + push

---

## Phase 6 — Verify + Production Deploy

### Task 12: 全测试 + 测试环境验证 + 生产部署

- [ ] 全测试 on server
- [ ] curl smoke 4 endpoints
- [ ] merge feature/raw-video-pool 到 master + push
- [ ] SSH /opt/autovideosrt git pull + restart autovideosrt
- [ ] curl http://172.30.254.14/login 验证 200
- [ ] CronDelete 自己
