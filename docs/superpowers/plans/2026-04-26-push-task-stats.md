# 推送管理 · 任务统计 Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在推送管理页加 admin-only 的「任务统计」Tab，按产品负责人聚合「素材提交数 / 已推送 / 未推送 / 推送率」，支持自定义日期区间 + 6 个时间快捷按钮，默认本月。

**Architecture:** 路由级分页（`/pushes/` 与 `/pushes/stats` 各自独立 URL，可分享/收藏）。后端在 `appcore/pushes.py` 加纯聚合函数 `aggregate_stats_by_owner`；`web/routes/pushes.py` 暴露 `/stats` 渲染 + `/api/stats` JSON 端点（双双 admin 守卫）。前端两个页面共享 `_pushes_tabs.html` partial；`pushes_stats.html` + `pushes_stats.js/css` 独立成套。

**Tech Stack:** Python 3.14 + Flask + Jinja2 + 原生 JS（无框架，遵循现有 `pushes_list` 风格） + MySQL。测试用 `pytest`，复用 `tests/conftest.py` 的 `authed_client_no_db` / `authed_user_client_no_db` / `logged_in_client` fixtures。

**Spec:** `docs/superpowers/specs/2026-04-26-push-task-stats-design.md`

---

## File Structure

**新增（5 个）：**

| 文件 | 责任 |
|---|---|
| `web/templates/_pushes_tabs.html` | Tab 头 partial，被两个页面 include；`{% if is_admin %}` 包住「任务统计」tab |
| `web/templates/pushes_stats.html` | 任务统计页面：toolbar（日期 + 快捷按钮）+ 表格 + 三态容器 |
| `web/static/pushes_stats.css` | Tab 头样式 + 任务统计表格/工具栏样式（ocean-blue tokens） |
| `web/static/pushes_stats.js` | 拉数据、渲染表格、快捷按钮日期换算、三态切换 |
| `tests/test_pushes_stats.py` | 6 个 `aggregate_stats_by_owner` 单测 + 6 个路由测试 |

**修改（3 个）：**

| 文件 | 改动 |
|---|---|
| `appcore/pushes.py` | 新增 `aggregate_stats_by_owner(date_from, date_to)` + 私有 `_normalize_date_range` |
| `web/routes/pushes.py` | 新增两个路由 `/stats`（admin gate + 渲染）、`/api/stats`（admin gate + JSON） |
| `web/templates/pushes_list.html` | 顶部 include `_pushes_tabs.html`，`active='list'` |

---

## Task 1: 后端聚合函数 `aggregate_stats_by_owner`

**Files:**
- Modify: `appcore/pushes.py`（在文件末尾追加）
- Test: `tests/test_pushes_stats.py`（新建）

- [ ] **Step 1.1: 创建测试文件并写 6 个失败测试**

```python
# tests/test_pushes_stats.py
"""任务统计：聚合函数 + 路由测试。"""
from datetime import date, datetime

import pytest


# ============================================================
# aggregate_stats_by_owner — 纯函数单测
# ============================================================


def test_aggregate_stats_normalizes_dates_and_passes_half_open_window(monkeypatch):
    """指定区间 → SQL 参数应为 [from_dt 00:00:00, to_dt+1day 00:00:00)。"""
    from appcore import pushes
    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")

    assert captured["params"][0] == datetime(2026, 4, 1, 0, 0, 0)
    assert captured["params"][1] == datetime(2026, 4, 27, 0, 0, 0)  # 半开右开
    assert result["date_from"] == "2026-04-01"
    assert result["date_to"] == "2026-04-26"
    assert result["rows"] == []
    assert result["totals"] == {
        "submitted": 0, "pushed": 0, "unpushed": 0, "push_rate": None,
    }


def test_aggregate_stats_default_dates_are_current_month_to_today(monkeypatch):
    """未传 date_from / date_to → 默认 [本月 1 日, 今天]。"""
    from appcore import pushes
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: [])
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner()
    today = date.today()
    assert result["date_from"] == today.replace(day=1).strftime("%Y-%m-%d")
    assert result["date_to"] == today.strftime("%Y-%m-%d")


def test_aggregate_stats_rejects_inverted_range(monkeypatch):
    """date_from > date_to → ValueError。"""
    from appcore import pushes
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )
    with pytest.raises(ValueError):
        pushes.aggregate_stats_by_owner("2026-04-26", "2026-04-01")


def test_aggregate_stats_computes_derived_fields_and_totals(monkeypatch):
    """SQL 返回原始 rows → 函数注入 unpushed / push_rate / 合计。"""
    from appcore import pushes
    fake_rows = [
        {"user_id": 7, "owner_name": "张三", "submitted": 12, "pushed": 8},
        {"user_id": 8, "owner_name": "李四", "submitted": 8, "pushed": 8},
        {"user_id": None, "owner_name": "未指派", "submitted": 3, "pushed": 0},
    ]
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: fake_rows)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert result["rows"][0] == {
        "user_id": 7, "name": "张三",
        "submitted": 12, "pushed": 8, "unpushed": 4,
        "push_rate": pytest.approx(8 / 12),
    }
    assert result["rows"][1]["push_rate"] == pytest.approx(1.0)
    assert result["rows"][2]["push_rate"] == 0.0
    assert result["totals"]["submitted"] == 23
    assert result["totals"]["pushed"] == 16
    assert result["totals"]["unpushed"] == 7
    assert result["totals"]["push_rate"] == pytest.approx(16 / 23)


def test_aggregate_stats_empty_db_returns_null_rate(monkeypatch):
    """没有任何数据 → 合计推送率 = None（前端显示 —）。"""
    from appcore import pushes
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: [])
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )
    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert result["rows"] == []
    assert result["totals"] == {
        "submitted": 0, "pushed": 0, "unpushed": 0, "push_rate": None,
    }


def test_aggregate_stats_sql_filters_and_uses_owner_expr(monkeypatch):
    """SQL 应包含 owner_name_expr、排除 lang='en'、排除 deleted_at。"""
    from appcore import pushes
    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)",
    )
    pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)" in captured["sql"]
    assert "i.lang <> 'en'" in captured["sql"]
    assert "i.deleted_at IS NULL" in captured["sql"]
    assert "p.deleted_at IS NULL" in captured["sql"]
    assert "i.created_at >= %s" in captured["sql"]
    assert "i.created_at <  %s" in captured["sql"]
```

