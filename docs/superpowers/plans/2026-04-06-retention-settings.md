# 项目保留周期管理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 admin 可以在后台按模块配置项目保留周期，保留周期从项目完成时开始计算。

**Architecture:** 新增 `system_settings` KV 表存储配置，`appcore/settings.py` 提供读写接口。项目创建时 `expires_at = NULL`，完成（done/error）时根据配置计算。admin 通过 `/admin/settings` 页面管理。

**Tech Stack:** Flask, MySQL, Jinja2

---

### Task 1: 数据库迁移 — 建 system_settings 表

**Files:**
- Create: `db/migrations/add_system_settings.sql`

- [ ] **Step 1: 编写迁移 SQL**

```sql
-- db/migrations/add_system_settings.sql
CREATE TABLE IF NOT EXISTS system_settings (
    `key`      VARCHAR(100) PRIMARY KEY,
    `value`    TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO system_settings (`key`, `value`) VALUES ('retention_default_hours', '168');
```

- [ ] **Step 2: 修改 projects 表允许 expires_at 为 NULL**

```sql
-- 追加到同一迁移文件末尾
ALTER TABLE projects MODIFY COLUMN expires_at DATETIME NULL;
```

- [ ] **Step 3: 执行迁移**

Run: `python -c "from appcore.db import execute; execute(open('db/migrations/add_system_settings.sql').read())"`

如果 `execute` 不支持多语句，手动在 MySQL 终端执行。

- [ ] **Step 4: 验证**

Run: `python -c "from appcore.db import query; print(query('SELECT * FROM system_settings'))"`
Expected: `[{'key': 'retention_default_hours', 'value': '168', 'updated_at': ...}]`

- [ ] **Step 5: Commit**

```bash
git add db/migrations/add_system_settings.sql
git commit -m "feat: 新增 system_settings 表和迁移脚本"
```

---

### Task 2: appcore/settings.py — 配置读写模块

**Files:**
- Create: `appcore/settings.py`
- Create: `tests/test_settings.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_settings.py
from __future__ import annotations


def test_get_retention_hours_default(monkeypatch):
    """无覆盖值时返回全局默认"""
    import appcore.settings as settings

    store = {"retention_default_hours": "168"}

    def fake_query_one(sql, args):
        key = args[0]
        if key in store:
            return {"value": store[key]}
        return None

    monkeypatch.setattr(settings, "_query_one", fake_query_one)
    assert settings.get_retention_hours("translation") == 168


def test_get_retention_hours_override(monkeypatch):
    """有模块覆盖值时优先使用"""
    import appcore.settings as settings

    store = {
        "retention_default_hours": "168",
        "retention_copywriting_hours": "48",
    }

    def fake_query_one(sql, args):
        key = args[0]
        if key in store:
            return {"value": store[key]}
        return None

    monkeypatch.setattr(settings, "_query_one", fake_query_one)
    assert settings.get_retention_hours("copywriting") == 48


def test_get_retention_hours_fallback_hardcode(monkeypatch):
    """数据库完全没有配置时，硬编码 168"""
    import appcore.settings as settings

    monkeypatch.setattr(settings, "_query_one", lambda sql, args: None)
    assert settings.get_retention_hours("translation") == 168


def test_get_setting(monkeypatch):
    import appcore.settings as settings

    monkeypatch.setattr(
        settings, "_query_one",
        lambda sql, args: {"value": "hello"} if args[0] == "some_key" else None,
    )
    assert settings.get_setting("some_key") == "hello"
    assert settings.get_setting("missing") is None


def test_set_setting(monkeypatch):
    import appcore.settings as settings

    calls = []
    monkeypatch.setattr(settings, "_execute", lambda sql, args: calls.append(args))
    settings.set_setting("foo", "bar")
    assert len(calls) == 1
    assert calls[0] == ("foo", "bar")


def test_get_all_retention_settings(monkeypatch):
    import appcore.settings as settings

    rows = [
        {"key": "retention_default_hours", "value": "168"},
        {"key": "retention_copywriting_hours", "value": "48"},
    ]
    monkeypatch.setattr(
        settings, "_query",
        lambda sql, args=(): [r for r in rows if r["key"].startswith(args[0])] if args else rows,
    )
    result = settings.get_all_retention_settings()
    assert result["default"] == 168
    assert result["copywriting"] == 48
    assert result.get("translation") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'appcore.settings'`

