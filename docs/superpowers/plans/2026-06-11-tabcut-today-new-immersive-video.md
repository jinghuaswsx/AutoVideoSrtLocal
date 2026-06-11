# Tabcut Today New Immersive Video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Tabcut `今日新增` video sub Type and let both the default video list and today-new list open a Meta-style immersive mobile video overlay.

**Architecture:** Reuse the existing Tabcut video candidate query, hydration, routes, template, local-video endpoint, and card data cache. Add a narrow today-new query mode keyed by `tabcut_videos.first_seen_at`, then add a front-end view state that shares the video-card renderer and a Tabcut-specific overlay copied from the proven Meta hot-posts interaction pattern.

**Tech Stack:** Python 3.12, Flask, MySQL SQL through `appcore.db.query`, Jinja templates with inline JavaScript, pytest route/store tests.

---

## Source Anchors

- Spec: `docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md`
- Existing Tabcut API delegation: `web/routes/medias/tabcut_selection.py`, `web/routes/xuanpin.py`
- Existing Tabcut store/service: `appcore/tabcut_selection/store.py`, `appcore/tabcut_selection/service.py`
- Existing Tabcut UI: `web/templates/tabcut_selection.html`
- Existing Meta overlay model: `web/templates/meta_hot_posts.html`
- Verification rule: `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`

## File Structure

- Modify `appcore/tabcut_selection/store.py`
  - Add today-new filtering without duplicating the full video candidate SQL.
  - Include `v.first_seen_at` in video candidate rows so the overlay can show it.
- Modify `appcore/tabcut_selection/service.py`
  - Add `build_today_new_videos_response(args)` that hydrates the today-new store payload.
- Modify `web/routes/medias/tabcut_selection.py`
  - Add `/medias/api/tabcut-selection/today-new` for the medias API layer.
- Modify `web/routes/xuanpin.py`
  - Add `/xuanpin/api/tabcut/today-new` and delegate to the medias Tabcut route module.
- Modify `web/templates/tabcut_selection.html`
  - Add the `今日新增` sub Type entry.
  - Treat `today_new` as a video view for filtering, rendering, and pagination.
  - Add Tabcut immersive overlay CSS/JS and card entry button.
- Modify `tests/test_tabcut_selection_store.py`
  - Lock SQL semantics and service hydration entry point.
- Modify `tests/test_tabcut_selection_routes.py`
  - Lock medias-layer today-new API delegation and template strings.
- Modify `tests/test_xuanpin_routes.py`
  - Lock xuanpin API alias and page-level template contract.

## Task 1: Back-End Today-New Query

**Files:**
- Modify: `appcore/tabcut_selection/store.py`
- Modify: `appcore/tabcut_selection/service.py`
- Test: `tests/test_tabcut_selection_store.py`

- [ ] **Step 1: Write failing store tests**

Append these tests in `tests/test_tabcut_selection_store.py` after the existing video candidate filter tests:

```python
def test_list_today_new_video_candidates_filters_by_first_seen_and_orders_latest_first():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"cnt": 0}] if "COUNT" in sql else []

    payload = store.list_today_new_video_candidates(
        {"source_rank": "7d", "page": "2", "page_size": "20"},
        query_fn=fake_query,
    )

    count_sql, count_params = calls[0]
    data_sql, data_params = calls[-1]
    assert payload["page"] == 2
    assert payload["page_size"] == 20
    assert "v.first_seen_at >= CURDATE()" in count_sql
    assert "v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in count_sql
    assert "v.first_seen_at >= CURDATE()" in data_sql
    assert "v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in data_sql
    assert "v.first_seen_at" in data_sql
    assert (
        "ORDER BY v.first_seen_at DESC, "
        "COALESCE(vs.play_count, c.play_count, 0) DESC, "
        "c.score DESC, c.video_id ASC"
    ) in " ".join(data_sql.split())
    assert count_params[:3] == ["US", "video_7d_play", "video_7d_sales"]
    assert data_params[:5] == ["US", "video_7d_play", "video_7d_sales", 20, 20]


def test_build_today_new_videos_response_uses_today_new_store_payload(monkeypatch):
    seen = {}

    def fake_list(args):
        seen.update(args)
        return {
            "items": [
                {
                    "video_id": "v1",
                    "primary_item_id": "i1",
                    "primary_item_name": "Demo product",
                    "video_raw_json": "{}",
                    "first_seen_at": "2026-06-11 08:00:00",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 50,
        }

    monkeypatch.setattr(store, "list_today_new_video_candidates", fake_list)
    monkeypatch.setattr(service, "_tabcut_attach_fine_ai_evaluation", lambda items: None)

    result = service.build_today_new_videos_response({"q": "demo"})

    assert result.status_code == 200
    assert seen == {"q": "demo"}
    assert result.payload["total"] == 1
    assert result.payload["items"][0]["video_id"] == "v1"
    assert result.payload["items"][0]["first_seen_at"] == "2026-06-11 08:00:00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_tabcut_selection_store.py::test_list_today_new_video_candidates_filters_by_first_seen_and_orders_latest_first tests/test_tabcut_selection_store.py::test_build_today_new_videos_response_uses_today_new_store_payload -q
```

