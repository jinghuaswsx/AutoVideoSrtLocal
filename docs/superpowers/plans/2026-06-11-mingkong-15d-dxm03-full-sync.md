# Mingkong 15d DXM03 Full Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mistaken local-only 15-day Mingkong pairing path with a recent-product full-sync path that pushes deterministic Mingkong/DXM02 SKU data into our DXM03 account, confirms pairing, adds to Yuncang, refreshes purchase price, and reports every stage.

**Architecture:** Reuse `tools/mingkong_unprocessed_sku_backfill.py::run_product_sync` as the complete stage runner because it already orchestrates local SKU import, DXM03 replication, DXM03 pairing confirmation, Yuncang add, purchase-price refresh, and logistics/packaging handling. Add a recent-product selector and a full-sync batch/CLI wrapper that always enables configured-local-SKU protection, then remove the misleading local-only CLI by turning it into a deprecation stub. Keep implementation unit-testable by injecting fake stage functions in tests rather than driving Playwright.

**Tech Stack:** Python 3.12, existing Flask appcore helpers, `appcore.dianxiaomi_mingkong_pairing`, `appcore.dianxiaomi_yuncang`, pytest, JSON report files.

---

## Document Anchor

- Primary spec: `docs/superpowers/specs/2026-06-11-mingkong-15d-dxm03-full-sync-design.md`
- Deprecated spec: `docs/superpowers/specs/2026-06-10-mingkong-15d-local-pairing-report-design.md`
- Existing complete runner: `tools/mingkong_unprocessed_sku_backfill.py::run_product_sync`
- Existing DXM03 stages:
  - `appcore.dianxiaomi_mingkong_pairing.replicate_mingkong_skus_to_dxm03`
  - `appcore.dianxiaomi_mingkong_pairing.confirm_dxm03_pairing`
  - `appcore.dianxiaomi_yuncang.add_product_skus_to_yuncang`

## File Structure

- Modify `tools/mingkong_unprocessed_sku_backfill.py`
  - Add `FULL_SYNC_SOURCE = "mingkong_15d_dxm03_full_sync"` and `FULL_SYNC_OUTPUT_DIR`.
  - Add `list_recent_products_for_full_sync` using `media_products.created_at >= cutoff` with no configured-SKU exclusion.
  - Replace protect-mode local import inside `run_product_sync` with non-destructive upsert for unprotected variants.
  - Add `run_recent_15d_full_sync_batch`, `write_recent_full_sync_report`, and full-sync summary aggregation.
- Create `tools/mingkong_recent_15d_full_sync.py`
  - New CLI for plan/execute modes.
- Modify `tools/mingkong_local_pairing_15d.py`
  - Deprecation stub that exits nonzero and points to `tools/mingkong_recent_15d_full_sync.py`.
- Replace `tests/test_mingkong_local_pairing_15d.py`
  - Rename/replace with `tests/test_mingkong_recent_15d_full_sync.py`.
- Modify `tests/test_mingkong_unprocessed_sku_backfill.py`
  - Update protect-mode test to assert non-destructive local upsert and complete DXM03/Yuncang chain.

## Task 1: Recent Full-Sync Candidate Query

**Files:**
- Modify: `tests/test_mingkong_recent_15d_full_sync.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [x] **Step 1: Write failing selector tests**

Create `tests/test_mingkong_recent_15d_full_sync.py` with:

```python
from __future__ import annotations

import json
from datetime import datetime

from tools import mingkong_unprocessed_sku_backfill as mod


def test_list_recent_products_for_full_sync_uses_created_at_cutoff_without_configured_exclusion():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return [{"id": 11, "product_code": "P-11", "created_at": "2026-05-28 10:00:00"}]

    rows = mod.list_recent_products_for_full_sync(
        days=15,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 11, 12, 0, 0),
    )

    assert rows[0]["id"] == 11
    assert "mp.created_at >= %s" in captured["sql"]
    assert "NOT EXISTS" not in captured["sql"]
    assert captured["params"][0] == "2026-05-27 12:00:00"


