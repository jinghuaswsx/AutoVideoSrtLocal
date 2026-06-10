# Mingkong 15d Local Pairing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only Mingkong SKU pairing run for Material Management SKU products created in the last 15 days, preserving configured local SKU rows and emitting a detailed report.

**Architecture:** Reuse the existing Mingkong workbench parsing and target-building code, but add a local-only batch path that never enters DXM03 confirmation, DXM03 replication, or Dianxiaomi Yuncang writes. Candidate products are selected by `media_products.created_at >= server_now - days`; per-product execution builds Shopify-variant baseline rows, protects already configured local rows, upserts only current `media_product_skus` fields for unconfigured variants, and writes report-only Mingkong metadata separately. A thin CLI wraps the batch runner and writes JSON reports under `output/mingkong_local_pairing_15d/`.

**Tech Stack:** Python 3.12, Flask app data helpers, SQLite-compatible SQL helpers, pytest, existing `tools/mingkong_unprocessed_sku_backfill.py`, existing `appcore.dianxiaomi_mingkong_pairing`.

---

## Document Anchor

- Spec: `docs/superpowers/specs/2026-06-10-mingkong-15d-local-pairing-report-design.md`
- Related implementation file: `tools/mingkong_unprocessed_sku_backfill.py`
- Existing target mapper: `appcore/dianxiaomi_mingkong_pairing.py::build_target_sku_import_pairs`
- Existing protective configured-row rules: `tools/mingkong_unprocessed_sku_backfill.py::is_configured_local_sku_row`

## File Structure

- Modify `tools/mingkong_unprocessed_sku_backfill.py`
  - Add `LOCAL_PAIRING_SOURCE = "mingkong_local_pairing_15d"`.
  - Add candidate selector for recent products without the old "no configured SKU rows" exclusion.
  - Add schema-safe local upsert helpers that skip protected variants and do not delete stale rows.
  - Add per-product local-only runner and batch/report aggregation.
- Create `tools/mingkong_local_pairing_15d.py`
  - CLI wrapper for dry-run plan and execute modes.
  - Writes JSON report path and summary to stdout.
- Create `tests/test_mingkong_local_pairing_15d.py`
  - Unit tests for candidate SQL, protected-row upsert behavior, local-only product runner, report aggregation, and CLI report writing.
- Modify `docs/superpowers/plans/2026-06-10-mingkong-15d-local-pairing.md`
  - Check off each task as it is completed.

## Task 1: Candidate Query Tests

**Files:**
- Create: `tests/test_mingkong_local_pairing_15d.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [ ] **Step 1: Write failing tests for recent candidate SQL**

Add these tests to `tests/test_mingkong_local_pairing_15d.py`:

```python
from datetime import datetime

from tools import mingkong_unprocessed_sku_backfill as mod


def test_list_recent_products_for_local_pairing_uses_created_at_cutoff_without_configured_exclusion():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "id": 11,
                "product_code": "P-11",
                "product_name": "Product 11",
                "product_link": "https://admin.shopify.com/store/x/products/11",
                "shopifyid": "11",
                "created_at": "2026-05-28 10:00:00",
            }
        ]

    rows = mod.list_recent_products_for_local_pairing(
        days=15,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 10, 12, 0, 0),
    )

    assert rows[0]["id"] == 11
    assert "mp.created_at >= ?" in captured["sql"]
    assert "NOT EXISTS" not in captured["sql"]
    assert captured["params"][0] == "2026-05-26 12:00:00"


def test_list_recent_products_for_local_pairing_can_include_archived_and_unlisted_products():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    mod.list_recent_products_for_local_pairing(
        days=15,
        include_archived=True,
        listed_only=False,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 10, 12, 0, 0),
    )

    assert "COALESCE(mp.archived, 0) = 0" not in captured["sql"]
    assert "COALESCE(mp.is_listed, 0) = 1" not in captured["sql"]
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py::test_list_recent_products_for_local_pairing_uses_created_at_cutoff_without_configured_exclusion tests/test_mingkong_local_pairing_15d.py::test_list_recent_products_for_local_pairing_can_include_archived_and_unlisted_products -q
```

Expected: both tests fail with `AttributeError: module ... has no attribute 'list_recent_products_for_local_pairing'`.

- [ ] **Step 3: Implement candidate selector**

Add this public helper to `tools/mingkong_unprocessed_sku_backfill.py` near `find_unprocessed_products`:

```python
LOCAL_PAIRING_SOURCE = "mingkong_local_pairing_15d"
LOCAL_PAIRING_OUTPUT_DIR = REPO_ROOT / "output" / "mingkong_local_pairing_15d"