- [ ] **Step 1.2: 运行测试，确认 6 个全部失败**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -q 2>&1 | tail -20`
Expected: 6 个 ERROR/FAIL，原因均为 `AttributeError: module 'appcore.pushes' has no attribute 'aggregate_stats_by_owner'`。

- [ ] **Step 1.3: 在 `appcore/pushes.py` 末尾追加实现**

```python
# ---------- 任务统计聚合（按产品负责人） ----------

from datetime import date as _date, datetime as _datetime, timedelta as _timedelta


def _normalize_date_range(date_from: str | None, date_to: str | None) -> tuple[_date, _date]:
    today = _date.today()
    df = (
        _datetime.strptime(date_from, "%Y-%m-%d").date()
        if date_from else today.replace(day=1)
    )
    dt = (
        _datetime.strptime(date_to, "%Y-%m-%d").date()
        if date_to else today
    )
    if df > dt:
        raise ValueError(f"date_from {df} > date_to {dt}")
    return df, dt


def aggregate_stats_by_owner(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """按产品负责人聚合「素材提交数 / 已推送 / 未推送 / 推送率」。

    Args:
        date_from: 'YYYY-MM-DD'，含；None → 当月 1 日。
        date_to: 'YYYY-MM-DD'，含；None → 今天。

    Returns:
        {
          "rows": [{user_id, name, submitted, pushed, unpushed, push_rate}, ...],
          "totals": {submitted, pushed, unpushed, push_rate},
          "date_from": "YYYY-MM-DD",
          "date_to": "YYYY-MM-DD",
        }

    Raises:
        ValueError: date_from > date_to。
    """
    df, dt = _normalize_date_range(date_from, date_to)
    from_dt = _datetime.combine(df, _datetime.min.time())
    to_dt = _datetime.combine(dt + _timedelta(days=1), _datetime.min.time())

    owner_name_expr = medias._media_product_owner_name_expr()
    sql = (
        "SELECT "
        "  u.id AS user_id, "
        f" COALESCE({owner_name_expr}, '未指派') AS owner_name, "
        "  COUNT(*) AS submitted, "
        "  SUM(CASE WHEN i.pushed_at IS NOT NULL THEN 1 ELSE 0 END) AS pushed "
        "FROM media_items i "
        "JOIN media_products p ON p.id = i.product_id "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE i.deleted_at IS NULL "
        "  AND p.deleted_at IS NULL "
        "  AND i.lang <> 'en' "
        "  AND i.created_at >= %s "
        "  AND i.created_at <  %s "
        "GROUP BY u.id, owner_name "
        "ORDER BY submitted DESC, owner_name ASC"
    )
    rows = query(sql, (from_dt, to_dt))

    out_rows = []
    total_submitted = 0
    total_pushed = 0
    for r in rows or []:
        sub = int(r.get("submitted") or 0)
        push = int(r.get("pushed") or 0)
        unp = sub - push
        rate = (push / sub) if sub > 0 else None
        out_rows.append({
            "user_id": r.get("user_id"),
            "name": r.get("owner_name") or "未指派",
            "submitted": sub,
            "pushed": push,
            "unpushed": unp,
            "push_rate": rate,
        })
        total_submitted += sub
        total_pushed += push

    total_unpushed = total_submitted - total_pushed
    total_rate = (total_pushed / total_submitted) if total_submitted > 0 else None

    return {
        "rows": out_rows,
        "totals": {
            "submitted": total_submitted,
            "pushed": total_pushed,
            "unpushed": total_unpushed,
            "push_rate": total_rate,
        },
        "date_from": df.strftime("%Y-%m-%d"),
        "date_to": dt.strftime("%Y-%m-%d"),
    }
```

- [ ] **Step 1.4: 运行测试，确认 6 个全部通过**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -q 2>&1 | tail -10`
Expected: `6 passed`。

- [ ] **Step 1.5: 跑现有 push 测试套件确保未回归**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_routes.py tests/test_pushes_shopify_image_readiness.py tests/test_pushes_ui_assets.py -q 2>&1 | tail -10`
Expected: 所有用例 passed。

- [ ] **Step 1.6: Commit**

```bash
git add appcore/pushes.py tests/test_pushes_stats.py
git commit -m "feat(push-stats): add aggregate_stats_by_owner helper

Aggregates submitted-vs-pushed counts per product owner over a date
range, with derived push_rate and totals. Used by the upcoming
/pushes/stats tab."
```

---

## Task 2: 路由 `/pushes/stats` 与 `/pushes/api/stats`

**Files:**
- Modify: `web/routes/pushes.py`（追加两个路由）
- Create: `web/templates/pushes_stats.html`（最小骨架，仅 `extends layout.html` + h1）
- Test: `tests/test_pushes_stats.py`（追加 6 个路由测试）

- [ ] **Step 2.1: 在 `tests/test_pushes_stats.py` 末尾追加路由测试**

```python
# ============================================================
# 路由：/pushes/stats（页面） + /pushes/api/stats（JSON）
# ============================================================


def test_stats_page_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/stats")
    assert resp.status_code == 403


def test_stats_page_loads_for_admin(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/stats")
    assert resp.status_code == 200
    assert "任务统计".encode("utf-8") in resp.data


def test_api_stats_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/stats")
    assert resp.status_code == 403


def test_api_stats_returns_aggregate_payload(authed_client_no_db, monkeypatch):
    fake = {
        "rows": [{"user_id": 7, "name": "张三",
                  "submitted": 12, "pushed": 8, "unpushed": 4, "push_rate": 0.667}],
        "totals": {"submitted": 12, "pushed": 8, "unpushed": 4, "push_rate": 0.667},
        "date_from": "2026-04-01",
        "date_to": "2026-04-26",
    }
    captured = {}

    def fake_agg(date_from=None, date_to=None):
        captured["from"] = date_from
        captured["to"] = date_to
        return fake

    monkeypatch.setattr(
        "web.routes.pushes.pushes.aggregate_stats_by_owner", fake_agg,
    )
    resp = authed_client_no_db.get(
        "/pushes/api/stats?date_from=2026-04-01&date_to=2026-04-26",
    )
    assert resp.status_code == 200
    assert resp.get_json() == fake
    assert captured["from"] == "2026-04-01"
    assert captured["to"] == "2026-04-26"


def test_api_stats_invalid_range_returns_400(authed_client_no_db, monkeypatch):
    def boom(date_from=None, date_to=None):
        raise ValueError("date_from > date_to")
    monkeypatch.setattr(
        "web.routes.pushes.pushes.aggregate_stats_by_owner", boom,
    )
    resp = authed_client_no_db.get(
        "/pushes/api/stats?date_from=2026-04-26&date_to=2026-04-01",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_date_range"


def test_api_stats_passes_none_when_dates_omitted(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_agg(date_from=None, date_to=None):
        captured["from"] = date_from
        captured["to"] = date_to
        return {
            "rows": [],
            "totals": {"submitted": 0, "pushed": 0, "unpushed": 0, "push_rate": None},
            "date_from": "2026-04-01",
            "date_to": "2026-04-26",
        }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.aggregate_stats_by_owner", fake_agg,
    )
    resp = authed_client_no_db.get("/pushes/api/stats")
    assert resp.status_code == 200
    assert captured["from"] is None
    assert captured["to"] is None
```

- [ ] **Step 2.2: 运行测试，确认 6 个全部失败**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -k "stats_page or api_stats" -q 2>&1 | tail -20`
Expected: 6 个 FAIL（404 / template not found / route not registered）。

- [ ] **Step 2.3: 创建最小骨架模板 `web/templates/pushes_stats.html`**

```jinja
{% extends "layout.html" %}
{% block title %}任务统计{% endblock %}
{% block content %}
<div class="page-header">
  <h1>🚀 推送管理</h1>
</div>

<h2>任务统计</h2>
{% endblock %}
```

> 这是为了让 `test_stats_page_loads_for_admin` 通过；完整 UI 在 Task 4-5 替换。

- [ ] **Step 2.4: 在 `web/routes/pushes.py` 顶部 import 处下方追加两个路由**

定位到现有 `@bp.route("/api/items/<int:item_id>/logs", methods=["GET"])` 路由结束处之后（约行 365 之后）插入以下代码：

```python
# ================================================================
# 任务统计 Tab：/pushes/stats（页面） + /pushes/api/stats（JSON）
# 仅 admin。
# ================================================================


@bp.route("/stats")
@login_required
@admin_required
def stats():
    return render_template(
        "pushes_stats.html",
        is_admin=True,
    )


@bp.route("/api/stats", methods=["GET"])
@login_required
@admin_required
def api_stats():
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    try:
        result = pushes.aggregate_stats_by_owner(date_from, date_to)
    except ValueError as exc:
        return jsonify({"error": "invalid_date_range", "detail": str(exc)}), 400
    return jsonify(result)
```

- [ ] **Step 2.5: 运行新增的 6 个路由测试，确认全部通过**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -k "stats_page or api_stats" -q 2>&1 | tail -10`
Expected: `6 passed`。

- [ ] **Step 2.6: 跑全文件确保 task 1 的 6 个 + task 2 的 6 个共 12 个全部 OK**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -q 2>&1 | tail -10`
Expected: `12 passed`。

- [ ] **Step 2.7: Commit**

```bash
git add web/routes/pushes.py web/templates/pushes_stats.html tests/test_pushes_stats.py
git commit -m "feat(push-stats): add /pushes/stats route and /api/stats endpoint

Both routes are admin-only. The page route renders a minimal skeleton
template; full UI lands in a follow-up commit."
```

---

## Task 3: Tab 头 partial + `pushes_list.html` 接入

**Files:**
- Create: `web/templates/_pushes_tabs.html`
- Modify: `web/templates/pushes_list.html`（顶部 include partial）
- Modify: `web/templates/pushes_stats.html`（替换 `<h2>任务统计</h2>` 为 partial 渲染）

> 该 task 主要是模板，靠 task 2 已建立的 `test_stats_page_loads_for_admin` 同时验证 partial 渲染未崩溃；额外加一个测试确认两个页面都引用 partial。

- [ ] **Step 3.1: 在 `tests/test_pushes_stats.py` 末尾追加 partial 测试**

```python
# ============================================================
# Tab 头 partial（_pushes_tabs.html）
# ============================================================


def test_pushes_list_renders_tabs_with_list_active(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # tab 头容器与两个 tab 文案都存在
    assert "pushes-tabs" in text
    assert "推送管理" in text
    assert "任务统计" in text
    # list tab 高亮（active class 或 aria-current）
    assert 'data-tab-active="list"' in text


def test_pushes_stats_renders_tabs_with_stats_active(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/stats")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "pushes-tabs" in text
    assert 'data-tab-active="stats"' in text


def test_pushes_list_hides_stats_tab_for_non_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # 普通用户看不到「任务统计」tab
    assert "任务统计" not in text
```

- [ ] **Step 3.2: 运行 partial 测试，确认 3 个失败**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -k "tabs" -q 2>&1 | tail -10`
Expected: 3 FAIL（`pushes-tabs` not in HTML 等）。

- [ ] **Step 3.3: 创建 `web/templates/_pushes_tabs.html`**

```jinja
{# 推送管理页 / 任务统计页 共用的 tab 头。
   调用方传入 active='list' 或 active='stats' 与 is_admin。 #}
<nav class="pushes-tabs" data-tab-active="{{ active }}" aria-label="推送模块导航">
  <a class="pushes-tab {% if active == 'list' %}is-active{% endif %}"
     href="/pushes/"
     aria-current="{% if active == 'list' %}page{% else %}false{% endif %}">
    推送管理
  </a>
  {% if is_admin %}
  <a class="pushes-tab {% if active == 'stats' %}is-active{% endif %}"
     href="/pushes/stats"
     aria-current="{% if active == 'stats' %}page{% else %}false{% endif %}">
    任务统计
  </a>
  {% endif %}
</nav>
```

- [ ] **Step 3.4: 修改 `web/templates/pushes_list.html`，在 page-header 之后 include partial**

把现有的：
```jinja
<div class="page-header">
  <h1>🚀 推送管理</h1>
</div>

<div class="push-toolbar">
```

改成：
```jinja
<div class="page-header">
  <h1>🚀 推送管理</h1>
</div>

{% include "_pushes_tabs.html" with context %}

<div class="push-toolbar">
```

> Jinja2 的 `include` 默认带上下文，`with context` 可省略；这里显式写明便于 reader 理解。

调用方需要在视图传 `active='list'` 与 `is_admin` —— `is_admin` 已传，需补 `active`。修改 `web/routes/pushes.py` 的 `index()`：

```python
@bp.route("/")
@login_required
def index():
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        active="list",
    )
```

- [ ] **Step 3.5: 修改 `web/templates/pushes_stats.html`，引入 partial 与 active='stats'**

替换文件全文为：
```jinja
{% extends "layout.html" %}
{% block title %}任务统计{% endblock %}
{% block content %}
<div class="page-header">
  <h1>🚀 推送管理</h1>
</div>

{% include "_pushes_tabs.html" with context %}

<h2>任务统计</h2>
{% endblock %}
```

修改 `web/routes/pushes.py` 的 `stats()` 视图，加 `active="stats"`：

```python
@bp.route("/stats")
@login_required
@admin_required
def stats():
    return render_template(
        "pushes_stats.html",
        is_admin=True,
        active="stats",
    )
```

- [ ] **Step 3.6: 运行所有 stats 测试，确认 partial 测试通过 + 之前的不回归**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -q 2>&1 | tail -10`
Expected: `15 passed`（task 1 的 6 + task 2 的 6 + task 3 的 3）。

- [ ] **Step 3.7: 跑现有推送测试套件确保不回归**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_routes.py tests/test_pushes_ui_assets.py -q 2>&1 | tail -10`
Expected: 全部 passed（pushes_list 仍然 200 + 含「推送管理」字样）。

- [ ] **Step 3.8: Commit**

```bash
git add web/templates/_pushes_tabs.html web/templates/pushes_list.html web/templates/pushes_stats.html web/routes/pushes.py tests/test_pushes_stats.py
git commit -m "feat(push-stats): add tab header partial shared across pushes pages

Both /pushes/ and /pushes/stats now include _pushes_tabs.html. The stats
tab is admin-only and hidden from regular users."
```

---

## Task 4: 完整 `pushes_stats.html` UI 骨架

**Files:**
- Modify: `web/templates/pushes_stats.html`（完整 UI 骨架，等 JS 来填充）

> 这一步只改模板，靠浏览器视觉验证；不写新单测（前端结构由 task 5 的 JS 行为测试覆盖）。

- [ ] **Step 4.1: 用以下完整内容覆盖 `web/templates/pushes_stats.html`**

```jinja
{% extends "layout.html" %}
{% block title %}任务统计{% endblock %}
{% block content %}
<div class="page-header">
  <h1>🚀 推送管理</h1>
</div>

{% include "_pushes_tabs.html" with context %}

<div class="stats-toolbar">
  <div class="stats-filter-row">
    <label class="stats-field">
      <span>时间范围</span>
      <input id="stats-from" type="date" aria-label="起始日期" />
    </label>
    <span class="range-sep">至</span>
    <label class="stats-field">
      <span>&nbsp;</span>
      <input id="stats-to" type="date" aria-label="截止日期" />
    </label>
  </div>
  <div class="stats-quick-row" role="group" aria-label="快捷时间范围">
    <button type="button" data-range="today">今天</button>
    <button type="button" data-range="yesterday">昨天</button>
    <button type="button" data-range="this-week">本周</button>
    <button type="button" data-range="last-week">上周</button>
    <button type="button" data-range="this-month">本月</button>
    <button type="button" data-range="last-month">上月</button>
  </div>
  <div class="stats-actions">
    <button id="stats-apply" type="button" class="primary">筛选</button>
    <button id="stats-reset" type="button">重置</button>
  </div>
</div>

<div id="stats-error" class="stats-error" hidden></div>

<table class="stats-table">
  <thead>
    <tr>
      <th>员工</th>
      <th class="num">提交数</th>
      <th class="num">已推送</th>
      <th class="num">未推送</th>
      <th class="num">推送率</th>
    </tr>
  </thead>
  <tbody id="stats-tbody">
    <tr class="stats-row-loading"><td colspan="5">加载中…</td></tr>
  </tbody>
  <tfoot id="stats-tfoot" hidden>
    <tr>
      <th>合计</th>
      <th class="num" id="stats-total-submitted">0</th>
      <th class="num" id="stats-total-pushed">0</th>
      <th class="num" id="stats-total-unpushed">0</th>
      <th class="num" id="stats-total-rate">—</th>
    </tr>
  </tfoot>
</table>

<link rel="stylesheet" href="/static/pushes_stats.css">
<script src="/static/pushes_stats.js"></script>
{% endblock %}
```

- [ ] **Step 4.2: 创建 `web/static/pushes_stats.css`**

```css
/* ===== Tabs（被 pushes_list / pushes_stats 共用） ===== */
.pushes-tabs {
  display: flex;
  gap: var(--space-2);
  border-bottom: 1px solid var(--border);
  margin: var(--space-4) 0 var(--space-6);
}
.pushes-tab {
  padding: var(--space-3) var(--space-4);
  color: var(--fg-muted);
  text-decoration: none;
  font-size: var(--text-base);
  border-bottom: 2px solid transparent;
  transition: color var(--duration-fast) var(--ease),
              border-color var(--duration-fast) var(--ease),
              background-color var(--duration-fast) var(--ease);
  border-radius: var(--radius) var(--radius) 0 0;
}
.pushes-tab:hover {
  color: var(--fg);
  background: var(--bg-muted);
}
.pushes-tab.is-active {
  color: var(--fg);
  border-bottom-color: var(--accent);
}

/* ===== Stats Toolbar ===== */
.stats-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-4);
  padding: var(--space-4);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-5);
}
.stats-filter-row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}
.stats-field {
  display: flex;
  flex-direction: column;
  font-size: var(--text-xs);
  color: var(--fg-muted);
  gap: var(--space-1);
}
.stats-field input[type="date"] {
  height: 32px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  padding: 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--fg);
  background: var(--bg);
}
.stats-field input[type="date"]:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-ring);
}
.stats-toolbar .range-sep {
  align-self: center;
  color: var(--fg-muted);
  padding: 0 var(--space-1);
}
.stats-quick-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.stats-quick-row button {
  height: 28px;
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  border: 1px solid var(--border-strong);
  background: var(--bg);
  color: var(--fg-muted);
  border-radius: var(--radius);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease),
              color var(--duration-fast) var(--ease);
}
.stats-quick-row button:hover {
  background: var(--bg-muted);
  color: var(--fg);
}
.stats-quick-row button.is-active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: var(--accent);
}
.stats-actions { display: flex; gap: var(--space-2); margin-left: auto; }
.stats-actions button {
  height: 32px;
  padding: 0 var(--space-4);
  font-size: var(--text-sm);
  border-radius: var(--radius);
  cursor: pointer;
}
.stats-actions button.primary {
  background: var(--accent);
  color: var(--accent-fg);
  border: 1px solid var(--accent);
}
.stats-actions button.primary:hover { background: var(--accent-hover); }
.stats-actions button:not(.primary) {
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border-strong);
}
.stats-actions button:not(.primary):hover { background: var(--bg-muted); }

