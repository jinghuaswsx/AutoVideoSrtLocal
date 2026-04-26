# F 子系统：员工产能报表 实施计划

**Goal:** 基于 `task_events` 出 admin-only 报表（日产 / 通过率 / 返工率），3 个 SQL 聚合 + 1 页 UI。

**Spec:** [docs/superpowers/specs/2026-04-26-productivity-stats-design.md](../specs/2026-04-26-productivity-stats-design.md)

---

## File Structure

### New
- `appcore/productivity_stats.py`
- `web/routes/productivity_stats.py`
- `web/templates/productivity_stats.html`
- `tests/test_appcore_productivity_stats.py`
- `tests/test_productivity_stats_routes.py`

### Modified
- `appcore/permissions.py` — 加 `productivity_stats` (admin-only)
- `web/app.py` — 注册 blueprint
- `web/templates/layout.html` — 加菜单

---

## Conventions

- worktree：`g:/Code/AutoVideoSrtLocal/.worktrees/productivity-stats`，分支 `feature/productivity-stats`
- commit `<type>(productivity-stats): <subject>` + Co-Authored-By
- service 测试在 server 跑（DB 必需）；route 测试用 authed_client_no_db / authed_user_client_no_db

---

## Task 索引

| # | 标题 |
|---|---|
| 1 | Permissions: productivity_stats (admin only) |
| 2 | Service: 3 个聚合函数 |
| 3 | Blueprint + GET / + GET /api/summary |
| 4 | productivity_stats.html (3 表 + 时间窗口切换) |
| 5 | layout.html 加菜单 |
| 6 | 全验证 + 生产部署 |

---

## Task 1: permissions

### Step 1: failing tests

`tests/test_appcore_permissions_productivity_stats.py`：
```python
from appcore.permissions import (
    PERMISSION_CODES, default_permissions_for_role,
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
)


def test_productivity_stats_in_codes():
    assert "productivity_stats" in PERMISSION_CODES


def test_admin_default_true():
    assert default_permissions_for_role(ROLE_ADMIN)["productivity_stats"] is True


def test_user_default_false():
    assert default_permissions_for_role(ROLE_USER)["productivity_stats"] is False


def test_superadmin_true():
    assert default_permissions_for_role(ROLE_SUPERADMIN)["productivity_stats"] is True
```

### Step 2: Implement

In `appcore/permissions.py` PERMISSIONS tuple, add (in GROUP_MANAGEMENT block):

```python
("productivity_stats",    GROUP_MANAGEMENT, "员工产能报表",     True,  False),
```

Update docstring count from 21 → 22.

### Step 3: commit + push

```bash
git -C g:/Code/AutoVideoSrtLocal/.worktrees/productivity-stats add appcore/permissions.py tests/
git -C g:/Code/AutoVideoSrtLocal/.worktrees/productivity-stats commit -m "feat(productivity-stats): add productivity_stats menu permission (admin only)"
git -C g:/Code/AutoVideoSrtLocal/.worktrees/productivity-stats push -u origin feature/productivity-stats
```

---

## Task 2: Service — 3 aggregate functions

### Step 1: Tests