def list_recent_products_for_local_pairing(
    *,
    days: int = 15,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    query_fn=db_query,
    now_fn=datetime.now,
) -> list[dict[str, Any]]:
    cutoff = now_fn() - timedelta(days=days)
    where = [
        "mp.deleted_at IS NULL",
        "mp.created_at >= ?",
        "(COALESCE(mp.product_code, '') <> '' OR COALESCE(mp.product_link, '') <> '' OR COALESCE(mp.shopifyid, '') <> '')",
    ]
    params: list[Any] = [cutoff.strftime("%Y-%m-%d %H:%M:%S")]
    if not include_archived:
        where.append("COALESCE(mp.archived, 0) = 0")
    if listed_only:
        where.append("COALESCE(mp.is_listed, 0) = 1")
    sql = f"""
        SELECT
            mp.id,
            mp.product_code,
            mp.product_name,
            mp.product_link,
            mp.shopifyid,
            mp.created_at
        FROM media_products mp
        WHERE {' AND '.join(where)}
        ORDER BY mp.created_at DESC, mp.id DESC
    """
    if limit and limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(row) for row in query_fn(sql, tuple(params))]
```

- [ ] **Step 4: Re-run the candidate tests**

Run the same command from Step 2.

Expected: both tests pass.

## Task 2: Protected Local Upsert Tests

**Files:**
- Modify: `tests/test_mingkong_local_pairing_15d.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [ ] **Step 1: Write failing test for local-only upsert**

Append:

```python
def test_upsert_local_pairing_pairs_skips_configured_variants_and_does_not_delete_stale_rows(monkeypatch):
    executed = []

    def fake_execute(sql, params=()):
        executed.append((sql, params))
        return 1

    pairs = [
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-protected",
            "shopify_sku": "BASE-A",
            "dianxiaomi_sku": "MK-A",
            "dianxiaomi_product_sku": "MK-P-A",
            "dianxiaomi_sku_code": "MK-C-A",
            "dianxiaomi_name": "Configured",
        },
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-open",
            "shopify_sku": "BASE-B",
            "dianxiaomi_sku": "MK-B",
            "dianxiaomi_product_sku": "MK-P-B",
            "dianxiaomi_sku_code": "MK-C-B",
            "dianxiaomi_name": "Open",
        },
        {
            "shopify_product_id": "shopify-1",
            "shopify_variant_id": "variant-new",
            "shopify_sku": "BASE-C",
            "dianxiaomi_sku": "",
            "dianxiaomi_product_sku": "",
            "dianxiaomi_sku_code": "",
            "dianxiaomi_name": "",
        },
    ]
    existing_rows = [
        {
            "id": 101,
            "shopify_variant_id": "variant-protected",
            "source": "manual",
            "dianxiaomi_sku": "KEEP",
        },
        {
            "id": 102,
            "shopify_variant_id": "variant-open",
            "source": "shopify_base",
            "dianxiaomi_sku": "",
        },
        {
            "id": 103,
            "shopify_variant_id": "variant-stale",
            "source": "shopify_base",
            "dianxiaomi_sku": "",
        },
    ]

    result = mod.upsert_local_pairing_pairs(
        product_id=1,
        pairs=pairs,
        existing_rows=existing_rows,
        protected_variant_ids={"variant-protected"},
        execute_fn=fake_execute,
    )

    assert result == {"updated": 1, "inserted": 1, "skipped_protected": 1}
    flattened = "\n".join(sql for sql, _params in executed)
    assert "DELETE FROM media_product_skus" not in flattened
    assert all("variant-protected" not in str(params) for _sql, params in executed)
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py::test_upsert_local_pairing_pairs_skips_configured_variants_and_does_not_delete_stale_rows -q
```