- [ ] **Step 3: 实现 appcore/settings.py**

```python
# appcore/settings.py
"""System settings stored in the system_settings table."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 支持的模块类型 → 显示名称
PROJECT_TYPE_LABELS: dict[str, str] = {
    "translation": "视频翻译（英文）",
    "de_translate": "视频翻译（德语）",
    "fr_translate": "视频翻译（法语）",
    "copywriting": "文案创作",
    "video_creation": "视频生成",
    "text_translate": "文案翻译",
}

_HARDCODE_DEFAULT_HOURS = 168


def _query_one(sql: str, args: tuple = ()):
    from appcore.db import query_one
    return query_one(sql, args)


def _query(sql: str, args: tuple = ()):
    from appcore.db import query
    return query(sql, args)


def _execute(sql: str, args: tuple = ()):
    from appcore.db import execute
    return execute(sql, args)


def get_setting(key: str) -> str | None:
    row = _query_one("SELECT `value` FROM system_settings WHERE `key` = %s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    _execute(
        "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
        (key, value),
    )


def get_retention_hours(project_type: str) -> int:
    override = get_setting(f"retention_{project_type}_hours")
    if override:
        try:
            return int(override)
        except (ValueError, TypeError):
            pass
    default = get_setting("retention_default_hours")
    if default:
        try:
            return int(default)
        except (ValueError, TypeError):
            pass
    return _HARDCODE_DEFAULT_HOURS


def get_all_retention_settings() -> dict:
    """返回 {'default': 168, 'copywriting': 48, ...}，无覆盖的模块不出现。"""
    rows = _query(
        "SELECT `key`, `value` FROM system_settings WHERE `key` LIKE %s",
        ("retention_%",),
    )
    result: dict = {}
    for row in rows:
        key = row["key"]
        try:
            val = int(row["value"])
        except (ValueError, TypeError):
            continue
        if key == "retention_default_hours":
            result["default"] = val
        else:
            # retention_{type}_hours → type
            suffix = key.removeprefix("retention_").removesuffix("_hours")
            if suffix:
                result[suffix] = val
    if "default" not in result:
        result["default"] = _HARDCODE_DEFAULT_HOURS
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add appcore/settings.py tests/test_settings.py
git commit -m "feat: 新增 appcore/settings.py 系统配置读写模块"
```

---

### Task 3: 修改 task_state.py — 创建时 expires_at 为 NULL

**Files:**
- Modify: `appcore/task_state.py:38-58`

- [ ] **Step 1: 修改 _db_upsert 函数**

在 `appcore/task_state.py` 中，将 `_db_upsert()` 的 `expires_at` 从硬编码 48 小时改为 NULL：

```python
# 旧代码（第 42-55 行）:
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        expires_at = datetime.now() + timedelta(hours=48)
        db_execute(
            """INSERT INTO projects (id, user_id, original_filename, status, task_dir, state_json, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 status = VALUES(status),
                 state_json = VALUES(state_json),
                 task_dir = VALUES(task_dir)""",
            (task_id, user_id, original_filename,
             task.get("status", "uploaded"),
             task.get("task_dir", ""),
             state_json,
             expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )

# 新代码:
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        db_execute(
            """INSERT INTO projects (id, user_id, original_filename, status, task_dir, state_json, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, NULL)
               ON DUPLICATE KEY UPDATE
                 status = VALUES(status),
                 state_json = VALUES(state_json),
                 task_dir = VALUES(task_dir)""",
            (task_id, user_id, original_filename,
             task.get("status", "uploaded"),
             task.get("task_dir", ""),
             state_json),
        )
```

- [ ] **Step 2: 新增 set_expires_at 函数**

在 `_sync_task_to_db()` 函数后面（约第 78 行）新增：