/* ===== Stats Error ===== */
.stats-error {
  background: var(--danger-bg);
  color: var(--danger-fg);
  border: 1px solid var(--danger);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-4);
  font-size: var(--text-sm);
}

/* ===== Stats Table ===== */
.stats-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  font-size: var(--text-sm);
}
.stats-table th, .stats-table td {
  padding: var(--space-3) var(--space-4);
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.stats-table th { color: var(--fg-muted); font-weight: 500; }
.stats-table td { color: var(--fg); }
.stats-table th.num, .stats-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.stats-table tbody tr:last-child td { border-bottom: 0; }
.stats-table tfoot th {
  background: var(--bg-subtle);
  color: var(--fg);
  font-weight: 600;
}
.stats-row-loading td, .stats-row-empty td {
  text-align: center;
  color: var(--fg-muted);
  padding: var(--space-7) var(--space-4);
}
```

- [ ] **Step 4.3: 浏览器手动确认 `/pushes/stats` 渲染（无 JS 数据，但骨架 + 样式应已就位）**

启动 dev server（用户已经知道怎么启），访问 `http://172.30.254.14/pushes/stats`，截图确认：
- Tab 头：「推送管理」 + 「任务统计」（后者高亮）
- Toolbar：日期 + 6 个快捷按钮 + 筛选 + 重置
- 表格：「员工 / 提交数 / 已推送 / 未推送 / 推送率」表头，body 显示「加载中…」

> 视觉对齐 ocean-blue token 风格；hue ≤ 240。

- [ ] **Step 4.4: Commit**

```bash
git add web/templates/pushes_stats.html web/static/pushes_stats.css
git commit -m "feat(push-stats): scaffold stats page UI (toolbar + table skeleton)

Adds the static UI skeleton — date range, 6 quick buttons, table header,
loading row — before wiring up data fetching."
```

---

## Task 5: `pushes_stats.js` 完整逻辑

**Files:**
- Create: `web/static/pushes_stats.js`
- Test: `tests/test_pushes_stats.py`（追加 1 个 ui_assets 测试，确认 JS 文件被引用）

- [ ] **Step 5.1: 追加测试，确认 JS 文件可访问**

```python
# ============================================================
# UI assets：JS / CSS 文件存在且被引用
# ============================================================


def test_stats_page_references_js_and_css(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/stats")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "/static/pushes_stats.js" in text
    assert "/static/pushes_stats.css" in text


def test_stats_static_assets_available(authed_client_no_db):
    """Flask test client 应能拿到静态资源 200。"""
    js = authed_client_no_db.get("/static/pushes_stats.js")
    css = authed_client_no_db.get("/static/pushes_stats.css")
    assert js.status_code == 200
    assert css.status_code == 200
```

- [ ] **Step 5.2: 运行测试，确认 JS 静态资源测试 FAIL**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -k "references_js or static_assets" -q 2>&1 | tail -10`
Expected: `test_stats_static_assets_available` FAIL（404）；`test_stats_page_references_js_and_css` 应已 PASS（task 4 模板里已 link 了文件路径）。

- [ ] **Step 5.3: 创建 `web/static/pushes_stats.js`**

```javascript
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const fromInput = $('stats-from');
  const toInput = $('stats-to');
  const tbody = $('stats-tbody');
  const tfoot = $('stats-tfoot');
  const errorBox = $('stats-error');
  const applyBtn = $('stats-apply');
  const resetBtn = $('stats-reset');
  const quickButtons = document.querySelectorAll('.stats-quick-row button[data-range]');

  // ---------- 日期工具 ----------
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmt(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function todayDate() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }

  function startOfWeek(d) {
    // 中国习惯：周一为一周的第一天
    const r = new Date(d);
    const day = r.getDay(); // 0=Sun ... 6=Sat
    const offset = day === 0 ? 6 : day - 1;
    r.setDate(r.getDate() - offset);
    r.setHours(0, 0, 0, 0);
    return r;
  }
  function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function endOfMonth(d) { return new Date(d.getFullYear(), d.getMonth() + 1, 0); }

  function rangeFor(key) {
    const today = todayDate();
    switch (key) {
      case 'today':       return [today, today];
      case 'yesterday':   { const y = addDays(today, -1); return [y, y]; }
      case 'this-week':   return [startOfWeek(today), today];
      case 'last-week':   { const lwStart = addDays(startOfWeek(today), -7); return [lwStart, addDays(lwStart, 6)]; }
      case 'this-month':  return [startOfMonth(today), today];
      case 'last-month':  { const lm = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                            return [lm, endOfMonth(lm)]; }
      default:            return [startOfMonth(today), today];
    }
  }

  function setRange(key) {
    const [from, to] = rangeFor(key);
    fromInput.value = fmt(from);
    toInput.value = fmt(to);
    quickButtons.forEach((b) => {
      b.classList.toggle('is-active', b.dataset.range === key);
    });
  }

  // ---------- 渲染 ----------
  function clearTbody() { tbody.innerHTML = ''; }
  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }
  function clearError() {
    errorBox.textContent = '';
    errorBox.hidden = true;
  }
  function renderLoading() {
    clearError();
    tfoot.hidden = true;
    tbody.innerHTML = '<tr class="stats-row-loading"><td colspan="5">加载中…</td></tr>';
  }
  function renderEmpty() {
    tfoot.hidden = true;
    tbody.innerHTML = '<tr class="stats-row-empty"><td colspan="5">该区间内暂无提交记录，试试其他时间范围</td></tr>';
  }

  function fmtRate(rate) {
    if (rate === null || rate === undefined) return '—';
    return (rate * 100).toFixed(1) + '%';
  }

  function renderRows(rows) {
    clearTbody();
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(r.name)}</td>
        <td class="num">${r.submitted}</td>
        <td class="num">${r.pushed}</td>
        <td class="num">${r.unpushed}</td>
        <td class="num">${fmtRate(r.push_rate)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderTotals(t) {
    $('stats-total-submitted').textContent = t.submitted;
    $('stats-total-pushed').textContent = t.pushed;
    $('stats-total-unpushed').textContent = t.unpushed;
    $('stats-total-rate').textContent = fmtRate(t.push_rate);
    tfoot.hidden = false;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ---------- 拉数据 ----------
  async function fetchStats() {
    const df = (fromInput.value || '').trim();
    const dt = (toInput.value || '').trim();
    if (df && dt && df > dt) {
      showError('起始日期不能晚于截止日期');
      tbody.innerHTML = '';
      tfoot.hidden = true;
      return;
    }
    renderLoading();
    const params = new URLSearchParams();
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    const url = '/pushes/api/stats' + (params.toString() ? `?${params}` : '');
    try {
      const resp = await fetch(url, { credentials: 'same-origin' });
      if (!resp.ok) {
        let detail;
        try { detail = (await resp.json()).detail || resp.statusText; }
        catch (_) { detail = resp.statusText; }
        showError(`加载失败：${detail}`);
        clearTbody();
        return;
      }
      const data = await resp.json();
      if (!data.rows || data.rows.length === 0) {
        renderEmpty();
        return;
      }
      renderRows(data.rows);
      renderTotals(data.totals);
    } catch (err) {
      showError(`网络错误：${err && err.message ? err.message : err}`);
      clearTbody();
    }
  }

  // ---------- 事件绑定 ----------
  quickButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      setRange(btn.dataset.range);
      fetchStats();
    });
  });
  applyBtn.addEventListener('click', () => {
    quickButtons.forEach((b) => b.classList.remove('is-active'));
    fetchStats();
  });
  resetBtn.addEventListener('click', () => {
    setRange('this-month');
    fetchStats();
  });
  // URL query 优先；否则默认本月
  (function init() {
    const params = new URLSearchParams(location.search);
    const df = params.get('date_from');
    const dt = params.get('date_to');
    if (df && dt) {
      fromInput.value = df;
      toInput.value = dt;
    } else {
      setRange('this-month');
    }
    fetchStats();
  })();
})();
```

- [ ] **Step 5.4: 运行 ui_assets 测试，确认 JS 资源 200**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -k "references_js or static_assets" -q 2>&1 | tail -10`
Expected: `2 passed`。