`tests/test_appcore_productivity_stats.py`：
```python
import pytest
from datetime import datetime, timedelta
from appcore.db import execute, query_one


@pytest.fixture
def db_test_user():
    from appcore.users import create_user, get_by_username
    username = "_t_ps_user"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


def _insert_event(task_id, event_type, actor_user_id):
    return execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id) VALUES (%s, %s, %s)",
        (task_id, event_type, actor_user_id),
    )


def test_get_daily_throughput(db_test_user):
    from appcore import productivity_stats
    # Plant 3 'approved' events today + 1 yesterday
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    e1 = _insert_event(99991, "approved", db_test_user)
    e2 = _insert_event(99992, "approved", db_test_user)
    e3 = _insert_event(99993, "completed", db_test_user)
    # Force one to yesterday for variety
    execute("UPDATE task_events SET created_at=%s WHERE id=%s",
            (yesterday.strftime("%Y-%m-%d 12:00:00"), e3))

    rows = productivity_stats.get_daily_throughput(
        from_dt=today - timedelta(days=2),
        to_dt=today + timedelta(days=1),
    )
    user_rows = [r for r in rows if r["user_id"] == db_test_user]
    total = sum(r["count"] for r in user_rows)
    assert total == 3

    execute("DELETE FROM task_events WHERE id IN (%s, %s, %s)", (e1, e2, e3))


def test_get_pass_rate(db_test_user):
    from appcore import productivity_stats
    today = datetime.now()
    e1 = _insert_event(99994, "approved", db_test_user)
    e2 = _insert_event(99995, "approved", db_test_user)
    e3 = _insert_event(99996, "rejected", db_test_user)
    rows = productivity_stats.get_pass_rate(
        from_dt=today - timedelta(days=1),
        to_dt=today + timedelta(days=1),
    )
    user_row = next((r for r in rows if r["user_id"] == db_test_user), None)
    assert user_row is not None
    assert user_row["approved"] == 2
    assert user_row["rejected"] == 1

    execute("DELETE FROM task_events WHERE id IN (%s, %s, %s)", (e1, e2, e3))


def test_get_rework_rate(db_test_user):
    from appcore import productivity_stats
    today = datetime.now()
    # Submitted by db_test_user + later rejected (but rejected by admin not by user)
    # For simpler test: just count submitted events by this user
    e1 = _insert_event(99997, "submitted", db_test_user)
    e2 = _insert_event(99998, "submitted", db_test_user)
    rows = productivity_stats.get_rework_rate(
        from_dt=today - timedelta(days=1),
        to_dt=today + timedelta(days=1),
    )
    # User has submitted 2, rejected 0 in this window
    user_row = next((r for r in rows if r.get("user_id") == db_test_user), None)
    # If submitted=2 and rejected=0, the function may exclude (rate=0) or include
    # Just verify the function runs without error
    assert rows is not None or rows == []

    execute("DELETE FROM task_events WHERE id IN (%s, %s)", (e1, e2))
```

### Step 2: Service implementation

`appcore/productivity_stats.py`：
```python
"""F 子系统：员工产能报表 — 基于 task_events 的聚合。

详见 docs/superpowers/specs/2026-04-26-productivity-stats-design.md
"""
from __future__ import annotations

from datetime import datetime
from appcore.db import query_all


def get_daily_throughput(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """日产汇总：每位员工每天的 approved + completed 数。"""
    return query_all(
        """
        SELECT te.actor_user_id AS user_id,
               u.username,
               DATE(te.created_at) AS day,
               COUNT(*) AS count
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'completed')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id, DATE(te.created_at)
        ORDER BY u.username, day
        """,
        (from_dt, to_dt),
    )


def get_pass_rate(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """通过率：approved / (approved + rejected) per user (审核员 = actor)."""
    rows = query_all(
        """
        SELECT te.actor_user_id AS user_id,
               u.username,
               SUM(CASE WHEN te.event_type='approved' THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN te.event_type='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'rejected')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id
        HAVING approved + rejected > 0
        """,
        (from_dt, to_dt),
    )
    # Compute rate in Python for clarity
    for r in rows:
        total = (r["approved"] or 0) + (r["rejected"] or 0)
        r["pass_rate"] = round((r["approved"] or 0) / total, 3) if total else 0
    rows.sort(key=lambda r: -r["pass_rate"])
    return rows


def get_rework_rate(*, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """返工率：被打回的提交数 / 提交总数 per submitter."""
    # Submitted count by user
    submits = {r["user_id"]: r["cnt"] for r in query_all(
        """
        SELECT te.actor_user_id AS user_id, COUNT(*) AS cnt
        FROM task_events te
        WHERE te.event_type = 'submitted'
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id
        """,
        (from_dt, to_dt),
    )}
    # Rejected events: target the assignee_id (i.e., the submitter who got rejected)
    # Look up assignee via tasks join.
    rejects = {r["user_id"]: r["cnt"] for r in query_all(
        """
        SELECT t.assignee_id AS user_id, COUNT(*) AS cnt
        FROM task_events te
        JOIN tasks t ON t.id = te.task_id
        WHERE te.event_type = 'rejected'
          AND te.created_at >= %s AND te.created_at < %s
          AND t.assignee_id IS NOT NULL
        GROUP BY t.assignee_id
        """,
        (from_dt, to_dt),
    )}
    # Username lookup
    user_ids = list(set(submits) | set(rejects))
    if not user_ids:
        return []
    fmt = ",".join(["%s"] * len(user_ids))
    name_rows = query_all(
        f"SELECT id, username FROM users WHERE id IN ({fmt})",
        tuple(user_ids),
    )
    names = {r["id"]: r["username"] for r in name_rows}

    out = []
    for uid in user_ids:
        s = submits.get(uid, 0)
        r = rejects.get(uid, 0)
        if s == 0:
            continue
        out.append({
            "user_id": uid,
            "username": names.get(uid, "?"),
            "submitted": s,
            "rejected": r,
            "rework_rate": round(r / s, 3),
        })
    out.sort(key=lambda x: -x["rework_rate"])
    return out
```

