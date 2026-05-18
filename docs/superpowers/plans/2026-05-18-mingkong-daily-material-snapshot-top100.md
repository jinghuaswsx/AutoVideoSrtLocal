# Mingkong Daily Material Snapshot Top100 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local daily Mingkong material snapshot pipeline and show archived material cards plus yesterday-spend Top100 inside `/xuanpin/mk`.

**Architecture:** Add durable MySQL tables for daily material snapshots and Top100 archives, an `appcore.mingkong_materials` service for all selection/fetch/upsert/delta/listing logic, a `tools/mingkong_material_daily_snapshot.py` systemd runner, and thin Flask route aliases under `/xuanpin/api/*`. The existing `mk_selection.html` inner video tab switches from live Mingkong requests to local archived rows, while a new `昨天消耗前100` tab reads the stored daily Top100.

**Tech Stack:** Python 3.12, Flask, MySQL-compatible SQL migrations, pytest, existing `appcore.db`, existing Mingkong credential helpers in `appcore.pushes`, existing media path normalization/proxy helpers in `web.services.media_mk_selection`.

---

### Task 1: Schema Migration

**Files:**
- Create: `db/migrations/2026_05_18_mingkong_material_daily_snapshots.sql`
- Create: `tests/test_mingkong_materials_schema.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_mingkong_materials_schema.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mingkong_material_snapshot_migration_declares_tables_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_18_mingkong_material_daily_snapshots.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_material_sync_runs",
        "mingkong_material_products",
        "mingkong_material_daily_snapshots",
        "mingkong_material_daily_top100",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in body

    for key in [
        "uk_mk_material_run_snapshot",
        "uk_mk_material_run_product",
        "uk_mk_material_snapshot_material",
        "uk_mk_material_top100_material",
        "idx_mk_material_snapshot_spend",
        "idx_mk_material_top100_display",
    ]:
        assert key in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mingkong_materials_schema.py -q`

Expected: fail because the migration file does not exist.

- [ ] **Step 3: Add the migration**

Create `db/migrations/2026_05_18_mingkong_material_daily_snapshots.sql` with the four tables from the spec. Use `DECIMAL(14,2)` for spend fields, `JSON` for metadata/summary, `DATE` for snapshot dates, and explicit unique/index names listed in the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mingkong_materials_schema.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add db/migrations/2026_05_18_mingkong_material_daily_snapshots.sql tests/test_mingkong_materials_schema.py
git commit -m "feat(mingkong): add material snapshot schema"
```

### Task 2: Service Core For Source Products, Material Keys, Flattening, And Delta

**Files:**
- Create: `appcore/mingkong_materials.py`
- Create: `tests/test_mingkong_materials.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_mingkong_materials.py` covering:

```python
from datetime import date

import appcore.mingkong_materials as mm


def test_material_key_is_stable_and_path_specific():
    first = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    second = mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")
    other = mm.material_key_for("cool-widget", 901, "uploads2/b.mp4")

    assert first == second
    assert first != other
    assert len(first) == 64


def test_latest_top300_products_use_latest_dianxiaomi_snapshot(monkeypatch):
    calls = []

    monkeypatch.setattr(
        mm,
        "query_one",
        lambda sql, args=(): {"snapshot_date": date(2026, 5, 17)},
    )

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [{
            "rank_position": 1,
            "product_id": "gid-1",
            "product_name": "Cool Widget",
            "product_url": "https://shop.example/products/cool-widget-rjc",
            "store": "7662984",
            "sales_count": 9,
            "order_count": 8,
            "revenue_main": "123.45",
        }]

    monkeypatch.setattr(mm, "query", fake_query)

    snapshot, rows = mm.latest_top_products(limit=300)

    assert snapshot == "2026-05-17"
    assert rows[0]["product_code"] == "cool-widget"
    assert "ORDER BY rank_position ASC" in calls[0][0]
    assert calls[0][1] == ("2026-05-17", 300)