- [ ] **Step 5.5: 跑全文件回归**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_stats.py -q 2>&1 | tail -10`
Expected: `17 passed`（task 1 的 6 + task 2 的 6 + task 3 的 3 + task 5 的 2）。

- [ ] **Step 5.6: Commit**

```bash
git add web/static/pushes_stats.js tests/test_pushes_stats.py
git commit -m "feat(push-stats): wire stats table data fetching and quick ranges

Implements front-end logic: 6 quick range buttons, manual date range,
loading/empty/error states, totals row, locale-aware HH:MM:SS-free
date formatting (YYYY-MM-DD)."
```

---

## Task 6: 端到端 smoke + 浏览器走查

**Files:** 无文件改动；纯验证步骤。如果发现问题，回到对应 task 修。

- [ ] **Step 6.1: 启动开发服务器（后台）**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m web.app` (run_in_background)

> 服务在 dev 端口启动；确认日志没有启动错误。

- [ ] **Step 6.2: 浏览器访问 `http://172.30.254.14/pushes/`，对照截图自检**

- [ ] Tab 头有「推送管理」 + 「任务统计」；前者高亮
- [ ] 「推送管理」原表格、状态、缩略图、操作列都正常
- [ ] 切到 `/pushes/stats`：tab 头切换，「任务统计」高亮
- [ ] 默认本月数据自动加载（看到至少一行或 empty 提示）
- [ ] 点击 6 个快捷按钮：日期框值变化 + 数据重拉
- [ ] 改日期框 + 点「筛选」：数据重拉
- [ ] 点「重置」：日期回到本月
- [ ] 输入起 > 止：表格清空 + inline error 显示
- [ ] 退出 admin、用普通用户登录，访问 `/pushes/`：tab 头不见「任务统计」
- [ ] 普通用户直接访问 `/pushes/stats` → 403