Expected: FAIL because `list_today_new_video_candidates` and `build_today_new_videos_response` do not exist yet.

- [ ] **Step 3: Add the store wrapper and query mode**

In `appcore/tabcut_selection/store.py`, change the function signature:

```python
def list_video_candidates(
    args: Mapping[str, Any],
    *,
    query_fn: QueryFn = query,
    today_new: bool = False,
) -> dict[str, Any]:
```

Inside `list_video_candidates`, after `params` is initialized, add:

```python
    if today_new:
        where.append("v.first_seen_at >= CURDATE()")
        where.append("v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)")
```

In the row SELECT list, after `v.video_duration_ms, v.create_time,` add:

```python
               v.first_seen_at,
```

Before the data query, define an order expression:

```python
    order_sql = (
        "v.first_seen_at DESC, COALESCE(vs.play_count, c.play_count, 0) DESC, c.score DESC, c.video_id ASC"
        if today_new
        else f"{sort_column} DESC, c.video_id ASC"
    )
```

Replace the existing row query `ORDER BY` line:

```python
        ORDER BY {sort_column} DESC, c.video_id ASC
```

with:

```python
        ORDER BY {order_sql}
```

After `list_video_candidates`, add:

```python
def list_today_new_video_candidates(args: Mapping[str, Any], *, query_fn: QueryFn = query) -> dict[str, Any]:
    return list_video_candidates(args, query_fn=query_fn, today_new=True)
```

- [ ] **Step 4: Add the service response builder**

In `appcore/tabcut_selection/service.py`, after `build_videos_response`, add:

```python
def build_today_new_videos_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(_hydrate_video_items(store.list_today_new_video_candidates(args)))
```

- [ ] **Step 5: Run store tests**

Run:

```bash
pytest tests/test_tabcut_selection_store.py::test_list_today_new_video_candidates_filters_by_first_seen_and_orders_latest_first tests/test_tabcut_selection_store.py::test_build_today_new_videos_response_uses_today_new_store_payload -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add appcore/tabcut_selection/store.py appcore/tabcut_selection/service.py tests/test_tabcut_selection_store.py
git commit -m "feat(tabcut): add today new video query" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md"
```

## Task 2: Today-New API Routes

**Files:**
- Modify: `web/routes/medias/tabcut_selection.py`
- Modify: `web/routes/medias/__init__.py`
- Modify: `web/routes/xuanpin.py`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing route tests**

In `tests/test_tabcut_selection_routes.py`, after `test_tabcut_selection_videos_api_delegates`, add:

```python
def test_tabcut_selection_today_new_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(args):
        captured.update(args)
        return TabcutResponse({"items": [{"video_id": "v1"}], "total": 1})

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_today_new_videos_response",
        fake_build,
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/today-new?q=demo")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]
    assert captured.get("q") == "demo"
```

In `tests/test_xuanpin_routes.py`, after `test_xuanpin_tabcut_api_alias_delegates`, add:

```python
def test_xuanpin_tabcut_today_new_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(args):
        captured.update(args)
        return TabcutResponse({"items": [{"video_id": "v2"}], "total": 1})

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_today_new_videos_response",
        fake_build,
    )

    resp = authed_client_no_db.get("/xuanpin/api/tabcut/today-new?source_rank=7d")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v2"}]
    assert captured.get("source_rank") == "7d"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_selection_today_new_api_delegates tests/test_xuanpin_routes.py::test_xuanpin_tabcut_today_new_api_alias_delegates -q
```

Expected: FAIL with 404 or missing route.

- [ ] **Step 3: Add medias route**

In `web/routes/medias/tabcut_selection.py`, after `api_tabcut_selection_videos`, add:

```python
@bp.route("/api/tabcut-selection/today-new", methods=["GET"])
@login_required
def api_tabcut_selection_today_new():
    if not _routes_module()._is_admin():
        return _json_response(service.build_admin_required_response())
    return _json_response(service.build_today_new_videos_response(request.args))
```