def test_flatten_mingkong_materials_keeps_all_visible_videos():
    product = {
        "id": 901,
        "product_name": "MK Cool",
        "product_links": ["https://shop.example/products/cool-widget-rjc"],
        "main_image": "uploads2/main.jpg",
        "videos": [
            {"name": "a.mp4", "path": "./medias/uploads2/a.mp4", "spends": "1.5万", "ads_count": 3},
            {"name": "hidden.mp4", "path": "uploads2/h.mp4", "hidden": True, "spends": "999"},
            {"name": "b.mp4", "path": "uploads2/b.mp4", "image_path": "uploads2/b.jpg", "spends": "20", "ads_count": 1},
        ],
    }

    rows = mm.flatten_materials_for_product(
        source_product={
            "product_code": "cool-widget",
            "rank_position": 1,
            "shopify_product_id": "gid-1",
            "product_name": "Cool Widget",
            "product_url": "https://shop.example/products/cool-widget-rjc",
        },
        mk_product=product,
    )

    assert [row["video_path"] for row in rows] == ["uploads2/a.mp4", "uploads2/b.mp4"]
    assert rows[0]["cumulative_90_spend"] == 15000.0
    assert rows[0]["material_key"] == mm.material_key_for("cool-widget", 901, "uploads2/a.mp4")


