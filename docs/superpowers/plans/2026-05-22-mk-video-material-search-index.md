# 明空视频素材库搜索索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/xuanpin/mk#videos` can quickly search local Mingkong video material cards by product name, product code, and video filename/path without breaking pagination.

**Architecture:** Keep the page on the existing local archive API, `GET /xuanpin/api/mk-material-library`. Centralize keyword condition construction in `appcore/mingkong_materials.py` so count and list queries share the same SQL predicate, then update the template state handling and add idempotent MySQL indexes for narrowed snapshot/date scans.

**Tech Stack:** Python 3.12, Flask routes, Jinja template inline JavaScript, MySQL migrations, pytest.

---

### Task 1: Backend Search Predicate

**Files:**
- Modify: `tests/test_mingkong_materials.py`
- Modify: `appcore/mingkong_materials.py`

- [ ] **Step 1: Write failing tests for product code variants and filename search**

Add tests near the existing `list_material_library` tests:

```python
def test_list_material_library_keyword_matches_product_code_rjc_variants(monkeypatch):
    captured = []
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {"snapshot_date": date(2026, 5, 22), "snapshot_at": datetime(2026, 5, 22, 5, 0, 0), "snapshot_slot": "0500"}
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {"status": "success", "snapshot_date": date(2026, 5, 22), "snapshot_at": datetime(2026, 5, 22, 5, 0, 0), "snapshot_slot": "0500", "summary_json": "{}"}
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return []

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    mm.list_material_library(keyword="cool-widget-rjc", page=1, page_size=100)

    count_args = next(args for kind, sql, args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    assert "%cool-widget%" in count_args
    assert "%cool-widget-rjc%" in count_args


def test_list_material_library_keyword_matches_video_filename_and_uses_same_filter(monkeypatch):
    captured = []
    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)

    def fake_query_one(sql, args=()):
        captured.append(("query_one", sql, args))
        if "FROM mingkong_material_daily_snapshots" in sql and "GROUP BY" in sql:
            return {"snapshot_date": date(2026, 5, 22), "snapshot_at": datetime(2026, 5, 22, 5, 0, 0), "snapshot_slot": "0500"}
        if "COUNT(*) AS cnt" in sql:
            return {"cnt": 1}
        if "mingkong_material_sync_runs" in sql:
            return {"status": "success", "snapshot_date": date(2026, 5, 22), "snapshot_at": datetime(2026, 5, 22, 5, 0, 0), "snapshot_slot": "0500", "summary_json": "{}"}
        raise AssertionError(sql)

    def fake_query(sql, args=()):
        captured.append(("query", sql, args))
        return []

    monkeypatch.setattr(mm, "query_one", fake_query_one)
    monkeypatch.setattr(mm, "query", fake_query)

    mm.list_material_library(keyword="family-memory-card-game.mp4", page=1, page_size=100)

    count_sql = next(sql for kind, sql, args in captured if kind == "query_one" and "COUNT(*) AS cnt" in sql)
    list_sql = next(sql for kind, sql, args in captured if kind == "query" and "ORDER BY s.cumulative_90_spend DESC" in sql)
    assert "s.video_name LIKE %s" in count_sql
    assert "s.video_path LIKE %s" in count_sql
    assert "s.video_name LIKE %s" in list_sql
    assert "s.video_path LIKE %s" in list_sql
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest tests/test_mingkong_materials.py::test_list_material_library_keyword_matches_product_code_rjc_variants tests/test_mingkong_materials.py::test_list_material_library_keyword_matches_video_filename_and_uses_same_filter -q
```

Expected: at least the product-code variant test fails because the existing predicate does not add the stripped/with-`-rjc` variants.

- [ ] **Step 3: Implement shared keyword predicate helper**

In `appcore/mingkong_materials.py`, add a helper near `_page_bounds()`:

```python
def _material_keyword_condition(alias: str, keyword: str) -> tuple[str, list[Any]]:
    kw = str(keyword or "").strip()
    if not kw:
        return "", []
    terms = [kw]
    lowered = kw.lower()
    stripped = _strip_rjc(lowered)
    if stripped and stripped not in {term.lower() for term in terms}:
        terms.append(stripped)
    with_rjc = f"{stripped}-rjc" if stripped else ""
    if with_rjc and with_rjc not in {term.lower() for term in terms}:
        terms.append(with_rjc)

    columns = [
        f"{alias}.product_code",
        f"{alias}.product_name",
        f"{alias}.mk_product_name",
        f"{alias}.video_name",
        f"{alias}.video_path",
    ]
    clauses = []
    args: list[Any] = []
    for column in columns:
        column_terms = terms if column.endswith(".product_code") else [kw]
        for term in column_terms:
            clauses.append(f"{column} LIKE %s")
            args.append(f"%{term}%")
    return "(" + " OR ".join(clauses) + ")", args
```

Replace both duplicated `if kw:` blocks in `list_material_library()` with:

```python
    keyword_sql, keyword_args = _material_keyword_condition("s", kw)
    if keyword_sql:
        where.append(keyword_sql)
        args.extend(keyword_args)
```