- [ ] **Step 4: Export medias route symbol**

In `web/routes/medias/__init__.py`, near the existing Tabcut exports, add:

```python
api_tabcut_selection_today_new = _tabcut_selection.api_tabcut_selection_today_new
```

- [ ] **Step 5: Add xuanpin alias**

In `web/routes/xuanpin.py`, after `api_tabcut_videos`, add:

```python
@bp.route("/api/tabcut/today-new", methods=["GET"])
@login_required
def api_tabcut_today_new():
    return _tabcut_routes().api_tabcut_selection_today_new()
```

- [ ] **Step 6: Run route tests**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_selection_today_new_api_delegates tests/test_xuanpin_routes.py::test_xuanpin_tabcut_today_new_api_alias_delegates -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add web/routes/medias/tabcut_selection.py web/routes/medias/__init__.py web/routes/xuanpin.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py
git commit -m "feat(tabcut): expose today new API" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md"
```

## Task 3: Tabcut Today-New Front-End View

**Files:**
- Modify: `web/templates/tabcut_selection.html`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing template tests**

In `tests/test_tabcut_selection_routes.py`, extend `test_tabcut_selection_page_renders_tabs` with:

```python
    assert "今日新增" in body
    assert "/xuanpin/api/tabcut/today-new" in body
    assert 'tabcutView === "today_new"' in body
    assert "今日暂无新抓到的视频" in body
```

In `tests/test_xuanpin_routes.py`, extend `test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api` with:

```python
    assert "/xuanpin/api/tabcut/today-new" in body
    assert "今日新增" in body
    assert 'tabcutView === "today_new"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_selection_page_renders_tabs tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: FAIL because the template does not expose today-new UI or API strings yet.

- [ ] **Step 3: Add the view tab**

In `web/templates/tabcut_selection.html`, add a `今日新增` link after the `视频榜` link in both share and non-share blocks:

```html
<a class="tabcut-view-tab {% if initial_view == 'today_new' %}active{% endif %}" href="{{ url_for('xuanpin.tabcut_share_videos_page') }}#today-new">今日新增</a>
```

and:

```html
<a class="tabcut-view-tab {% if initial_view == 'today_new' %}active{% endif %}" href="{{ url_for('xuanpin.tabcut_videos_page') }}#today-new">今日新增</a>
```

Then add `data-view` attributes to all four non-recommended view links so `setTabcutView()` can update active state without navigation:

```html
data-view="videos"
data-view="today_new"
data-view="goods"
data-view="recommended"
```

For the `今日新增` links, use:

```html
onclick="event.preventDefault(); setTabcutView('today_new')"
```

- [ ] **Step 4: Treat today-new as a video view in JavaScript**

Near the current `let tabcutView = "{{ initial_view or 'videos' }}";`, add:

```javascript
const tabcutVideoViews = new Set(["videos", "today_new"]);
function isTabcutVideoView() {
  return tabcutVideoViews.has(tabcutView);
}
```

Replace checks of:

```javascript
tabcutView === "videos"
```

with:

```javascript
isTabcutVideoView()
```

for video filters, endpoint selection, item caching, card rendering, zoom button visibility, and video price/sales parameter names.

Keep `tabcutView === "goods"` and `tabcutView === "recommended"` checks unchanged.

- [ ] **Step 5: Add endpoint and empty/status text**

In `loadTabcut(page)`, replace endpoint selection with:

```javascript
  const endpoint = tabcutView === "today_new"
    ? "{% if share_mode %}/xuanpin/api/tabcut/share/videos{% else %}/xuanpin/api/tabcut/today-new{% endif %}"
    : isTabcutVideoView()
      ? "{% if share_mode %}/xuanpin/api/tabcut/share/videos{% else %}/xuanpin/api/tabcut/videos{% endif %}"
      : "{% if share_mode %}/xuanpin/api/tabcut/share/goods{% else %}/xuanpin/api/tabcut/goods{% endif %}";
```

In `renderTabcut(data)`, set the status:

```javascript
  qs("tabcutStatus").textContent = tabcutView === "today_new"
    ? `今日新增 · ${fmtInt(data.total)} rows`
    : `${fmtInt(data.total)} rows`;
```

In the video grid empty state, use:

```javascript
    const emptyText = tabcutView === "today_new" ? "今日暂无新抓到的视频" : "No data";
    qs("tabcutVideoGrid").innerHTML = rows.length ? rows.map(renderVideoCard).join("") : `<div class='tabcut-empty'>${emptyText}</div>`;
```