def test_build_top100_rows_marks_new_entry_and_clamps_negative_delta():
    current = [
        {"material_key": "fresh", "cumulative_90_spend": 500.0, "video_ads_count": 4, "rank_position": 1},
        {"material_key": "old", "cumulative_90_spend": 150.0, "video_ads_count": 2, "rank_position": 2},
        {"material_key": "reset", "cumulative_90_spend": 10.0, "video_ads_count": 9, "rank_position": 3},
    ]
    previous_by_key = {
        "old": {"cumulative_90_spend": 100.0},
        "reset": {"cumulative_90_spend": 30.0},
    }
    previous_top100_keys = {"old"}

    rows = mm.build_top100_rows(
        snapshot_date="2026-05-18",
        previous_snapshot_date="2026-05-17",
        current_rows=current,
        previous_by_key=previous_by_key,
        previous_top100_keys=previous_top100_keys,
        limit=100,
    )

    assert rows[0]["material_key"] == "fresh"
    assert rows[0]["yesterday_spend_delta"] == 500.0
    assert rows[0]["is_new_material"] is True
    assert rows[0]["is_new_top100_entry"] is True
    assert rows[1]["material_key"] == "old"
    assert rows[1]["yesterday_spend_delta"] == 50.0
    assert rows[1]["is_new_top100_entry"] is False
    assert rows[2]["material_key"] == "reset"
    assert rows[2]["yesterday_spend_delta"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mingkong_materials.py -q`

Expected: fail because `appcore.mingkong_materials` does not exist.

- [ ] **Step 3: Implement minimal service helpers**

Create `appcore/mingkong_materials.py` with:

- `guard_against_windows_local_mysql()`
- `_as_float()`, `_as_int()`, `_strip_rjc()`, `_product_handle()`
- `material_key_for(product_code, mk_product_id, video_path)`
- `latest_top_products(limit=300)`
- `flatten_materials_for_product(source_product, mk_product)`
- `build_top100_rows(...)`

Use existing `normalize_mk_media_path` from `web.services.media_mk_selection`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mingkong_materials.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add appcore/mingkong_materials.py tests/test_mingkong_materials.py
git commit -m "feat(mingkong): add material snapshot service core"
```

### Task 3: Persistence, Mingkong Fetch, And Daily Runner

**Files:**
- Modify: `appcore/mingkong_materials.py`
- Create: `tools/mingkong_material_daily_snapshot.py`
- Modify: `tests/test_mingkong_materials.py`
- Create: `tests/test_mingkong_material_daily_snapshot.py`

- [ ] **Step 1: Write failing persistence and runner tests**

Extend `tests/test_mingkong_materials.py` with tests that monkeypatch `execute`, `query`, `query_one`, and `get_conn` to prove:

- `upsert_snapshot_rows(run_id=..., snapshot_date=..., ranking_snapshot_date=..., rows=...)` inserts with `ON DUPLICATE KEY UPDATE`.
- `list_material_library()` serializes JSON metadata and orders by `cumulative_90_spend DESC`.
- `list_yesterday_top100()` orders by `is_new_top100_entry DESC, yesterday_spend_delta DESC`.

Create `tests/test_mingkong_material_daily_snapshot.py`:

```python
import tools.mingkong_material_daily_snapshot as runner


def test_arg_parser_defaults_to_top300_and_sleep_policy():
    args = runner.build_arg_parser().parse_args([])

    assert args.source_limit == 300
    assert args.batch_size == 10
    assert args.sleep_after_products == 2
    assert args.sleep_seconds == 30


def test_main_invokes_service_run(monkeypatch):
    called = {}

    def fake_run_daily_snapshot(**kwargs):
        called.update(kwargs)
        return {"processed_product_count": 3}

    monkeypatch.setattr(runner.mingkong_materials, "run_daily_snapshot", fake_run_daily_snapshot)

    assert runner.main(["--source-limit", "3", "--sleep-seconds", "0"]) == 0
    assert called["source_limit"] == 3
    assert called["sleep_seconds"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mingkong_materials.py tests/test_mingkong_material_daily_snapshot.py -q`

Expected: fail because persistence and runner functions are missing.

- [ ] **Step 3: Implement persistence and runner**

Add to `appcore/mingkong_materials.py`:

- `create_or_reuse_run(snapshot_date, ranking_snapshot_date, source_product_count, source_product_limit)`
- `record_product_status(...)`
- `upsert_snapshot_rows(...)`
- `generate_daily_top100(snapshot_date)`
- `list_material_library(snapshot_date=None, keyword="", page=1, page_size=100)`
- `list_yesterday_top100(snapshot_date=None, page=1, page_size=100)`
- `run_daily_snapshot(source_limit=300, batch_size=10, sleep_after_products=2, sleep_seconds=30, timeout_seconds=20)`

Create `tools/mingkong_material_daily_snapshot.py` as a thin CLI around `mingkong_materials.run_daily_snapshot`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mingkong_materials.py tests/test_mingkong_material_daily_snapshot.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add appcore/mingkong_materials.py tools/mingkong_material_daily_snapshot.py tests/test_mingkong_materials.py tests/test_mingkong_material_daily_snapshot.py
git commit -m "feat(mingkong): persist daily material snapshots"
```

### Task 4: Scheduled Task Registry And Systemd Units

**Files:**
- Modify: `appcore/scheduled_tasks.py`
- Create: `deploy/server_browser/autovideosrt-mingkong-material-daily-snapshot.service`
- Create: `deploy/server_browser/autovideosrt-mingkong-material-daily-snapshot.timer`
- Create: `deploy/server_browser/install_mingkong_material_daily_snapshot_timer.sh`
- Modify: `tests/test_appcore_scheduled_tasks.py`
- Create: `tests/test_mingkong_materials_scheduler.py`

- [ ] **Step 1: Write failing scheduler/unit tests**

Create `tests/test_mingkong_materials_scheduler.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mingkong_material_daily_snapshot_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_material_daily_snapshot")
    listed = {
        item["code"]: item for item in scheduled_tasks.task_definitions()
    }["mingkong_material_daily_snapshot"]

    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-mingkong-material-daily-snapshot.timer"
    assert task["runner"] == "tools/mingkong_material_daily_snapshot.py"
    assert "06:00" in task["schedule"]
    assert task["log_table"] == "scheduled_task_runs"
    assert listed["control_strategy"] == "systemd"
    assert listed["log_source"] == "db:scheduled_task_runs"
    assert listed["log_link_available"] is True


def test_mingkong_material_daily_snapshot_systemd_units():
    service = (
        ROOT
        / "deploy"
        / "server_browser"
        / "autovideosrt-mingkong-material-daily-snapshot.service"
    ).read_text(encoding="utf-8")
    timer = (
        ROOT
        / "deploy"
        / "server_browser"
        / "autovideosrt-mingkong-material-daily-snapshot.timer"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT
        / "deploy"
        / "server_browser"
        / "install_mingkong_material_daily_snapshot_timer.sh"
    ).read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/autovideosrt" in service
    assert "python tools/mingkong_material_daily_snapshot.py" in service
    assert "OnCalendar=*-*-* 06:00:00" in timer
    assert "Unit=autovideosrt-mingkong-material-daily-snapshot.service" in timer
    assert "systemctl enable --now autovideosrt-mingkong-material-daily-snapshot.timer" in installer
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mingkong_materials_scheduler.py -q`

Expected: fail because task definition and unit files are missing.

- [ ] **Step 3: Implement registry and units**

Add `mingkong_material_daily_snapshot` to `TASK_DEFINITIONS`. Add service/timer/installer files under `deploy/server_browser`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mingkong_materials_scheduler.py tests/test_appcore_scheduled_tasks.py::test_task_definitions_expose_control_and_log_metadata -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add appcore/scheduled_tasks.py deploy/server_browser/autovideosrt-mingkong-material-daily-snapshot.service deploy/server_browser/autovideosrt-mingkong-material-daily-snapshot.timer deploy/server_browser/install_mingkong_material_daily_snapshot_timer.sh tests/test_mingkong_materials_scheduler.py
git commit -m "feat(mingkong): register daily material snapshot timer"
```

### Task 5: Xuanpin Local Material APIs

**Files:**
- Modify: `web/routes/xuanpin.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing route tests**

Extend `tests/test_xuanpin_routes.py` with:

```python
def test_xuanpin_mk_material_library_api_reads_local_archive(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_material_library(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"video_name": "local.mp4"}],
            "snapshot": "2026-05-18",
            "total": 1,
            "run_summary": {"status": "success"},
        }

    monkeypatch.setattr(
        "appcore.mingkong_materials.list_material_library",
        fake_list_material_library,
    )

    resp = authed_client_no_db.get("/xuanpin/api/mk-material-library?keyword=tooth")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_name": "local.mp4"}]
    assert captured["keyword"] == "tooth"