def test_list_recent_products_for_full_sync_can_include_archived_and_unlisted_products():
    captured = {}

    def fake_query(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    mod.list_recent_products_for_full_sync(
        days=15,
        include_archived=True,
        listed_only=False,
        query_fn=fake_query,
        now_fn=lambda: datetime(2026, 6, 11, 12, 0, 0),
    )

    assert "COALESCE(mp.archived, 0)=0" not in captured["sql"]
    assert "mp.listing_status" not in captured["sql"]
```

- [x] **Step 2: Run selector tests and verify RED**

Run:

```powershell
python -m pytest tests/test_mingkong_recent_15d_full_sync.py::test_list_recent_products_for_full_sync_uses_created_at_cutoff_without_configured_exclusion tests/test_mingkong_recent_15d_full_sync.py::test_list_recent_products_for_full_sync_can_include_archived_and_unlisted_products -q
```

Expected: fail with `AttributeError` for `list_recent_products_for_full_sync`.

- [x] **Step 3: Implement selector**

Add `list_recent_products_for_full_sync` near the existing recent selector. It may reuse the same SQL shape as the old local selector, but its public name and report semantics must be full-sync.

- [x] **Step 4: Re-run selector tests**

Run the same command from Step 2.

Expected: pass.

## Task 2: Non-Destructive Protected Local Import

**Files:**
- Modify: `tests/test_mingkong_unprocessed_sku_backfill.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [x] **Step 1: Update failing protect-mode test**

Replace the existing `test_protective_sync_only_sends_newly_filled_rows_to_dxm03` expectations so it asserts:

```python
assert captured["upsert"]["source"] == mod.FULL_SYNC_SOURCE
assert captured["upsert"]["protected_variant_ids"] == {"variant-1"}
assert captured["replace_called"] is False
assert [row["shopify_variant_id"] for row in captured["replicate_rows"]] == ["variant-2"]
assert [row["shopify_variant_id"] for row in captured["confirm_rows"]] == ["variant-2"]
assert [row["shopify_variant_id"] for row in captured["yuncang_rows"]] == ["variant-2"]
```

The test should monkeypatch `mod.protective_upsert_product_skus` and make `mod.medias.replace_product_skus` fail if called while `protect_configured_local_skus=True`.

- [x] **Step 2: Run protect-mode test and verify RED**

Run:

```powershell
python -m pytest tests/test_mingkong_unprocessed_sku_backfill.py::test_protective_sync_only_sends_newly_filled_rows_to_dxm03 -q
```

Expected: fail because current `run_product_sync` still calls `medias.replace_product_skus` before the DXM03 stages.

- [x] **Step 3: Implement protective upsert**

Add:

```python
def protective_upsert_product_skus(
    *,
    product_id: int,
    pairs: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    protected_variant_ids: set[str],
    source: str,
    execute_fn: Callable[..., Any] = db_execute,
) -> dict[str, int]:
    ...
```

It updates/inserts only non-protected variants, never deletes stale rows, and returns `{"updated": n, "inserted": n, "skipped_protected": n}`.

Update `run_product_sync` so protect mode uses `protective_upsert_product_skus(... source=FULL_SYNC_SOURCE)` while legacy non-protect mode still uses `medias.replace_product_skus(... source="mingkong_batch_sync")`.

- [x] **Step 4: Re-run protect-mode test**

Run the same command from Step 2.

Expected: pass.

## Task 3: Full-Sync Batch Summary and Report

**Files:**
- Modify: `tests/test_mingkong_recent_15d_full_sync.py`
- Modify: `tools/mingkong_unprocessed_sku_backfill.py`

- [x] **Step 1: Write failing batch/report tests**

Append tests:

```python
def test_run_recent_15d_full_sync_batch_calls_complete_runner_with_protection(monkeypatch):
    products = [{"id": 1, "product_code": "P1"}, {"id": 2, "product_code": "P2"}]
    calls = []
    monkeypatch.setattr(mod, "list_recent_products_for_full_sync", lambda **kwargs: products)

    def fake_run_product_sync(product, **kwargs):
        calls.append((product, kwargs))
        return {
            "product_id": product["id"],
            "status": "ok",
            "new_fillable_sku_count": 1,
            "protected_local_sku_count": 1,
            "replicate": {"summary": {"created_count": 1, "existing_count": 0}},
            "confirm": {"summary": {"confirmed_count": 1}},
            "yuncang": {
                "summary": {"added_count": 1, "existing_count": 0},
                "purchase_price_status": "updated",
            },
            "skus": [{"logistics_packaging": {"status": "updated"}}],
        }

    monkeypatch.setattr(mod, "run_product_sync", fake_run_product_sync)

    report = mod.run_recent_15d_full_sync_batch(days=15, execute=True)

    assert report["mode"] == "execute"
    assert report["summary"]["candidate_product_count"] == 2
    assert report["summary"]["completed_product_count"] == 2
    assert report["summary"]["synced_sku_count"] == 2
    assert report["summary"]["protected_sku_count"] == 2
    assert report["summary"]["dxm03_replicated_sku_count"] == 2
    assert report["summary"]["yuncang_added_sku_count"] == 2
    assert report["summary"]["purchase_price_updated_product_count"] == 2
    assert all(kwargs["protect_configured_local_skus"] is True for _product, kwargs in calls)


def test_write_recent_full_sync_report_writes_json_file(tmp_path):
    report = {"mode": "plan", "summary": {"candidate_product_count": 1}, "products": []}

    path = mod.write_recent_full_sync_report(report, output_dir=tmp_path)

    assert path.exists()
    assert path.name.startswith("mingkong-recent-15d-full-sync-plan-")
    assert json.loads(path.read_text(encoding="utf-8"))["summary"]["candidate_product_count"] == 1
```

- [x] **Step 2: Run batch/report tests and verify RED**

Run:

```powershell
python -m pytest tests/test_mingkong_recent_15d_full_sync.py::test_run_recent_15d_full_sync_batch_calls_complete_runner_with_protection tests/test_mingkong_recent_15d_full_sync.py::test_write_recent_full_sync_report_writes_json_file -q
```

Expected: fail with missing full-sync batch/report helpers.

- [x] **Step 3: Implement batch and report writer**

Add `run_recent_15d_full_sync_batch`, `_empty_full_sync_summary`, `_accumulate_full_sync_result`, and `write_recent_full_sync_report`.

- [x] **Step 4: Re-run batch/report tests**

Run the same command from Step 2.

Expected: pass.

## Task 4: CLI Replacement and Deprecation Stub

**Files:**
- Create: `tools/mingkong_recent_15d_full_sync.py`
- Modify: `tools/mingkong_local_pairing_15d.py`
- Modify: `tests/test_mingkong_recent_15d_full_sync.py`

- [x] **Step 1: Write failing CLI tests**

Append tests:

```python
def test_full_sync_cli_runs_plan_mode_and_prints_report_path(monkeypatch, tmp_path, capsys):
    from tools import mingkong_recent_15d_full_sync as cli

    monkeypatch.setattr(
        cli.backfill,
        "run_recent_15d_full_sync_batch",
        lambda **kwargs: {"mode": "plan", "summary": {"candidate_product_count": 1}, "products": []},
    )
    monkeypatch.setattr(cli.backfill, "write_recent_full_sync_report", lambda report: tmp_path / "report.json")

    rc = cli.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert "report.json" in out
    assert '"candidate_product_count": 1' in out


def test_old_local_pairing_cli_is_deprecated(capsys):
    from tools import mingkong_local_pairing_15d as old_cli

    rc = old_cli.main([])

    out = capsys.readouterr().out
    assert rc == 2
    assert "mingkong_recent_15d_full_sync.py" in out
```

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```powershell
python -m pytest tests/test_mingkong_recent_15d_full_sync.py::test_full_sync_cli_runs_plan_mode_and_prints_report_path tests/test_mingkong_recent_15d_full_sync.py::test_old_local_pairing_cli_is_deprecated -q
```

Expected: fail because the new CLI does not exist and old CLI still runs local-only.

- [x] **Step 3: Implement new CLI and old deprecation stub**

Create `tools/mingkong_recent_15d_full_sync.py` with args:

```text
--days 15
--limit 0
--execute
--include-archived
--include-unlisted
--force-refresh-mingkong
--overwrite-existing-pairing
--product-delay-seconds 0
```

Modify `tools/mingkong_local_pairing_15d.py::main` to print a deprecation message and return 2.

- [x] **Step 4: Re-run CLI tests**

Run the same command from Step 2.

Expected: pass.

## Task 5: Remove Misleading Local-Only Tests

**Files:**
- Delete: `tests/test_mingkong_local_pairing_15d.py`
- Modify: `docs/superpowers/plans/2026-06-11-mingkong-15d-dxm03-full-sync.md`

- [x] **Step 1: Delete old local-only test file**

Delete `tests/test_mingkong_local_pairing_15d.py` after the new full-sync tests cover the selector, protective import, batch, report, and CLI behavior.

- [x] **Step 2: Run new full-sync tests**

Run:

```powershell
python -m pytest tests/test_mingkong_recent_15d_full_sync.py -q
```

Expected: pass.

## Task 6: Verification and Commit

**Files:**
- All modified files.

- [x] **Step 1: Run focused new tests**

Run:

```powershell
python -m pytest tests/test_mingkong_recent_15d_full_sync.py -q
```

Expected: pass.

- [x] **Step 2: Run existing Mingkong tests**

Run:

```powershell
python -m pytest tests/test_mingkong_unprocessed_sku_backfill.py tests/test_mingkong_pairing_workbench.py tests/test_mingkong_weekly_sync_orchestrator.py -q
```

Expected: pass.

- [x] **Step 3: Run related-test helper**

Run:

```powershell
python scripts/pytest_related.py --base origin/master --run
```

Expected: pass or report no direct pytest coverage. Do not run full `pytest -q` unless the helper reports a broad trigger.

- [x] **Step 4: Run static diff check**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [x] **Step 5: Commit implementation**

Run:

```powershell
git add docs/superpowers/plans/2026-06-11-mingkong-15d-dxm03-full-sync.md tests/test_mingkong_recent_15d_full_sync.py tests/test_mingkong_unprocessed_sku_backfill.py tools/mingkong_unprocessed_sku_backfill.py tools/mingkong_recent_15d_full_sync.py tools/mingkong_local_pairing_15d.py tests/test_mingkong_local_pairing_15d.py
git commit -m "feat: add mingkong 15d dxm03 full sync" -m "Docs-anchor: docs/superpowers/specs/2026-06-11-mingkong-15d-dxm03-full-sync-design.md"
```

## Self-Review

- Spec coverage:
  - 15-day recent product scope: Task 1 and Task 3.
  - Protect configured local SKU rows: Task 2.
  - Continue full DXM03/Yuncang flow for unconfigured variants: Task 2 and Task 3.
  - Procurement price and logistics packaging reporting: Task 3 aggregation from existing stage outputs.
  - CLI replacement and old local-only deprecation: Task 4.
  - Misleading test removal: Task 5.
- Placeholder scan:
  - No TBD/TODO placeholders.
  - Commands and expected outcomes are explicit.
- Risk notes:
  - No local MySQL.
  - No service restart.
  - Playwright/RPA stages are mocked in unit tests; live execution must happen only when the user asks to run data sync in a server/browser-authenticated environment.