Expected: fail with missing `upsert_local_pairing_pairs`.

- [ ] **Step 3: Implement schema-safe upsert**

Add `LOCAL_PAIRING_SKU_FIELDS`, `_normalise_local_pairing_value`, and `upsert_local_pairing_pairs` to `tools/mingkong_unprocessed_sku_backfill.py`. The function updates rows whose `shopify_variant_id` exists in `existing_rows`, inserts rows that do not exist, skips `protected_variant_ids`, and never deletes rows.

- [ ] **Step 4: Re-run the upsert test**

Run the same command from Step 2.

Expected: pass.

## Task 3: Product Runner Tests

**Files:**
- Modify: `tests/test_mingkong_local_pairing_15d.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [ ] **Step 1: Write failing tests for dry-run and execute local-only product runner**

Append:

```python
def test_run_product_local_pairing_preserves_configured_rows_and_reports_partial(monkeypatch):
    product = {"id": 7, "product_code": "P7", "product_name": "P7", "product_link": "https://shopify/p/7"}
    existing_rows = [
        {"id": 1, "shopify_variant_id": "v1", "source": "manual", "dianxiaomi_sku": "KEEP"},
        {"id": 2, "shopify_variant_id": "v2", "source": "shopify_base", "dianxiaomi_sku": ""},
    ]

    monkeypatch.setattr(mod.medias, "list_product_skus", lambda product_id: existing_rows)
    monkeypatch.setattr(
        mod.pairing,
        "build_pairing_workbench_payload",
        lambda _product, include_mingkong_reference=True: {
            "items": [
                {"shopify_variant_id": "v1", "shopify_sku": "BASE-1"},
                {"shopify_variant_id": "v2", "shopify_sku": "BASE-2"},
                {"shopify_variant_id": "v3", "shopify_sku": "BASE-3"},
            ],
            "mingkong_procurement": {},
            "existing_sku_ids": {},
        },
    )
    monkeypatch.setattr(
        mod,
        "build_default_targets",
        lambda _payload: [
            {"shopify_variant_id": "v1", "dianxiaomi_sku": "MK-1"},
            {"shopify_variant_id": "v2", "dianxiaomi_sku": "MK-2"},
            {"shopify_variant_id": "v3", "dianxiaomi_sku": ""},
        ],
    )
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda _product, _items, _targets: [
            {"shopify_variant_id": "v1", "shopify_sku": "BASE-1", "dianxiaomi_sku": "MK-1"},
            {"shopify_variant_id": "v2", "shopify_sku": "BASE-2", "dianxiaomi_sku": "MK-2"},
            {"shopify_variant_id": "v3", "shopify_sku": "BASE-3", "dianxiaomi_sku": ""},
        ],
    )

    result = mod.run_product_local_pairing(product, execute=False)

    assert result["status"] == "partial"
    assert result["summary"]["synced_sku_count"] == 1
    assert result["summary"]["preserved_sku_count"] == 1
    assert result["summary"]["blank_base_sku_count"] == 1
    assert {row["action"] for row in result["sku_details"]} == {
        "preserved_existing_local_config",
        "synced_from_mingkong",
        "blank_base_no_mingkong_data",
    }