def test_xuanpin_mk_yesterday_top100_api_reads_archive(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.mingkong_materials.list_yesterday_top100",
        lambda **kwargs: {
            "items": [{"video_name": "winner.mp4", "is_new_top100_entry": True}],
            "snapshot": "2026-05-18",
            "previous_snapshot": "2026-05-17",
            "total": 1,
            "run_summary": {"status": "success"},
        },
    )

    resp = authed_client_no_db.get("/xuanpin/api/mk-yesterday-top100")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["video_name"] == "winner.mp4"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_material_library_api_reads_local_archive tests/test_xuanpin_routes.py::test_xuanpin_mk_yesterday_top100_api_reads_archive -q`

Expected: fail with 404.

- [ ] **Step 3: Implement route aliases**

In `web/routes/xuanpin.py`, add `_mingkong_materials()` lazy import and two admin-only GET routes:

- `/api/mk-material-library`
- `/api/mk-yesterday-top100`

Parse `snapshot`, `keyword`, `page`, and `page_size` from `request.args`; return `jsonify(service_result)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_material_library_api_reads_local_archive tests/test_xuanpin_routes.py::test_xuanpin_mk_yesterday_top100_api_reads_archive -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add web/routes/xuanpin.py tests/test_xuanpin_routes.py
git commit -m "feat(xuanpin): add local mingkong material APIs"
```

### Task 6: MK Selection Template Local Card Tabs

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing template test**

Extend `test_xuanpin_mk_page_uses_xuanpin_tabs_and_api` in `tests/test_xuanpin_routes.py` with assertions:

```python
assert "昨天消耗前100" in body
assert "/xuanpin/api/mk-material-library" in body
assert "/xuanpin/api/mk-yesterday-top100" in body
assert "loadMkLocalMaterialLibrary" in body
assert "loadMkYesterdayTop100" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_page_uses_xuanpin_tabs_and_api -q`

Expected: fail because template still references only live `/xuanpin/api/mk-video-materials` for the video material tab.

- [ ] **Step 3: Update template**

In `web/templates/mk_selection.html`:

- Add a third inner button with `data-mk-library-tab="yesterday-top100"` and text `昨天消耗前100`.
- Keep `mkVideosPanel`, but make its loader call `/xuanpin/api/mk-material-library`.
- Add a new `mkYesterdayTop100Panel` with grid and pager.
- Reuse `renderMkVideoMaterialCard(r)` for both local library and Top100 rows.
- Ensure cards use archived fields: `video_image_path`, `video_path`, `video_spends` or `current_cumulative_90_spend`, `yesterday_spend_delta`, `is_new_top100_entry`.
- Keep `加入素材库` and `做小语种` data attributes from archived `mk_video_metadata`.

- [ ] **Step 4: Run route/template tests**

Run: `pytest tests/test_xuanpin_routes.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add web/templates/mk_selection.html tests/test_xuanpin_routes.py
git commit -m "feat(xuanpin): show local mingkong material cards"
```

### Task 7: Focused Verification

**Files:**
- No source edits unless verification exposes failures.

- [ ] **Step 1: Run focused unit and route tests**

Run:

```bash
pytest tests/test_mingkong_materials_schema.py tests/test_mingkong_materials.py tests/test_mingkong_material_daily_snapshot.py tests/test_mingkong_materials_scheduler.py tests/test_xuanpin_routes.py tests/test_media_mk_selection_service.py -q
```

Expected: pass.

- [ ] **Step 2: Run syntax check**

Run:

```bash
python -m compileall appcore tools web -q
```

Expected: exit code 0.

- [ ] **Step 3: Run whitespace diff check**

Run:

```bash
git diff --check
```

Expected: exit code 0.

- [ ] **Step 4: Report limitations**

Because the project forbids Windows local MySQL, do not run DB-backed migration smoke checks against `127.0.0.1:3306`. Report that DB migration/application verification must happen on the server/test environment.
