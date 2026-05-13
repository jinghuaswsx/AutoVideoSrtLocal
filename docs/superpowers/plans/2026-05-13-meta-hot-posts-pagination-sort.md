# Meta Hot Posts Pagination Sort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/xuanpin/meta-hot-posts` show 50 records per page and sort by interaction-change count descending.

**Architecture:** Keep pagination and ordering owned by the existing list API. The template sends `page_size=50`; `appcore.meta_hot_posts.store.list_hot_posts()` performs SQL ordering by `sync_period_likes` before `LIMIT/OFFSET`.

**Tech Stack:** Python 3.12, Flask, Jinja, pytest, MySQL-compatible SQL through `appcore.db`.

---

### Task 1: Store Sorting Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_store.py`
- Modify: `appcore/meta_hot_posts/store.py`

- [x] **Step 1: Write the failing test**

Add an assertion to `test_list_hot_posts_applies_category_price_interaction_comment_and_create_filters()`:

```python
assert "ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id DESC" in data_sql
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_store.py::test_list_hot_posts_applies_category_price_interaction_comment_and_create_filters -q`

Expected: FAIL because the SQL still orders by `p.latest_likes`.

- [x] **Step 3: Implement the minimal code**

In `appcore/meta_hot_posts/store.py`, change the list query order clause to:

```sql
ORDER BY COALESCE(p.sync_period_likes, 0) DESC, p.creation_time DESC, p.id DESC
```

- [x] **Step 4: Run the store test**

Run: `pytest tests/test_meta_hot_posts_store.py -q`

Expected: PASS.

### Task 2: Page Size Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_routes.py`
- Modify: `web/templates/meta_hot_posts.html`

- [x] **Step 1: Write the failing test**

Add this assertion in `test_meta_hot_posts_page_renders_tabs_and_api()`:

```python
assert "const mhPageSize = 50;" in body
```

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api -q`

Expected: FAIL because the template still uses `const mhPageSize = 30;`.

- [x] **Step 3: Implement the minimal code**

In `web/templates/meta_hot_posts.html`, change:

```javascript
const mhPageSize = 30;
```

to:

```javascript
const mhPageSize = 50;
```

- [x] **Step 4: Run the route test**

Run: `pytest tests/test_meta_hot_posts_routes.py -q`

Expected: PASS.

### Task 3: Final Verification

**Files:**
- Verify: `docs/superpowers/specs/2026-05-13-meta-hot-posts-pagination-sort-design.md`
- Verify: `tests/test_meta_hot_posts_store.py`
- Verify: `tests/test_meta_hot_posts_routes.py`

- [x] **Step 1: Run focused pytest**

Run: `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`

Expected: all tests pass.

- [x] **Step 2: Run route smoke checks if local app fixtures are available**

Use the existing Flask test clients to preserve the current auth checks already covered in `tests/test_meta_hot_posts_routes.py`.
