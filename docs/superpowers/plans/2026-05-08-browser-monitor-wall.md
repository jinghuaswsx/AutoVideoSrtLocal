# Browser Monitor Wall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lab menu page named `浏览器监控` that displays DXM01-Meta, DXM02-MK, DXM03-RJC, TABCUT, and 采集程序 noVNC sessions in a 2x3 monitor wall.

**Architecture:** Add one focused Flask blueprint for browser monitoring, one template for the wall, and one sidebar link inside the existing lab group. The route uses existing `lab` permission and reads `scheduled_tasks.latest_run("cdp_environment_watchdog")` as best-effort status context without creating new timers or tables.

**Tech Stack:** Flask blueprint, Jinja templates, existing `appcore.scheduled_tasks`, pytest route/template tests.

---

## File Structure

- Create `web/routes/browser_monitor.py`: owns DXM environment definitions, noVNC URL generation, status extraction from watchdog summaries, and the `/browser-monitor` route.
- Create `web/templates/browser_monitor.html`: renders the 2x3 grid, five iframe windows, and status/action panel.
- Modify `web/app.py`: imports and registers the new blueprint.
- Modify `web/templates/layout.html`: adds `browser-monitor` to `lab_active` and inserts the `浏览器监控` menu item inside the lab group.
- Modify `docs/server_browser_runtime.md`: documents the new Web entry.
- Modify `tests/test_av_sync_menu_routes.py`: extends lab menu ordering and active-state coverage.
- Create `tests/test_browser_monitor_routes.py`: route-level coverage for page, iframe URLs, and watchdog summary rendering.

## Task 1: Route And Sidebar Tests

**Files:**
- Modify: `tests/test_av_sync_menu_routes.py`
- Create: `tests/test_browser_monitor_routes.py`

- [ ] **Step 1: Write failing browser monitor route tests**

Add `tests/test_browser_monitor_routes.py`:

```python
def test_browser_monitor_page_renders_three_vnc_iframes(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "浏览器监控" in html
    assert "DXM01-Meta" in html
    assert "DXM02-MK" in html
    assert "DXM03-RJC" in html
    assert 'src="http://172.16.254.106:6092/vnc.html?host=172.16.254.106&amp;port=6092&amp;autoconnect=true&amp;resize=remote"' in html
    assert 'src="http://172.16.254.106:6093/vnc.html?host=172.16.254.106&amp;port=6093&amp;autoconnect=true&amp;resize=remote"' in html
    assert 'src="http://172.16.254.106:6095/vnc.html?host=172.16.254.106&amp;port=6095&amp;autoconnect=true&amp;resize=remote"' in html


def test_browser_monitor_page_uses_watchdog_latest_summary(authed_client_no_db, monkeypatch):
    latest = {
        "status": "success",
        "started_at": "2026-05-08 12:00:00",
        "summary": {
            "environments": [
                {
                    "final": {
                        "code": "DXM01-Meta",
                        "ok": True,
                        "issues": [],
                    }
                },
                {
                    "final": {
                        "code": "DXM02-MK",
                        "ok": False,
                        "issues": [{"kind": "novnc", "message": "HTTP 500"}],
                    }
                },
            ]
        },
    }
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: latest)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "2026-05-08 12:00:00" in html
    assert "正常" in html
    assert "异常" in html
    assert "novnc: HTTP 500" in html
```

- [ ] **Step 2: Write failing sidebar tests**

Modify `tests/test_av_sync_menu_routes.py`:

```python
def test_dashboard_sidebar_lab_group_includes_browser_monitor():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")
    nav_html = template[template.index('<nav class="sidebar-nav">'):template.index("</nav>")]

    lab_group_idx = nav_html.index('<details class="sidebar-group sidebar-lab-group"')
    browser_monitor_idx = nav_html.index('href="{{ url_for(\\'browser_monitor.page\\') }}"')
    av_sync_idx = nav_html.index("url_for('projects.av_sync_page')")

    assert "浏览器监控" in nav_html
    assert lab_group_idx < browser_monitor_idx < av_sync_idx


def test_browser_monitor_menu_entry_is_active(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert re.search(r'<a href="/browser-monitor"[^>]*class="active"', html)
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
pytest tests/test_browser_monitor_routes.py tests/test_av_sync_menu_routes.py::test_dashboard_sidebar_lab_group_includes_browser_monitor tests/test_av_sync_menu_routes.py::test_browser_monitor_menu_entry_is_active -q
```

Expected: tests fail because `web.routes.browser_monitor` and `/browser-monitor` do not exist yet, and the sidebar link is absent.

## Task 2: Minimal Blueprint And Registration

**Files:**
- Create: `web/routes/browser_monitor.py`
- Modify: `web/app.py`

- [ ] **Step 1: Implement the route module**