> 任何一项不过：定位对应 task 修；修完重跑该 task 的测试 + 这一节再走一遍。

- [ ] **Step 6.3: 关掉开发服务器**

Use the bash tool's `KillShell` (or kill the background process) — do not leave it running.

> 无 commit 步骤；如果 6.2 发现 bug 需要改代码，按"修一处 commit 一处"。

---

## Task 7: 全套测试 + Lint + 准备合并

**Files:** 无文件改动。

- [ ] **Step 7.1: 跑完整 push 相关测试**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest tests/test_pushes_routes.py tests/test_pushes_stats.py tests/test_pushes_shopify_image_readiness.py tests/test_pushes_ui_assets.py -q 2>&1 | tail -20`
Expected: 全部 passed，无新增 warning。

- [ ] **Step 7.2: 跑全套测试套件确保无外溢回归**

Run: `cd g:/Code/AutoVideoSrtLocal && python -m pytest -q 2>&1 | tail -30`
Expected: pass count 比基线只增不减（基线由 task 1 之前同样命令记录）。

> 若有偶发 flaky 失败，重跑一次；持续失败需 debug。

- [ ] **Step 7.3: 复核 git log 与变更范围**

Run: `git log --oneline master..HEAD && git diff --stat master..HEAD`
Expected：
- 5-7 个新 commit，全部带 `feat(push-stats):` / `feat(push-stats):` 前缀
- 改动文件清单完全等于本 plan 的 File Structure 部分
- 没有意外文件（比如 `pipeline/` 下被改了）

- [ ] **Step 7.4: 报告完成**

Plan 执行完毕，停在 worktree 分支 `feature/push-task-stats`。
向用户报告：「实现完成，所有测试通过；准备合并到 master 时告诉我。」
不自己 push、不自己 merge —— 这两个都是用户范围内的决定。

---

## Self-Review

**Spec coverage:**
- ✅ 路由表（`/pushes/` / `/pushes/stats` / `/pushes/api/stats`） → Task 2 + Task 3
- ✅ admin gate → Task 2 测试 + Task 3 partial 隐藏
- ✅ 聚合 SQL（含 owner_name_expr、半开区间、deleted_at 过滤、lang≠en） → Task 1
- ✅ 派生字段 unpushed / push_rate（分母 0 → null） → Task 1
- ✅ 合计行后端返回 → Task 1
- ✅ 字段映射 owner_name → name、Decimal → int → Task 1（int(...) cast）
- ✅ 时间归一化 + 默认本月 → Task 1
- ✅ Tab partial → Task 3
- ✅ 时间快捷按钮（6 个） → Task 4 模板 + Task 5 JS
- ✅ 三态（加载中 / 空 / 错误） → Task 5 JS
- ✅ ocean-blue token 样式 → Task 4 CSS
- ✅ 测试覆盖（aggregate 6 + 路由 6 + partial 3 + assets 2 = 17） → Task 1/2/3/5

**Placeholder scan:** 已扫一遍，无 TBD/TODO/「按需填」之类占位。

**Type consistency:**
- `aggregate_stats_by_owner` 返回 `{rows, totals, date_from, date_to}` → 路由原样转发 → 前端读 `data.rows / data.totals` 一致
- 字段命名 `submitted / pushed / unpushed / push_rate / name / user_id` 在测试、实现、JS 全链路一致
- partial active 值 `'list'` / `'stats'` 在视图、模板、测试三处一致

**Scope check:** 单一聚焦 `/pushes/stats` Tab，不外溢。