- [ ] **Step 6: Initialize from hash**

Before the final `setTabcutView(tabcutView);`, add:

```javascript
if (window.location.hash === "#today-new") {
  tabcutView = "today_new";
}
```

- [ ] **Step 7: Run template tests**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_selection_page_renders_tabs tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add web/templates/tabcut_selection.html tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py
git commit -m "feat(tabcut): add today new video view" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md"
```

## Task 4: Tabcut Immersive Video Overlay

**Files:**
- Modify: `web/templates/tabcut_selection.html`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing overlay template tests**

In `tests/test_tabcut_selection_routes.py`, add a new test after the card layout tests:

```python
def test_tabcut_template_contains_immersive_video_overlay_controls():
    from pathlib import Path

    template = Path("web/templates/tabcut_selection.html").read_text(encoding="utf-8")

    assert "function openTabcutVideoOverlay(event, videoId)" in template
    assert "function switchTabcutVideoOverlay(direction)" in template
    assert "function handleTabcutVideoOverlayTouchStart(event)" in template
    assert "function handleTabcutVideoOverlayTouchEnd(event)" in template
    assert "function renderTabcutVideoOverlayInfo(item)" in template
    assert "function toggleTabcutVideoOverlayInfo(event)" in template
    assert "tabcut-video-overlay-download" in template
    assert "tabcutVideoOverlayState.infoExpanded" in template
    assert "scrollIntoView({behavior: 'smooth', block: 'center'})" in template
```

In `tests/test_xuanpin_routes.py`, extend `test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api` with:

```python
    assert "function openTabcutVideoOverlay(event, videoId)" in body
    assert "tabcut-video-overlay-download" in body
    assert "tabcutVideoOverlayState.infoExpanded" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_template_contains_immersive_video_overlay_controls tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: FAIL because overlay controls do not exist yet.

- [ ] **Step 3: Add overlay CSS**

In `web/templates/tabcut_selection.html` inside `extra_style`, add CSS near the existing video-card styles:

```css
body.tabcut-video-overlay-open { overflow:hidden; }
.tabcut-video-overlay[hidden] { display:none; }
.tabcut-video-overlay { position:fixed; inset:0; z-index:2400; display:flex; align-items:center; justify-content:center; padding:calc(env(safe-area-inset-top, 0px) + 12px) 12px calc(env(safe-area-inset-bottom, 0px) + 12px); background:rgba(15,23,42,.9); touch-action:pan-y; overscroll-behavior:contain; }
.tabcut-video-overlay-panel { position:relative; display:flex; align-items:center; justify-content:center; width:min(100%, 820px); height:100%; touch-action:pan-y; }
.tabcut-video-overlay-player { display:block; width:100%; max-width:100%; max-height:100%; border:0; border-radius:6px; background:#020617; object-fit:contain; }
.tabcut-video-overlay-toolbar { position:absolute; top:10px; left:10px; right:10px; z-index:2; display:flex; align-items:flex-start; justify-content:flex-end; gap:8px; pointer-events:none; }
.tabcut-video-overlay-info-slot { flex:1 1 auto; min-width:0; display:flex; justify-content:flex-start; pointer-events:auto; }
.tabcut-video-overlay-info { flex:0 1 min(620px, calc(100% - 104px)); min-width:0; padding:9px 10px; border:1px solid rgba(226,232,240,.35); border-radius:6px; background:rgba(15,23,42,.42); color:#fff; box-shadow:0 2px 12px rgba(15,23,42,.18); }
.tabcut-video-overlay-info-body { display:grid; grid-template-columns:minmax(0, 1fr); gap:8px; align-items:start; }
.tabcut-video-overlay-product-image { display:none; width:100px; height:100px; border-radius:6px; object-fit:cover; background:rgba(226,232,240,.24); }
.tabcut-video-overlay-title { overflow:hidden; color:#fff; font-size:13px; font-weight:800; line-height:1.45; text-overflow:ellipsis; white-space:nowrap; word-break:break-word; }
.tabcut-video-overlay-meta { display:flex; flex-wrap:wrap; gap:5px; margin-top:5px; color:rgba(226,232,240,.9); font-size:12px; font-weight:700; }
.tabcut-video-overlay-toggle { height:24px; margin-top:7px; padding:0 8px; border:1px solid rgba(226,232,240,.45); border-radius:5px; background:rgba(15,23,42,.24); color:#fff; font-size:12px; font-weight:800; cursor:pointer; }
.tabcut-video-overlay-info.is-expanded { max-height:min(44vh, 360px); overflow:auto; }
.tabcut-video-overlay-info.is-expanded.has-product-image .tabcut-video-overlay-info-body { grid-template-columns:auto minmax(0, 1fr); }
.tabcut-video-overlay-info.is-expanded .tabcut-video-overlay-product-image { display:block; }
.tabcut-video-overlay-info.is-expanded .tabcut-video-overlay-title { white-space:normal; overflow:visible; text-overflow:clip; }
.tabcut-video-overlay-actions { flex:0 0 auto; display:flex; align-items:center; gap:8px; pointer-events:auto; }
.tabcut-video-overlay-download,
.tabcut-video-overlay-close { display:inline-flex; align-items:center; justify-content:center; min-width:40px; height:40px; padding:0 12px; border:1px solid rgba(226,232,240,.45); border-radius:6px; background:rgba(15,23,42,.36); color:#fff; text-decoration:none; cursor:pointer; box-shadow:0 2px 12px rgba(15,23,42,.2); }
.tabcut-video-overlay-close { width:40px; padding:0; border-radius:999px; }
.tabcut-video-overlay-icon { width:18px; height:18px; stroke:currentColor; stroke-width:2; fill:none; stroke-linecap:round; stroke-linejoin:round; }
.tabcut-video-card-actions-overlay { position:absolute; top:8px; right:8px; z-index:4; display:flex; align-items:center; gap:6px; }
.tabcut-video-overlay-open-btn { display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; padding:0; border:1px solid rgba(226,232,240,.55); border-radius:6px; background:rgba(15,23,42,.58); color:#fff; cursor:pointer; box-shadow:0 2px 10px rgba(15,23,42,.22); }
@media (max-width: 768px) {
  .tabcut-video-overlay { align-items:stretch; padding:0; }
  .tabcut-video-overlay-panel { width:100vw; height:100dvh; }
  .tabcut-video-overlay-player { width:100vw; height:100dvh; max-height:none; border-radius:0; }
  .tabcut-video-overlay-toolbar { top:calc(env(safe-area-inset-top, 0px) + 10px); left:calc(env(safe-area-inset-left, 0px) + 10px); right:calc(env(safe-area-inset-right, 0px) + 10px); }
  .tabcut-video-overlay-info { flex-basis:min(100%, calc(100% - 96px)); padding:8px; }
  .tabcut-video-overlay-download span { display:none; }
}
```

- [ ] **Step 4: Add overlay state and helpers**

After `const tabcutItemsByVideoId = new Map();`, add:

```javascript
let tabcutVideoOverlayState = {videoId: "", touchStartX: null, touchStartY: null, infoExpanded: false, item: null, lastWheelAt: 0};
```

Near existing formatting helpers, add:

```javascript
function tabcutVideoIcon(name) {
  const icons = {
    expand: '<path d="M8 3H3v5"></path><path d="M3 3l6 6"></path><path d="M16 3h5v5"></path><path d="M21 3l-6 6"></path><path d="M8 21H3v-5"></path><path d="M3 21l6-6"></path><path d="M16 21h5v-5"></path><path d="M21 21l-6-6"></path>',
    download: '<path d="M12 3v12"></path><path d="M7 10l5 5 5-5"></path><path d="M5 21h14"></path>',
    close: '<path d="M18 6L6 18"></path><path d="M6 6l12 12"></path>',
  };
  return `<svg class="tabcut-video-overlay-icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.expand}</svg>`;
}

function tabcutLocalVideoUrl(videoId) {
  return `/xuanpin/api/tabcut/videos/${encodeURIComponent(videoId)}/local-video`;
}

function tabcutVideoDownloadName(row) {
  const rawId = String(row && row.video_id || "video").trim();
  const safeId = rawId.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "video";
  return `tabcut-${safeId}.mp4`;
}

function tabcutOverlaySummary(row) {
  if (!row) return [];
  const price = Number(row.primary_item_price_min || row.primary_item_price_max || 0);
  const symbol = row.currency_symbol || "$";
  return [
    price ? `${symbol}${price.toLocaleString(undefined, {maximumFractionDigits: 2})}` : "",
    row.primary_item_sold_count || row.goods_sold_count_7d ? `销量 ${fmtCompact(row.primary_item_sold_count || row.goods_sold_count_7d)}` : "",
    row.category_l1_name || "",
  ].filter(Boolean).slice(0, 3);
}