Create `web/routes/browser_monitor.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from appcore import scheduled_tasks

bp = Blueprint("browser_monitor", __name__, url_prefix="/browser-monitor")

SERVER_HOST = "172.16.254.106"


@dataclass(frozen=True)
class BrowserEnvironment:
    code: str
    label: str
    port: int
    purpose: str

    @property
    def novnc_url(self) -> str:
        return (
            f"http://{SERVER_HOST}:{self.port}/vnc.html"
            f"?host={SERVER_HOST}&port={self.port}&autoconnect=true&resize=remote"
        )


ENVIRONMENTS: tuple[BrowserEnvironment, ...] = (
    BrowserEnvironment("DXM01-Meta", "DXM01-Meta", 6092, "Meta Ads Manager 导出"),
    BrowserEnvironment("DXM02-MK", "DXM02-MK", 6093, "明空选品店小秘"),
    BrowserEnvironment("DXM03-RJC", "DXM03-RJC", 6095, "荣锦成店小秘订单 / SKU / Shopify ID"),
)


def _has_lab_permission() -> bool:
    checker = getattr(current_user, "has_permission", None)
    return bool(callable(checker) and checker("lab"))


def _issue_text(issues: list[dict[str, Any]]) -> str:
    parts = []
    for issue in issues:
        kind = str(issue.get("kind") or "issue")
        message = str(issue.get("message") or "").strip()
        parts.append(f"{kind}: {message}" if message else kind)
    return "；".join(parts)


def _status_by_env(latest_run: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    statuses = {
        env.code: {"label": "未知", "class": "unknown", "detail": "暂无 watchdog 摘要"}
        for env in ENVIRONMENTS
    }
    summary = (latest_run or {}).get("summary") or {}
    for item in summary.get("environments") or []:
        final = item.get("final") or item.get("initial") or {}
        code = str(final.get("code") or "")
        if code not in statuses:
            continue
        issues = final.get("issues") or []
        if final.get("ok"):
            statuses[code] = {"label": "正常", "class": "ok", "detail": "systemd / CDP / noVNC 可访问"}
        else:
            statuses[code] = {
                "label": "异常",
                "class": "bad",
                "detail": _issue_text(issues) or "watchdog 报告异常",
            }
    return statuses


@bp.route("")
@login_required
def page():
    if not _has_lab_permission():
        abort(403)
    try:
        latest_run = scheduled_tasks.latest_run("cdp_environment_watchdog")
    except Exception:
        latest_run = None
    return render_template(
        "browser_monitor.html",
        environments=ENVIRONMENTS,
        status_by_env=_status_by_env(latest_run),
        latest_run=latest_run,
    )
```

- [ ] **Step 2: Register the blueprint**

Modify `web/app.py` imports and registration:

```python
from web.routes.browser_monitor import bp as browser_monitor_bp
```

Register near the other internal UI blueprints:

```python
app.register_blueprint(browser_monitor_bp)
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_browser_monitor_routes.py -q
```

Expected: tests now fail because `browser_monitor.html` does not exist.

## Task 3: Template And Sidebar

**Files:**
- Create: `web/templates/browser_monitor.html`
- Modify: `web/templates/layout.html`

- [ ] **Step 1: Create the monitor wall template**

Create `web/templates/browser_monitor.html` rendering:

- `extends "layout.html"`
- page title `浏览器监控`
- CSS grid `.browser-monitor-grid`
- three `.browser-monitor-frame-card` items with `<iframe src="{{ env.novnc_url }}">`
- one status card iterating over `environments`
- a `reloadBrowserWall()` function that reloads iframe `src`

- [ ] **Step 2: Add sidebar link**

Modify `web/templates/layout.html`:

- Add `or request.path.startswith('/browser-monitor')` to `lab_active`.
- Add this link inside `.sidebar-subnav`, after link check and before AV sync:

```jinja
<a href="{{ url_for('browser_monitor.page') }}" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/browser-monitor') %}class="active"{% endif %}>
  <span class="nav-icon">▦</span> 浏览器监控
</a>
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_browser_monitor_routes.py tests/test_av_sync_menu_routes.py::test_dashboard_sidebar_lab_group_includes_browser_monitor tests/test_av_sync_menu_routes.py::test_browser_monitor_menu_entry_is_active tests/test_av_sync_menu_routes.py::test_dashboard_sidebar_menu_links_open_new_tabs -q
```

Expected: all selected tests pass.

## Task 4: Runtime Documentation And Verification

**Files:**
- Modify: `docs/server_browser_runtime.md`

- [ ] **Step 1: Document the Web entry**

Add a short subsection under “远程查看浏览器”:

```markdown
3. **浏览器监控四宫格**：登录 Web 后打开 `/browser-monitor`，或从左侧“实验室”→“浏览器监控”进入。页面会同时加载 DXM01-Meta、DXM02-MK、DXM03-RJC 三个 noVNC 窗口，并在第四格展示 `cdp_environment_watchdog` 最近状态。
```

- [ ] **Step 2: Run full focused regression**

Run:

```bash
pytest tests/test_browser_monitor_routes.py tests/test_av_sync_menu_routes.py tests/test_cdp_environment_watchdog.py -q
python3 -m compileall web/routes/browser_monitor.py
```

Expected: pytest passes; compileall exits 0.

- [ ] **Step 3: Manual server check if a dev server is already running**

If the local/test Web service is available, open:

```text
http://172.16.254.106:8080/browser-monitor
```

Expected: after login, the page shows the four-grid layout and three noVNC frames.