def test_run_product_local_pairing_execute_does_not_call_dxm03_or_yuncang(monkeypatch):
    calls = []
    product = {"id": 8, "product_code": "P8", "product_name": "P8", "product_link": "https://shopify/p/8"}

    monkeypatch.setattr(mod.medias, "list_product_skus", lambda product_id: [])
    monkeypatch.setattr(
        mod.pairing,
        "build_pairing_workbench_payload",
        lambda _product, include_mingkong_reference=True: {"items": [], "mingkong_procurement": {}, "existing_sku_ids": {}},
    )
    monkeypatch.setattr(mod, "build_default_targets", lambda _payload: [{"shopify_variant_id": "v1", "dianxiaomi_sku": "MK-1"}])
    monkeypatch.setattr(
        mod.pairing,
        "build_target_sku_import_pairs",
        lambda _product, _items, _targets: [{"shopify_variant_id": "v1", "shopify_sku": "BASE-1", "dianxiaomi_sku": "MK-1"}],
    )
    monkeypatch.setattr(
        mod,
        "upsert_local_pairing_pairs",
        lambda **kwargs: calls.append(kwargs) or {"updated": 0, "inserted": 1, "skipped_protected": 0},
    )
    monkeypatch.setattr(mod.pairing, "replicate_mingkong_skus_to_dxm03", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM03 replicate called")))
    monkeypatch.setattr(mod.pairing, "confirm_dxm03_pairing", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM03 confirm called")))
    monkeypatch.setattr(mod.dianxiaomi_yuncang, "add_product_skus_to_yuncang", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Yuncang called")))

    result = mod.run_product_local_pairing(product, execute=True)

    assert result["status"] == "completed"
    assert result["write_result"] == {"updated": 0, "inserted": 1, "skipped_protected": 0}
    assert len(calls) == 1
```

- [ ] **Step 2: Run product runner failing tests**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py::test_run_product_local_pairing_preserves_configured_rows_and_reports_partial tests/test_mingkong_local_pairing_15d.py::test_run_product_local_pairing_execute_does_not_call_dxm03_or_yuncang -q
```

Expected: fail with missing `run_product_local_pairing`.

- [ ] **Step 3: Implement product runner**

Add helpers:

```python
def _target_lookup_by_variant(targets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(target.get("shopify_variant_id") or ""): target for target in targets if target.get("shopify_variant_id")}


def _classify_local_pairing_pair(pair: dict[str, Any], protected_variant_ids: set[str]) -> tuple[str, str]:
    variant_id = str(pair.get("shopify_variant_id") or "")
    if variant_id in protected_variant_ids:
        return "preserved_existing_local_config", "本地已有配置，按规则保留不覆盖"
    if str(pair.get("dianxiaomi_sku") or "").strip():
        return "synced_from_mingkong", "明空配对数据确定，写入本地 SKU 配对字段"
    return "blank_base_no_mingkong_data", "Shopify variant 有基底，但明空无确定 SKU 数据，保留空白"
```

Implement `run_product_local_pairing(product, *, execute=False, force_refresh_mingkong=False) -> dict[str, Any]` by composing existing workbench and mapper helpers, then calling `upsert_local_pairing_pairs` only when `execute=True`.

- [ ] **Step 4: Re-run product runner tests**

Run the same command from Step 2.

Expected: pass.

## Task 4: Batch Aggregation and Report Writer

**Files:**
- Modify: `tests/test_mingkong_local_pairing_15d.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [ ] **Step 1: Write failing batch/report tests**

Append:

```python
def test_run_local_pairing_batch_aggregates_product_and_sku_counts(monkeypatch):
    products = [{"id": 1}, {"id": 2}, {"id": 3}]
    monkeypatch.setattr(mod, "list_recent_products_for_local_pairing", lambda **kwargs: products)
    results = [
        {"status": "completed", "summary": {"synced_sku_count": 2, "preserved_sku_count": 0, "blank_base_sku_count": 0}},
        {"status": "partial", "summary": {"synced_sku_count": 1, "preserved_sku_count": 1, "blank_base_sku_count": 1}},
        {"status": "suspended", "summary": {"synced_sku_count": 0, "preserved_sku_count": 0, "blank_base_sku_count": 2}},
    ]
    monkeypatch.setattr(mod, "run_product_local_pairing", lambda product, **kwargs: results.pop(0))

    report = mod.run_local_pairing_batch(days=15, execute=False)

    assert report["summary"]["candidate_product_count"] == 3
    assert report["summary"]["completed_product_count"] == 1
    assert report["summary"]["partial_product_count"] == 1
    assert report["summary"]["suspended_product_count"] == 1
    assert report["summary"]["synced_sku_count"] == 3
    assert report["summary"]["blank_base_sku_count"] == 3


def test_write_local_pairing_report_writes_json_file(tmp_path):
    report = {
        "mode": "plan",
        "summary": {"synced_sku_count": 1},
        "products": [],
    }

    path = mod.write_local_pairing_report(report, output_dir=tmp_path)

    assert path.exists()
    assert path.name.startswith("mingkong-local-pairing-15d-plan-")
    assert '"synced_sku_count": 1' in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run failing batch/report tests**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py::test_run_local_pairing_batch_aggregates_product_and_sku_counts tests/test_mingkong_local_pairing_15d.py::test_write_local_pairing_report_writes_json_file -q
```

Expected: fail with missing batch/report helpers.

- [ ] **Step 3: Implement batch and report writer**

Add `run_local_pairing_batch` and `write_local_pairing_report` to `tools/mingkong_unprocessed_sku_backfill.py`. The report must include `generated_at`, `mode`, `criteria`, `summary`, and `products`.

- [ ] **Step 4: Re-run batch/report tests**

Run the same command from Step 2.

Expected: pass.

## Task 5: CLI Wrapper

**Files:**
- Create: `tools/mingkong_local_pairing_15d.py`
- Modify: `tests/test_mingkong_local_pairing_15d.py`

- [ ] **Step 1: Write failing CLI test**

Append:

```python
def test_cli_main_runs_plan_mode_and_prints_report_path(monkeypatch, tmp_path, capsys):
    from tools import mingkong_local_pairing_15d as cli

    monkeypatch.setattr(
        cli.backfill,
        "run_local_pairing_batch",
        lambda **kwargs: {"mode": "plan", "summary": {"synced_sku_count": 1}, "products": []},
    )
    monkeypatch.setattr(cli.backfill, "write_local_pairing_report", lambda report: tmp_path / "report.json")

    rc = cli.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert "report.json" in out
    assert '"synced_sku_count": 1' in out
```

- [ ] **Step 2: Run the failing CLI test**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py::test_cli_main_runs_plan_mode_and_prints_report_path -q
```

Expected: fail with import error for `tools.mingkong_local_pairing_15d`.

- [ ] **Step 3: Implement CLI**

Create `tools/mingkong_local_pairing_15d.py` with `argparse` options:

```text
--days 15
--limit 0
--execute
--include-archived
--include-unlisted
--force-refresh-mingkong
--product-delay-seconds 0
```

Default mode is dry-run plan. `--execute` performs local writes only.

- [ ] **Step 4: Re-run CLI test**

Run the same command from Step 2.

Expected: pass.

## Task 6: Regression Tests and Verification

**Files:**
- Modify: only files already listed above.

- [ ] **Step 1: Run new focused test file**

Run:

```powershell
python -m pytest tests/test_mingkong_local_pairing_15d.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run existing Mingkong backfill tests**

Run:

```powershell
python -m pytest tests/test_mingkong_unprocessed_sku_backfill.py tests/test_mingkong_weekly_sync_orchestrator.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run related-test helper**

Run:

```powershell
python scripts/pytest_related.py --base origin/master --run
```

Expected: the helper runs the tests it maps from changed files, or reports no direct pytest coverage. Do not fall back to full pytest unless the helper reports a repository-level trigger.

- [ ] **Step 4: Run static diff check**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Commit implementation**

Run:

```powershell
git add docs/superpowers/plans/2026-06-10-mingkong-15d-local-pairing.md tests/test_mingkong_local_pairing_15d.py tools/mingkong_unprocessed_sku_backfill.py tools/mingkong_local_pairing_15d.py
git commit -m "feat: add mingkong 15d local sku pairing"
```

Commit message body must include:

```text
Docs-anchor: docs/superpowers/specs/2026-06-10-mingkong-15d-local-pairing-report-design.md
```

## Self-Review

- Spec coverage:
  - Recent 15-day products: Task 1 and Task 4.
  - Shopify SKU baseline: Task 3 through existing workbench payload and `build_target_sku_import_pairs`.
  - Preserve configured local rows: Task 2 and Task 3.
  - Sync high-confidence Mingkong data only: Task 3 reuses deterministic Mingkong target builder and reports blank rows where no SKU exists.
  - Mingkong-no-data remains blank: Task 2 and Task 3.
  - Detailed report counts: Task 4 and Task 5.
  - No DXM03 / no Dianxiaomi Yuncang: Task 3.
- Placeholder scan:
  - No step relies on unspecified paths or unnamed commands.
  - All new public function names are introduced before later tasks use them.
- Verification:
  - Focused pytest only, per project rule.
  - No local MySQL access.
  - No service restart.