```python
def set_expires_at(task_id: str, project_type: str) -> None:
    """项目完成时，根据配置计算并写入 expires_at。"""
    try:
        from appcore.db import execute as db_execute
        from appcore.settings import get_retention_hours

        hours = get_retention_hours(project_type)
        expires_at = datetime.now() + timedelta(hours=hours)
        db_execute(
            "UPDATE projects SET expires_at = %s WHERE id = %s",
            (expires_at.strftime("%Y-%m-%d %H:%M:%S"), task_id),
        )
    except Exception:
        log.warning("[task_state] set_expires_at 失败 task_id=%s", task_id, exc_info=True)
```

- [ ] **Step 3: 删除未使用的 timedelta import（如果不再需要）**

`timedelta` 仍然在 `set_expires_at` 中使用，保留 import 不变。但 `_db_upsert` 中不再需要 `expires_at` 变量。

- [ ] **Step 4: 运行现有测试确认不破坏**

Run: `python -m pytest tests/test_appcore_task_state.py tests/test_appcore_task_state_db.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/task_state.py
git commit -m "refactor: 项目创建时 expires_at 设为 NULL，新增 set_expires_at()"
```

---

### Task 4: 修改 runtime — 完成时设置过期时间

**Files:**
- Modify: `appcore/runtime.py:175,530`
- Modify: `appcore/copywriting_runtime.py:60,81,85`
- Modify: `appcore/runtime_de.py`（如果有 done/error 状态设置）

- [ ] **Step 1: 修改 appcore/runtime.py**

在 `runtime.py` 中找到 status 变为 done 和 error 的位置，加入 `set_expires_at` 调用：

**第 175 行（error 处理）：**
```python
# 旧:
            task_state.update(task_id, status="error", error=str(exc))
# 新:
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, "translation")
```

**第 530 行（done 处理）：**
```python
# 旧:
        task_state.update(task_id, variants=variants, exports=exports, status="done")
# 新:
        task_state.update(task_id, variants=variants, exports=exports, status="done")
        task_state.set_expires_at(task_id, "translation")
```

- [ ] **Step 2: 修改 appcore/copywriting_runtime.py**

**第 60 行（error）：**
```python
# 旧:
            task_state.update(task_id, status="error")
# 新:
            task_state.update(task_id, status="error")
            task_state.set_expires_at(task_id, "copywriting")
```

**第 81 行（done）：**
```python
# 旧:
            task_state.update(task_id, status="done")
# 新:
            task_state.update(task_id, status="done")
            task_state.set_expires_at(task_id, "copywriting")
```

**第 85 行（error）：**
```python
# 旧:
            task_state.update(task_id, status="error")
# 新:
            task_state.update(task_id, status="error")
            task_state.set_expires_at(task_id, "copywriting")
```

- [ ] **Step 3: 检查 runtime_de.py 是否有 done/error 状态设置**

如果 `runtime_de.py` 继承了 `PipelineRunner` 且未覆盖 done/error 逻辑，则父类的改动已足够。如果有覆盖，需同样添加 `task_state.set_expires_at(task_id, "de_translate")`。