- [ ] **Step 4: Run backend tests and verify GREEN**

Run:

```bash
pytest tests/test_mingkong_materials.py::test_list_material_library_keyword_matches_product_code_rjc_variants tests/test_mingkong_materials.py::test_list_material_library_keyword_matches_video_filename_and_uses_same_filter -q
```

Expected: both pass.

### Task 2: Frontend Search State

**Files:**
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `web/templates/mk_selection.html`

- [ ] **Step 1: Write failing template tests**

Add tests near existing Mingkong material archive tests:

```python
def test_mk_selection_video_search_placeholder_lists_supported_fields():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert 'placeholder="搜索产品名 / product code / 视频文件名"' in template


def test_mk_selection_manual_video_search_clears_active_product_code_before_loading():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function runMkSearch(options = {})" in template
    assert "if (currentMkLibraryTab === 'videos' && options.preserveProductCode !== true) {" in template
    assert "activeMkProductCode = '';" in template
    assert "switchMkLibraryTab('videos', {preserveProductCode: true});" in template
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_video_search_placeholder_lists_supported_fields tests/test_mk_selection_routes.py::test_mk_selection_manual_video_search_clears_active_product_code_before_loading -q
```

Expected: both fail against the current placeholder and function signature.

- [ ] **Step 3: Implement template state change**

In `web/templates/mk_selection.html`:

```html
<input id="searchInput" class="oc-input" type="text" placeholder="搜索产品名 / product code / 视频文件名" style="width:240px">
```

Change `runMkSearch()` to:

```javascript
function runMkSearch(options = {}) {
  mkProductsLoaded = false;
  mkVideoMaterialsLoaded = false;
  mkYesterdayTop100Loaded = false;
  if (currentMkLibraryTab === 'videos' && options.preserveProductCode !== true) {
    activeMkProductCode = '';
  }
  if (currentMkLibraryTab === 'videos') {
    loadMkLocalMaterialLibrary(1);
  } else if (currentMkLibraryTab === 'yesterday-top100') {
    loadMkYesterdayTop100(1);
  } else {
    loadData(1);
  }
}
```

Change `switchMkLibraryTab()` so options are passed through:

```javascript
  if (currentMkLibraryTab === 'videos' && (!mkVideoMaterialsLoaded || options.forceLoad)) {
    loadMkLocalMaterialLibrary(1);
```

Leave the call as-is, and change `openProductMaterialLibrary()` to:

```javascript
  switchMkLibraryTab('videos', {preserveProductCode: true});
```

- [ ] **Step 4: Run template tests and verify GREEN**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_video_search_placeholder_lists_supported_fields tests/test_mk_selection_routes.py::test_mk_selection_manual_video_search_clears_active_product_code_before_loading -q
```

Expected: both pass.

### Task 3: Search Index Migration

**Files:**
- Create: `db/migrations/2026_05_22_mingkong_material_search_indexes.sql`
- Modify: `tests/test_mk_selection_routes.py`

- [ ] **Step 1: Write failing migration test**

Add:

```python
def test_mk_material_search_index_migration_is_idempotent():
    sql = Path("db/migrations/2026_05_22_mingkong_material_search_indexes.sql").read_text(encoding="utf-8")

    assert "idx_mk_material_search_at_product_code" in sql
    assert "idx_mk_material_search_at_video_name" in sql
    assert "idx_mk_material_search_at_product_name" in sql
    assert "idx_mk_material_search_at_mk_product_name" in sql
    assert "idx_mk_material_search_at_video_path" in sql
    assert "information_schema.STATISTICS" in sql
    assert "PREPARE stmt FROM @ddl" in sql
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_material_search_index_migration_is_idempotent -q
```

Expected: fails because the migration file does not exist.

- [ ] **Step 3: Add idempotent migration**

Create `db/migrations/2026_05_22_mingkong_material_search_indexes.sql` with repeated guarded DDL blocks:

```sql
SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_product_code'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_product_code (snapshot_at, product_code)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

Repeat for:

```text
idx_mk_material_search_at_video_name (snapshot_at, video_name)
idx_mk_material_search_at_product_name (snapshot_at, product_name)
idx_mk_material_search_at_mk_product_name (snapshot_at, mk_product_name)
idx_mk_material_search_at_video_path (snapshot_at, video_path(191))
idx_mk_material_search_date_product_code (snapshot_date, product_code)
idx_mk_material_search_date_video_name (snapshot_date, video_name)
```

- [ ] **Step 4: Run migration test and verify GREEN**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_material_search_index_migration_is_idempotent -q
```

Expected: pass.

### Task 4: Focused Verification

**Files:**
- Verify: `docs/superpowers/specs/2026-05-22-mk-video-material-search-index-design.md`
- Verify: `docs/superpowers/plans/2026-05-22-mk-video-material-search-index.md`
- Verify: changed source/test/migration files

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_mingkong_materials.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: pass.

- [ ] **Step 2: Run compile check**

Run:

```bash
python -m compileall appcore web tests -q
```

Expected: exit code 0.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Review status**

Run:

```bash
git status --short
```

Expected: only the intended spec, plan, migration, source, template, and tests are modified.