function tabcutVideoOverlayItemFromRow(row) {
  if (!row || !tabcutHasReadyLocalVideo(row)) return null;
  const videoId = String(row.video_id || "").trim();
  if (!videoId) return null;
  return {
    videoId,
    videoSrc: tabcutLocalVideoUrl(videoId),
    downloadName: tabcutVideoDownloadName(row),
    title: String(row.primary_item_name || row.video_desc || videoId).trim(),
    productImageUrl: String(row.primary_item_pic_url || "").trim(),
    summary: tabcutOverlaySummary(row),
    details: [
      row.primary_item_id ? `商品ID ${row.primary_item_id}` : "",
      row.play_count ? `播放 ${fmtCompact(row.play_count)}` : "",
      row.like_count ? `点赞 ${fmtCompact(row.like_count)}` : "",
      row.share_count ? `分享 ${fmtCompact(row.share_count)}` : "",
      row.comment_count ? `评论 ${fmtCompact(row.comment_count)}` : "",
      row.score ? `评分 ${Number(row.score).toLocaleString(undefined, {maximumFractionDigits: 1})}` : "",
      row.create_time ? `发布时间 ${formatDateTime(row.create_time)}` : "",
      row.first_seen_at ? `首次发现 ${formatDateTime(row.first_seen_at)}` : "",
    ].filter(Boolean),
  };
}
```

- [ ] **Step 5: Add overlay rendering and switching functions**

Near `openTiktokBrowserModal`, add:

```javascript
function ensureTabcutVideoOverlay() {
  let overlay = qs("tabcutVideoOverlay");
  if (overlay) return overlay;
  overlay = document.createElement("div");
  overlay.id = "tabcutVideoOverlay";
  overlay.className = "tabcut-video-overlay";
  overlay.hidden = true;
  overlay.addEventListener("click", handleTabcutVideoOverlayBackdropClick);
  overlay.addEventListener("touchstart", handleTabcutVideoOverlayTouchStart, {passive: true});
  overlay.addEventListener("touchend", handleTabcutVideoOverlayTouchEnd, {passive: true});
  overlay.addEventListener("wheel", handleTabcutVideoOverlayWheel, {passive: false});
  document.body.appendChild(overlay);
  return overlay;
}

function tabcutPlayableOverlayItems() {
  return Array.from(tabcutItemsByVideoId.values())
    .map(row => tabcutVideoOverlayItemFromRow(row))
    .filter(Boolean);
}

function tabcutAdjacentPlayableVideo(videoId, direction) {
  const items = tabcutPlayableOverlayItems();
  const currentIndex = items.findIndex(item => item.videoId === String(videoId || ""));
  if (currentIndex < 0) return null;
  const nextIndex = currentIndex + (direction > 0 ? 1 : -1);
  if (nextIndex < 0 || nextIndex >= items.length) return null;
  return items[nextIndex];
}

function renderTabcutVideoOverlayInfo(item) {
  if (!item) return "";
  const expanded = Boolean(tabcutVideoOverlayState.infoExpanded);
  const productImage = expanded && item.productImageUrl
    ? `<img class="tabcut-video-overlay-product-image" src="${esc(item.productImageUrl)}" alt="" loading="lazy">`
    : "";
  const meta = (expanded ? item.summary.concat(item.details) : item.summary)
    .map(text => `<span>${esc(text)}</span>`)
    .join("");
  return `<div class="tabcut-video-overlay-info${expanded ? " is-expanded" : ""}${item.productImageUrl ? " has-product-image" : ""}">
    <div class="tabcut-video-overlay-info-body">
      ${productImage}
      <div>
        <div class="tabcut-video-overlay-title">${esc(item.title || "Tabcut video")}</div>
        <div class="tabcut-video-overlay-meta">${meta}</div>
        <button class="tabcut-video-overlay-toggle" type="button" onclick="toggleTabcutVideoOverlayInfo(event)" aria-expanded="${expanded ? "true" : "false"}">${expanded ? "收起" : "展开"}</button>
      </div>
    </div>
  </div>`;
}

function updateTabcutVideoOverlayInfo() {
  const overlay = qs("tabcutVideoOverlay");
  const slot = overlay ? overlay.querySelector(".tabcut-video-overlay-info-slot") : null;
  if (!slot) return;
  slot.innerHTML = renderTabcutVideoOverlayInfo(tabcutVideoOverlayState.item);
}

function toggleTabcutVideoOverlayInfo(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  tabcutVideoOverlayState.infoExpanded = !tabcutVideoOverlayState.infoExpanded;
  updateTabcutVideoOverlayInfo();
}