根据之前探索，`runtime_de.py` 继承 `PipelineRunner`，grep 未发现 done/error 状态设置，因此无需修改。

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/test_appcore_runtime.py tests/test_cleanup.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py appcore/copywriting_runtime.py
git commit -m "feat: 项目完成/失败时根据配置设置 expires_at"
```

---

### Task 5: 修改 cleanup.py — 增加僵尸项目兜底清理

**Files:**
- Modify: `appcore/cleanup.py:16-31`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: 编写测试**

在 `tests/test_cleanup.py` 末尾追加：

```python
def test_run_cleanup_handles_zombie_projects(monkeypatch):
    """expires_at IS NULL 且非运行中且超过 30 天的项目应被清理"""
    expired_rows = []
    zombie_rows = [
        {
            "id": "zombie-task",
            "task_dir": "",
            "user_id": 1,
            "state_json": "{}",
        }
    ]
    updated = []

    call_count = {"n": 0}

    def fake_query(sql, args=()):
        call_count["n"] += 1
        if "expires_at < NOW()" in sql:
            return expired_rows
        if "expires_at IS NULL" in sql:
            return zombie_rows
        if "SELECT id FROM projects WHERE id IN" in sql:
            return []
        return []

    def fake_execute(sql, args=()):
        updated.append(args)

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup, "execute", fake_execute)
    monkeypatch.setattr(cleanup.tos_clients, "is_tos_configured", lambda: False)

    cleanup.run_cleanup()

    assert any("zombie-task" in str(a) for a in updated)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cleanup.py::test_run_cleanup_handles_zombie_projects -v`
Expected: FAIL

- [ ] **Step 3: 修改 cleanup.py 的 run_cleanup 函数**

在 `run_cleanup()` 函数中，现有过期清理逻辑之后、`delete_stale_upload_objects` 之前，追加僵尸项目清理：

```python
def run_cleanup() -> None:
    # ── 原有：清理已过期的项目 ──
    rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at < NOW() AND deleted_at IS NULL"
    )
    for row in rows:
        task_id = row["id"]
        try:
            delete_task_storage(row)
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up expired project %s", task_id)
        except Exception as e:
            log.error("Cleanup failed for %s: %s", task_id, e)

    # ── 新增：僵尸项目兜底清理 ──
    zombie_rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at IS NULL "
        "AND status NOT IN ('uploaded', 'running') "
        "AND created_at < NOW() - INTERVAL 30 DAY "
        "AND deleted_at IS NULL"
    )
    for row in zombie_rows:
        task_id = row["id"]
        try:
            delete_task_storage(row)
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up zombie project %s", task_id)
        except Exception as e:
            log.error("Zombie cleanup failed for %s: %s", task_id, e)

    try:
        delete_stale_upload_objects()
    except Exception as e:
        log.error("Orphan upload cleanup failed: %s", e)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cleanup.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/cleanup.py tests/test_cleanup.py
git commit -m "feat: cleanup 增加僵尸项目（expires_at 为空超 30 天）兜底清理"
```

---

### Task 6: Admin 设置页面 — 路由和模板

**Files:**
- Modify: `web/routes/admin.py`
- Create: `web/templates/admin_settings.html`
- Modify: `web/templates/layout.html:319-321`

- [ ] **Step 1: 在 admin.py 中新增路由**

在 `web/routes/admin.py` 文件末尾追加：

```python
from appcore.settings import (
    PROJECT_TYPE_LABELS,
    get_all_retention_settings,
    set_setting,
)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    if request.method == "POST":
        # 保存全局默认值
        default_days = request.form.get("retention_default_days", "").strip()
        if default_days:
            try:
                hours = int(float(default_days) * 24)
                if hours > 0:
                    set_setting("retention_default_hours", str(hours))
            except (ValueError, TypeError):
                flash("全局默认值必须是正数")
                return redirect(url_for("admin.settings"))

        # 保存各模块覆盖值
        for ptype in PROJECT_TYPE_LABELS:
            field = f"retention_{ptype}_days"
            val = request.form.get(field, "").strip()
            key = f"retention_{ptype}_hours"
            if val:
                try:
                    hours = int(float(val) * 24)
                    if hours > 0:
                        set_setting(key, str(hours))
                    else:
                        set_setting(key, "")
                except (ValueError, TypeError):
                    pass
            else:
                # 留空 = 删除覆盖，回退到全局默认
                from appcore.db import execute as db_execute
                db_execute("DELETE FROM system_settings WHERE `key` = %s", (key,))

        flash("保留周期设置已保存")
        return redirect(url_for("admin.settings"))

    current = get_all_retention_settings()
    return render_template(
        "admin_settings.html",
        project_types=PROJECT_TYPE_LABELS,
        current=current,
    )