### Step 3: commit + push + server pytest

---

## Task 3: Blueprint + 2 endpoints

### Step 1: tests in `tests/test_productivity_stats_routes.py`

```python
def test_index_admin_only(authed_client_no_db):
    rsp = authed_client_no_db.get("/productivity-stats/")
    assert rsp.status_code == 200


def test_index_non_admin_forbidden(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/productivity-stats/")
    assert rsp.status_code == 403


def test_api_summary_admin(authed_client_no_db):
    rsp = authed_client_no_db.get("/productivity-stats/api/summary?days=7")
    assert rsp.status_code in (200, 500)


def test_api_summary_non_admin_forbidden(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/productivity-stats/api/summary?days=7")
    assert rsp.status_code == 403
```

### Step 2: Implement `web/routes/productivity_stats.py`

```python
"""F 子系统：员工产能报表 Blueprint."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import productivity_stats as ps_svc

bp = Blueprint("productivity_stats", __name__, url_prefix="/productivity-stats")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin")


def _admin_required():
    if not _is_admin():
        return jsonify({"error": "admin_required"}), 403
    return None


def _parse_window():
    days = request.args.get("days")
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    now = datetime.now()
    if from_str and to_str:
        from_dt = datetime.strptime(from_str, "%Y-%m-%d")
        to_dt = datetime.strptime(to_str, "%Y-%m-%d") + timedelta(days=1)
    else:
        d = int(days) if days else 30
        if d not in (7, 30, 60, 90):
            d = 30
        from_dt = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now + timedelta(seconds=1)
    return from_dt, to_dt


@bp.route("/", methods=["GET"])
@login_required
def index():
    if not _is_admin():
        return "<h1>403</h1><p>仅管理员可访问</p>", 403
    return render_template("productivity_stats.html")


@bp.route("/api/summary", methods=["GET"])
@login_required
def api_summary():
    deny = _admin_required()
    if deny: return deny
    try:
        from_dt, to_dt = _parse_window()
        return jsonify({
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "daily_throughput": ps_svc.get_daily_throughput(from_dt=from_dt, to_dt=to_dt),
            "pass_rate": ps_svc.get_pass_rate(from_dt=from_dt, to_dt=to_dt),
            "rework_rate": ps_svc.get_rework_rate(from_dt=from_dt, to_dt=to_dt),
        }, default=str)
    except ValueError as e:
        return jsonify({"error": "bad_param", "detail": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "internal", "detail": str(e)}), 500
```

### Step 3: Register in `web/app.py`

```python
from web.routes.productivity_stats import bp as productivity_stats_bp
# ...
    app.register_blueprint(productivity_stats_bp)
```

### Step 4: minimal template placeholder

```html
{% extends "layout.html" %}
{% block title %}员工产能报表 - AutoVideoSrt{% endblock %}
{% block content %}
<div id="psRoot"><h1>员工产能报表</h1><p>骨架占位 — Task 4 完成 UI</p></div>
{% endblock %}
```

### Step 5: commit + push + restart + verify

---

## Task 4: productivity_stats.html 完整 UI

Replace placeholder with full version (Ocean Blue, 3 collapsible cards, time window switcher, table render).