function renderTabcutVideoOverlayPlayer(item, options = {}) {
  if (!item || !item.videoSrc) return;
  const overlay = ensureTabcutVideoOverlay();
  const activeVideo = overlay.querySelector("video");
  if (activeVideo) {
    activeVideo.pause();
    activeVideo.removeAttribute("src");
    activeVideo.load();
  }
  if (options.keepInfoExpanded !== true) tabcutVideoOverlayState.infoExpanded = false;
  tabcutVideoOverlayState.videoId = item.videoId;
  tabcutVideoOverlayState.item = item;
  overlay.dataset.currentVideoId = item.videoId;
  overlay.innerHTML = `<div class="tabcut-video-overlay-panel" role="dialog" aria-modal="true" aria-label="Tabcut沉浸播放">
    <div class="tabcut-video-overlay-toolbar">
      <div class="tabcut-video-overlay-info-slot">${renderTabcutVideoOverlayInfo(item)}</div>
      <div class="tabcut-video-overlay-actions">
        <a class="tabcut-video-overlay-download" href="${esc(item.videoSrc)}" download="${esc(item.downloadName)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" aria-label="下载视频" title="下载视频">${tabcutVideoIcon("download")}<span>下载</span></a>
        <button class="tabcut-video-overlay-close" type="button" onclick="closeTabcutVideoOverlay(event)" aria-label="关闭" title="关闭">${tabcutVideoIcon("close")}</button>
      </div>
    </div>
    <video class="tabcut-video-overlay-player" controls autoplay playsinline preload="metadata" src="${esc(item.videoSrc)}"></video>
  </div>`;
  overlay.hidden = false;
  document.body.classList.add("tabcut-video-overlay-open");
  const video = overlay.querySelector("video");
  if (video) video.play().catch(() => {});
}

function openTabcutVideoOverlay(event, videoId) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const row = tabcutItemsByVideoId.get(String(videoId || ""));
  const item = tabcutVideoOverlayItemFromRow(row);
  if (!item) return;
  closeTiktokBrowserModal();
  renderTabcutVideoOverlayPlayer(item);
}

function switchTabcutVideoOverlay(direction) {
  const overlay = qs("tabcutVideoOverlay");
  if (!overlay || overlay.hidden) return;
  const next = tabcutAdjacentPlayableVideo(tabcutVideoOverlayState.videoId, Number(direction || 0));
  if (!next) return;
  renderTabcutVideoOverlayPlayer(next, {keepInfoExpanded: true});
}

function handleTabcutVideoOverlayTouchStart(event) {
  const touch = event.touches && event.touches[0];
  if (!touch) return;
  tabcutVideoOverlayState.touchStartX = touch.clientX;
  tabcutVideoOverlayState.touchStartY = touch.clientY;
}

function handleTabcutVideoOverlayTouchEnd(event) {
  const touch = event.changedTouches && event.changedTouches[0];
  if (!touch || tabcutVideoOverlayState.touchStartY === null) return;
  const deltaX = touch.clientX - tabcutVideoOverlayState.touchStartX;
  const deltaY = touch.clientY - tabcutVideoOverlayState.touchStartY;
  tabcutVideoOverlayState.touchStartX = null;
  tabcutVideoOverlayState.touchStartY = null;
  if (Math.abs(deltaY) < 56 || Math.abs(deltaY) < Math.abs(deltaX) * 1.2) return;
  switchTabcutVideoOverlay(deltaY < 0 ? 1 : -1);
}

function handleTabcutVideoOverlayWheel(event) {
  const overlay = qs("tabcutVideoOverlay");
  if (!overlay || overlay.hidden) return;
  if (event.target && event.target.closest && event.target.closest(".tabcut-video-overlay-info")) return;
  const deltaY = Number(event.deltaY || 0);
  const deltaX = Number(event.deltaX || 0);
  if (Math.abs(deltaY) < 48 || Math.abs(deltaY) < Math.abs(deltaX) * 1.2) return;
  event.preventDefault();
  const now = Date.now();
  if (now - tabcutVideoOverlayState.lastWheelAt < 650) return;
  tabcutVideoOverlayState.lastWheelAt = now;
  switchTabcutVideoOverlay(deltaY > 0 ? 1 : -1);
}

function scrollTabcutCardIntoView(videoId) {
  const safeId = String(videoId || "");
  if (!safeId) return;
  const card = document.querySelector(`.tabcut-video-card[data-video-id="${CSS.escape(safeId)}"]`);
  if (card && card.scrollIntoView) card.scrollIntoView({behavior: 'smooth', block: 'center'});
}

