# Meta Hot Posts Visible Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible pagination controls above and below the Meta hot posts card grid.

**Architecture:** Keep pagination data owned by the existing `/xuanpin/api/meta-hot-posts` response. The Jinja template adds two pager containers and a shared JavaScript renderer so both controls stay synchronized after every list load.

**Tech Stack:** Python 3.12, Flask/Jinja templates, browser JavaScript, pytest.

---

### Task 1: Route Template Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_routes.py`
- Modify: `web/templates/meta_hot_posts.html`

- [x] **Step 1: Write the failing test**

Add these assertions to `test_meta_hot_posts_page_renders_tabs_and_api()`:

```python
assert 'id="mhPagerTop"' in body
assert 'id="mhPagerBottom"' in body
assert "function renderMetaHotPager(data)" in body
assert "首页" in body
assert "上一页" in body
assert "下一页" in body
assert "末页" in body
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api -q`

Expected: FAIL because the template has no visible pager containers or pager renderer.

- [x] **Step 3: Add pager markup and CSS**

In `web/templates/meta_hot_posts.html`, add this CSS near `.mh-status`:

```css
.mh-pager { display:flex; align-items:center; justify-content:center; gap:6px; flex-wrap:wrap; margin:10px 0 12px; color:var(--mh-muted); font-size:13px; }
.mh-pager button { height:32px; min-width:32px; padding:0 10px; border:1px solid var(--mh-strong); border-radius:6px; background:#fff; color:#334155; font-size:13px; font-weight:700; cursor:pointer; }
.mh-pager button.active { border-color:var(--mh-accent); background:var(--mh-accent); color:#fff; }
.mh-pager button:disabled { opacity:.45; cursor:not-allowed; }
.mh-pager-info { min-width:190px; text-align:center; }
```

Add these containers around the card grid:

```html
<div class="mh-pager" id="mhPagerTop" aria-label="Meta热帖分页"></div>

<div id="metaHotCardGrid" class="meta-hot-card-grid">
  <div class="mh-empty">加载中...</div>
</div>

<div class="mh-pager" id="mhPagerBottom" aria-label="Meta热帖分页"></div>
```

- [x] **Step 4: Add pager JavaScript**

Add a helper after `buildParams(page)`:

```javascript
function renderMetaHotPager(data) {
  const total = Number(data.total || 0);
  const pageSize = Math.max(1, Number(data.page_size || mhPageSize));
  const page = Math.max(1, Number(data.page || mhPage || 1));
  const totalPages = total > 0 ? Math.ceil(total / pageSize) : 0;
  const safeLastPage = Math.max(1, totalPages);
  const disabledPrev = totalPages <= 0 || page <= 1 ? ' disabled' : '';
  const disabledNext = totalPages <= 0 || page >= totalPages ? ' disabled' : '';
  const buttons = [
    `<button type="button"${disabledPrev} onclick="loadMetaHotPosts(1)">首页</button>`,
    `<button type="button"${disabledPrev} onclick="loadMetaHotPosts(${Math.max(1, page - 1)})">上一页</button>`,
  ];
  const start = Math.max(1, page - 2);
  const end = Math.min(safeLastPage, page + 2);
  for (let i = start; i <= end; i += 1) {
    buttons.push(`<button type="button" class="${i === page ? 'active' : ''}" onclick="loadMetaHotPosts(${i})">${i}</button>`);
  }
  buttons.push(
    `<button type="button"${disabledNext} onclick="loadMetaHotPosts(${Math.min(safeLastPage, page + 1)})">下一页</button>`,
    `<button type="button"${disabledNext} onclick="loadMetaHotPosts(${safeLastPage})">末页</button>`,
    `<span class="mh-pager-info">第 ${totalPages ? page : 0} / ${totalPages} 页 · 共 ${total} 条 · 每页 ${pageSize} 条</span>`
  );
  ['mhPagerTop', 'mhPagerBottom'].forEach(id => {
    qs(id).innerHTML = buttons.join('');
  });
}
```

Then in `loadMetaHotPosts(page)`, after setting card grid HTML, call:

```javascript
mhPage = Number(data.page || page || 1);
renderMetaHotPager(data);
```

- [x] **Step 5: Run the route test**

Run: `pytest tests/test_meta_hot_posts_routes.py -q`

Expected: PASS.

### Task 2: Final Verification

**Files:**
- Verify: `docs/superpowers/specs/2026-05-13-meta-hot-posts-visible-pagination-design.md`
- Verify: `tests/test_meta_hot_posts_routes.py`
- Verify: `web/templates/meta_hot_posts.html`

- [x] **Step 1: Run focused pytest**

Run: `pytest tests/test_meta_hot_posts_routes.py tests/test_meta_hot_posts_store.py tests/test_xuanpin_routes.py -q`

Expected: all tests pass.

- [x] **Step 2: Check whitespace**

Run: `git diff --check`

Expected: no output and exit code 0.