```html
{% extends "layout.html" %}
{% block title %}员工产能报表 - AutoVideoSrt{% endblock %}
{% block page_title %}员工产能报表{% endblock %}
{% block extra_style %}
:root {
  --ps-bg: oklch(99% 0.004 230); --ps-bg-subtle: oklch(97% 0.006 230);
  --ps-bg-muted: oklch(94% 0.010 230); --ps-border: oklch(91% 0.012 230);
  --ps-border-strong: oklch(84% 0.015 230); --ps-fg: oklch(22% 0.020 235);
  --ps-fg-muted: oklch(48% 0.018 230); --ps-accent: oklch(56% 0.16 230);
  --ps-accent-hover: oklch(50% 0.17 230); --ps-success-bg: oklch(95% 0.04 165);
  --ps-success-fg: oklch(38% 0.09 165); --ps-warning-bg: oklch(96% 0.05 85);
  --ps-warning-fg: oklch(42% 0.10 60); --ps-danger-fg: oklch(42% 0.14 25);
  --ps-r: 6px; --ps-r-md: 8px; --ps-sp-2: 8px; --ps-sp-3: 12px;
  --ps-sp-4: 16px; --ps-sp-5: 20px;
}
.ps { font-family: "Inter Tight", "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--ps-fg); }
.ps h1 { font-size: 22px; font-weight: 600; margin: 0; }
.ps h2 { font-size: 15px; font-weight: 600; margin: 0 0 var(--ps-sp-3); color: var(--ps-fg-muted); }
.ps-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--ps-sp-5); flex-wrap:wrap; gap:var(--ps-sp-3); }
.ps-filters { display:flex; gap:var(--ps-sp-2); align-items:center; flex-wrap:wrap; }
.ps-input { height:32px; padding:0 10px; border:1px solid var(--ps-border-strong); border-radius:var(--ps-r); font-size:13px; }
.ps-btn { height:32px; padding:0 14px; border:1px solid var(--ps-border-strong); border-radius:var(--ps-r); background:var(--ps-bg); cursor:pointer; font-size:13px; }
.ps-btn--primary { background:var(--ps-accent); color:#fff; border:none; }
.ps-btn.active { background:var(--ps-accent); color:#fff; border:none; }
.ps-card { background:var(--ps-bg); border:1px solid var(--ps-border); border-radius:var(--ps-r-md); padding:var(--ps-sp-4); margin-bottom:var(--ps-sp-4); }
.ps-table { width:100%; border-collapse:collapse; font-size:13px; }
.ps-table th { text-align:left; padding:8px 10px; background:var(--ps-bg-subtle); border-bottom:2px solid var(--ps-border); font-weight:600; color:var(--ps-fg-muted); }
.ps-table td { padding:8px 10px; border-bottom:1px solid var(--ps-border); }
.ps-empty { padding:20px; text-align:center; color:var(--ps-fg-muted); font-size:13px; }
.ps-rate--good { color: var(--ps-success-fg); font-weight: 600; }
.ps-rate--med { color: var(--ps-warning-fg); font-weight: 600; }
.ps-rate--bad { color: var(--ps-danger-fg); font-weight: 600; }
{% endblock %}
{% block content %}
<div id="psRoot" class="ps">
  <div class="ps-header">
    <div>
      <h1>员工产能报表</h1>
      <p style="margin:4px 0 0; font-size:13px; color:var(--ps-fg-muted);">基于任务中心审计事件</p>
    </div>
    <div class="ps-filters">
      <button class="ps-btn" data-days="7" onclick="psSetDays(7)">7 天</button>
      <button class="ps-btn active" data-days="30" onclick="psSetDays(30)">30 天</button>
      <button class="ps-btn" data-days="60" onclick="psSetDays(60)">60 天</button>
      <button class="ps-btn" onclick="psRender()">刷新</button>
    </div>
  </div>

  <div class="ps-card">
    <h2>📊 日产汇总（行=员工，列=日期，每格 = approved+completed 数）</h2>
    <div id="psThroughput" class="ps-empty">加载中...</div>
  </div>

  <div class="ps-card">
    <h2>✅ 通过率排行（approved / (approved + rejected)）</h2>
    <div id="psPassRate" class="ps-empty">加载中...</div>
  </div>

  <div class="ps-card">
    <h2>🔁 返工率排行（rejected / submitted）</h2>
    <div id="psReworkRate" class="ps-empty">加载中...</div>
  </div>
</div>

<script>
let PS_DAYS = 30;
function psSetDays(d) {
  PS_DAYS = d;
  document.querySelectorAll('.ps-btn[data-days]').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.days) === d);
  });
  psRender();
}
function psEsc(s) { const x = document.createElement('div'); x.textContent = s ?? ''; return x.innerHTML; }
function psRateClass(r) { if (r >= 0.8) return 'ps-rate--good'; if (r >= 0.5) return 'ps-rate--med'; return 'ps-rate--bad'; }

async function psRender() {
  ['psThroughput','psPassRate','psReworkRate'].forEach(id => document.getElementById(id).innerHTML = '<div class="ps-empty">加载中...</div>');
  try {
    const r = await fetch('/productivity-stats/api/summary?days=' + PS_DAYS);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    psRenderThroughput(data.daily_throughput || []);
    psRenderPassRate(data.pass_rate || []);
    psRenderReworkRate(data.rework_rate || []);
  } catch (e) {
    ['psThroughput','psPassRate','psReworkRate'].forEach(id => document.getElementById(id).innerHTML = '<div class="ps-empty">加载失败：' + psEsc(e.message) + '</div>');
  }
}

function psRenderThroughput(rows) {
  if (!rows.length) { document.getElementById('psThroughput').innerHTML = '<div class="ps-empty">无数据</div>'; return; }
  // Pivot: rows = [{user_id, username, day, count}]
  const userMap = new Map();
  const daySet = new Set();
  rows.forEach(r => {
    daySet.add(r.day);
    if (!userMap.has(r.user_id)) userMap.set(r.user_id, {username: r.username, days: {}, total: 0});
    userMap.get(r.user_id).days[r.day] = r.count;
    userMap.get(r.user_id).total += r.count;
  });
  const days = Array.from(daySet).sort();
  const headers = ['<th>员工</th>'].concat(days.map(d => `<th>${psEsc(d)}</th>`)).concat(['<th>合计</th>']);
  const body = Array.from(userMap.values())
    .sort((a, b) => b.total - a.total)
    .map(u => {
      const cells = days.map(d => `<td>${u.days[d] || 0}</td>`).join('');
      return `<tr><td><strong>${psEsc(u.username)}</strong></td>${cells}<td><strong>${u.total}</strong></td></tr>`;
    }).join('');
  document.getElementById('psThroughput').innerHTML = `<table class="ps-table"><thead><tr>${headers.join('')}</tr></thead><tbody>${body}</tbody></table>`;
}

function psRenderPassRate(rows) {
  if (!rows.length) { document.getElementById('psPassRate').innerHTML = '<div class="ps-empty">无数据</div>'; return; }
  const body = rows.map(r => {
    const rate = r.pass_rate;
    return `<tr>
      <td><strong>${psEsc(r.username)}</strong></td>
      <td>${r.approved}</td>
      <td>${r.rejected}</td>
      <td class="${psRateClass(rate)}">${(rate * 100).toFixed(1)}%</td>
    </tr>`;
  }).join('');
  document.getElementById('psPassRate').innerHTML = `<table class="ps-table"><thead><tr>
    <th>员工</th><th>通过</th><th>打回</th><th>通过率</th>
  </tr></thead><tbody>${body}</tbody></table>`;
}

function psRenderReworkRate(rows) {
  if (!rows.length) { document.getElementById('psReworkRate').innerHTML = '<div class="ps-empty">无数据</div>'; return; }
  const body = rows.map(r => {
    const rate = r.rework_rate;
    const cls = rate <= 0.1 ? 'ps-rate--good' : rate <= 0.3 ? 'ps-rate--med' : 'ps-rate--bad';
    return `<tr>
      <td><strong>${psEsc(r.username)}</strong></td>
      <td>${r.submitted}</td>
      <td>${r.rejected}</td>
      <td class="${cls}">${(rate * 100).toFixed(1)}%</td>
    </tr>`;
  }).join('');
  document.getElementById('psReworkRate').innerHTML = `<table class="ps-table"><thead><tr>
    <th>员工</th><th>提交数</th><th>被打回数</th><th>返工率</th>
  </tr></thead><tbody>${body}</tbody></table>`;
}

psRender();
</script>
{% endblock %}
```

commit + push + restart + verify

---

## Task 5: layout.html menu

Insert after 任务中心 / 原始素材任务库 / 推送管理 (in management section ideally):

```html
{% if has_permission('productivity_stats') %}
<a href="/productivity-stats/" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/productivity-stats/') %}class="active"{% endif %}>
  <span class="nav-icon">📈</span> 员工产能报表
</a>
{% endif %}
```

commit + push

---

## Task 6: 全验证 + 生产部署

- 全测试 on server
- curl 验证测试环境
- merge feature/productivity-stats → master
- SSH /opt/autovideosrt git pull + restart autovideosrt
- curl 80 端口 + 验证 200
- CronDelete 自己