function closeTabcutVideoOverlay(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const overlay = qs("tabcutVideoOverlay");
  if (!overlay) return;
  const currentVideoId = overlay.dataset.currentVideoId || tabcutVideoOverlayState.videoId || "";
  const video = overlay.querySelector("video");
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
  overlay.hidden = true;
  overlay.innerHTML = "";
  overlay.dataset.currentVideoId = "";
  tabcutVideoOverlayState = {videoId: "", touchStartX: null, touchStartY: null, infoExpanded: false, item: null, lastWheelAt: 0};
  document.body.classList.remove("tabcut-video-overlay-open");
  scrollTabcutCardIntoView(currentVideoId);
}

function handleTabcutVideoOverlayBackdropClick(event) {
  if (event.target === event.currentTarget) closeTabcutVideoOverlay(event);
}

function handleTabcutVideoOverlayKey(event) {
  if (event.key !== "Escape") return;
  const overlay = qs("tabcutVideoOverlay");
  if (!overlay || overlay.hidden) return;
  closeTabcutVideoOverlay(event);
}
document.addEventListener("keydown", handleTabcutVideoOverlayKey);
```

- [ ] **Step 6: Add card entry and card data attribute**

In `renderVideoCover(row)`, after `const playBtn = ...`, add:

```javascript
  const overlayBtn = tabcutHasReadyLocalVideo(row)
    ? `<span class="tabcut-video-card-actions-overlay"><button class="tabcut-video-overlay-open-btn" type="button" onclick="openTabcutVideoOverlay(event, '${esc(videoId)}')" aria-label="沉浸播放视频" title="沉浸播放视频">${tabcutVideoIcon("expand")}</button></span>`
    : "";
```

Inside `frame`, before `${inner}`, insert:

```javascript
      ${overlayBtn}
```

In `renderVideoCard(row)`, change:

```html
    <article class="tabcut-video-card">
```

to:

```html
    <article class="tabcut-video-card" data-video-id="${esc(row.video_id || "")}">
```

- [ ] **Step 7: Run overlay tests**

Run:

```bash
pytest tests/test_tabcut_selection_routes.py::test_tabcut_template_contains_immersive_video_overlay_controls tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add web/templates/tabcut_selection.html tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py
git commit -m "feat(tabcut): add immersive video overlay" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md"
```

## Task 5: Focused Verification

**Files:**
- No source changes expected.
- Verification only.

- [ ] **Step 1: Run related pytest selector**

Run:

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

Expected: PASS for selected related tests. If the selector reports no direct targets, run Step 2 and state that the selector had no target.

- [ ] **Step 2: Run fixed focused test set**

Run:

```bash
pytest tests/test_tabcut_selection_store.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: PASS.

- [ ] **Step 3: Compile touched Python packages**

Run:

```bash
python -m compileall appcore/tabcut_selection web/routes tests -q
```

Expected: exit code 0 with no output.

- [ ] **Step 4: Check whitespace and patch hygiene**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Optional manual smoke**

Run a local dev server on an unused port:

```bash
python -m web.app
```

Expected:

- Unauthenticated `GET /xuanpin/tabcut` returns 302.
- Authenticated admin opens `/xuanpin/tabcut`.
- `今日新增` tab appears.
- Browser network shows `/xuanpin/api/tabcut/today-new` after clicking `今日新增`.
- A card with local video ready opens the immersive overlay.
- Overlay download and close buttons are visible.

- [ ] **Step 6: Final commit if verification changed files**

If verification caused no file changes, do not commit. If a fix was needed, commit with:

```bash
git add <fixed-files>
git commit -m "fix(tabcut): stabilize immersive today new videos" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-today-new-immersive-video-design.md"
```

## Self-Review

- Spec coverage: Tasks 1-2 cover today-new data/API; Task 3 covers the Tabcut sub Type and shared video filtering/rendering; Task 4 covers immersive overlay, swipe switching, product info, download, close, and scroll-back; Task 5 covers focused verification.
- Placeholder scan: The plan contains no red-flag placeholder language or vague unexpanded test steps.
- Type consistency: The planned names are consistent across tasks: `list_today_new_video_candidates`, `build_today_new_videos_response`, `api_tabcut_selection_today_new`, `api_tabcut_today_new`, `today_new`, `tabcutVideoOverlayState`, and `openTabcutVideoOverlay`.