```

- [ ] **Step 2: 创建 admin_settings.html 模板**

```html
<!-- web/templates/admin_settings.html -->
{% extends "layout.html" %}
{% block title %}系统设置 — AutoVideoSrt{% endblock %}
{% block page_title %}系统设置{% endblock %}
{% block extra_style %}
.settings-card { background: var(--bg-card, #fff); border: 1.5px solid var(--border-main, #e5e7eb); border-radius: 14px; padding: 24px; max-width: 600px; }
.settings-card h2 { font-size: 16px; font-weight: 700; color: var(--text-main, #111827); margin-bottom: 20px; }
.field-group { margin-bottom: 16px; }
.field-group label { display: block; color: var(--text-muted, #6b7280); font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.field-row { display: flex; align-items: center; gap: 8px; }
.field-row input { width: 100px; background: var(--bg-input, #f9fafb); border: 1.5px solid var(--border-main, #e5e7eb); border-radius: 8px; color: var(--text-main, #111827); padding: 8px 10px; font-size: 14px; font-family: inherit; outline: none; text-align: center; }
.field-row input:focus { border-color: #7c6fe0; background: var(--bg-card, #fff); }
.field-row .unit { color: var(--text-muted, #6b7280); font-size: 13px; }
.field-hint { color: var(--text-muted, #9ca3af); font-size: 12px; margin-top: 4px; }
.section-divider { border-top: 1px solid var(--border-main, #e5e7eb); margin: 20px 0; }
.success-msg { background: #f0fdf4; border: 1px solid #bbf7d0; color: #16a34a; font-size: 13px; padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; }
{% endblock %}
{% block content %}
<div class="settings-card">
  <h2>项目保留周期</h2>

  {% with messages = get_flashed_messages() %}
    {% if messages %}<p class="success-msg">{{ messages[0] }}</p>{% endif %}
  {% endwith %}

  <form method="post">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

    <div class="field-group">
      <label>全局默认</label>
      <div class="field-row">
        <input type="number" name="retention_default_days" min="1" step="1"
               value="{{ (current.get('default', 168) / 24) | int }}">
        <span class="unit">天</span>
      </div>
      <div class="field-hint">所有模块的默认保留周期，项目完成后开始计算</div>
    </div>

    <div class="section-divider"></div>

    <p style="color:var(--text-muted,#6b7280);font-size:13px;margin-bottom:16px;">按模块覆盖（留空表示使用全局默认）</p>

    {% for ptype, label in project_types.items() %}
    <div class="field-group">
      <label>{{ label }}</label>
      <div class="field-row">
        <input type="number" name="retention_{{ ptype }}_days" min="1" step="1"
               value="{% if current.get(ptype) is not none %}{{ (current[ptype] / 24) | int }}{% endif %}"
               placeholder="默认">
        <span class="unit">天</span>
      </div>
    </div>
    {% endfor %}

    <div class="section-divider"></div>

    <button type="submit" style="background:var(--primary-gradient,#3b82f6);color:#fff;border:none;border-radius:8px;padding:10px 24px;font-size:14px;font-weight:600;cursor:pointer;">
      保存
    </button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 3: 修改 layout.html 侧边栏**

在 `web/templates/layout.html` 第 321 行（用户管理链接）之后追加：

```html
      <a href="{{ url_for('admin.settings') }}" {% if request.endpoint == 'admin.settings' %}class="active"{% endif %}>
        <span class="nav-icon">⚙️</span> 系统设置
      </a>
```

- [ ] **Step 4: 手动验证**

1. 启动应用
2. 以 admin 登录
3. 侧边栏可见"系统设置"
4. 进入 `/admin/settings`，看到全局默认 7 天
5. 修改文案创作为 3 天，保存
6. 刷新页面，值保留

- [ ] **Step 5: Commit**

```bash
git add web/routes/admin.py web/templates/admin_settings.html web/templates/layout.html
git commit -m "feat: admin 后台新增系统设置页面，支持配置项目保留周期"
```

---

### Task 7: 更新 schema.sql 保持一致

**Files:**
- Modify: `db/schema.sql:35`

- [ ] **Step 1: 在 schema.sql 中追加 system_settings 表定义**

在 `projects` 表定义之后追加：

```sql
CREATE TABLE IF NOT EXISTS system_settings (
    `key`      VARCHAR(100) PRIMARY KEY,
    `value`    TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: 将 projects 表的 expires_at 改为允许 NULL**

```sql
-- 旧:
    expires_at       DATETIME NOT NULL,
-- 新:
    expires_at       DATETIME,
```

- [ ] **Step 3: Commit**

```bash
git add db/schema.sql
git commit -m "chore: schema.sql 同步 system_settings 表和 expires_at 可空"
```
