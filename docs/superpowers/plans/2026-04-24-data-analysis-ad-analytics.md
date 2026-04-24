# Data Analysis Ad Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the order analytics module to data analysis and add a long-lived Meta ad analytics import and reporting workflow.

**Architecture:** Extend the existing `order_analytics` route, DAO, and template instead of adding a new top-level module. Store Meta ad uploads as import batches plus period-level campaign metric rows, then join matched `product_id` rows to Shopify order aggregates for the selected report period.

**Tech Stack:** Flask, PyMySQL DAO helpers, MySQL migrations, Jinja templates, vanilla JavaScript, pytest.

---

## File Structure

- Create `db/migrations/2026_04_24_meta_ad_analytics_tables.sql` for `meta_ad_import_batches` and `meta_ad_campaign_metrics`.
- Modify `appcore/order_analytics.py` with Meta parsing, import, matching, stats, period, and summary functions.
- Modify `web/routes/order_analytics.py` with advertising upload, stats, periods, summary, and rematch endpoints.
- Modify `web/templates/order_analytics.html` to rename the module and add the advertising tab.
- Modify `web/templates/layout.html` to rename the sidebar item to “数据分析”.
- Create `tests/test_order_analytics_ads.py` for DAO-level behavior.
- Modify or add a route/template test for visible labels and the new API.

## Task 1: Database Schema

**Files:**
- Create: `db/migrations/2026_04_24_meta_ad_analytics_tables.sql`

- [ ] **Step 1: Add migration file**

```sql
CREATE TABLE IF NOT EXISTS meta_ad_import_batches (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_filename VARCHAR(255) NOT NULL,
  file_sha256 CHAR(64) NOT NULL,
  import_frequency VARCHAR(16) NOT NULL DEFAULT 'custom',
  report_start_date DATE DEFAULT NULL,
  report_end_date DATE DEFAULT NULL,
  raw_row_count INT NOT NULL DEFAULT 0,
  imported_rows INT NOT NULL DEFAULT 0,
  updated_rows INT NOT NULL DEFAULT 0,
  skipped_rows INT NOT NULL DEFAULT 0,
  matched_rows INT NOT NULL DEFAULT 0,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_file_sha256 (file_sha256),
  KEY idx_report_range (report_start_date, report_end_date),
  KEY idx_imported_at (imported_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Meta ad report upload batches';

CREATE TABLE IF NOT EXISTS meta_ad_campaign_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id BIGINT NOT NULL,
  report_start_date DATE NOT NULL,
  report_end_date DATE NOT NULL,
  import_frequency VARCHAR(16) NOT NULL DEFAULT 'custom',
  campaign_name VARCHAR(255) NOT NULL,
  normalized_campaign_code VARCHAR(255) NOT NULL,
  matched_product_code VARCHAR(128) DEFAULT NULL,
  product_id INT DEFAULT NULL,
  result_count INT NOT NULL DEFAULT 0,
  result_metric VARCHAR(128) DEFAULT NULL,
  spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_value_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  roas_purchase DECIMAL(12,6) DEFAULT NULL,
  cpm_usd DECIMAL(12,6) DEFAULT NULL,
  unique_link_click_cost_usd DECIMAL(12,6) DEFAULT NULL,
  link_ctr DECIMAL(12,6) DEFAULT NULL,
  campaign_delivery VARCHAR(32) DEFAULT NULL,
  link_clicks INT NOT NULL DEFAULT 0,
  add_to_cart_count INT NOT NULL DEFAULT 0,
  initiate_checkout_count INT NOT NULL DEFAULT 0,
  add_to_cart_cost_usd DECIMAL(12,6) DEFAULT NULL,
  initiate_checkout_cost_usd DECIMAL(12,6) DEFAULT NULL,
  cost_per_result_usd DECIMAL(12,6) DEFAULT NULL,
  average_purchase_value_usd DECIMAL(12,6) DEFAULT NULL,
  impressions INT NOT NULL DEFAULT 0,
  video_avg_play_time DECIMAL(12,6) DEFAULT NULL,
  raw_json JSON DEFAULT NULL,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_meta_ad_period_campaign (report_start_date, report_end_date, campaign_name),
  KEY idx_meta_ad_product_period (product_id, report_start_date, report_end_date),
  KEY idx_meta_ad_campaign_code (normalized_campaign_code(191)),
  KEY idx_meta_ad_batch (import_batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Meta ad period-level campaign metrics';
```

- [ ] **Step 2: Verify migration splits cleanly**

Run: `python -m pytest tests/test_db_migrations.py -q` if present, otherwise rely on application startup migration parsing.

Expected: migration parsing accepts two DDL statements.

## Task 2: DAO Tests For Meta Import

**Files:**
- Create: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Write failing parser and normalizer tests**

```python
import io

from appcore import order_analytics as oa


def test_parse_meta_ad_file_reads_required_fields():
    csv_text = (
        "报告开始日期,报告结束日期,广告系列名称,成效,成效指标,已花费金额 (USD),购物转化价值,"
        "广告花费回报 (ROAS) - 购物,CPM（千次展示费用） (USD),链接点击量,加入购物车次数,"
        "结账发起次数,展示次数\\n"
        "2026-04-01,2026-04-22,glow-go-insect-set-rjc,787,actions:offsite_conversion.fb_pixel_purchase,"
        "19377.19,34829.05,1.797425,34.111528,14109,1725,1338,568054\\n"
    )

    rows = oa.parse_meta_ad_file(io.BytesIO(csv_text.encode("utf-8")), "meta.csv")

    assert rows[0]["campaign_name"] == "glow-go-insect-set-rjc"
    assert rows[0]["report_start_date"].isoformat() == "2026-04-01"
    assert rows[0]["spend_usd"] == 19377.19
    assert rows[0]["link_clicks"] == 14109


def test_product_code_candidates_cover_rjc_suffix_variants():
    assert oa.product_code_candidates_for_ad_campaign("Glow-Go-Insect-Set") == [
        "glow-go-insect-set",
        "glow-go-insect-set-rjc",
    ]
    assert oa.product_code_candidates_for_ad_campaign("Glow-Go-Insect-Set-RJC") == [
        "glow-go-insect-set-rjc",
        "glow-go-insect-set",
    ]
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m pytest tests/test_order_analytics_ads.py -q`

Expected: fails because `parse_meta_ad_file` and `product_code_candidates_for_ad_campaign` do not exist.

## Task 3: DAO Implementation

**Files:**
- Modify: `appcore/order_analytics.py`
- Test: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Implement parsing helpers**

Add functions:

```python
def product_code_candidates_for_ad_campaign(campaign_name: str) -> list[str]:
    code = (campaign_name or "").strip().lower()
    if not code:
        return []
    candidates = [code]
    if code.endswith("-rjc"):
        candidates.append(code[:-4])
    else:
        candidates.append(f"{code}-rjc")
    return list(dict.fromkeys(candidates))


def parse_meta_ad_file(file_stream, filename: str) -> list[dict]:
    rows = parse_shopify_file(file_stream, filename)
    missing = [col for col in _META_AD_REQUIRED_COLS if col not in (rows[0].keys() if rows else [])]
    if missing:
        raise ValueError("Meta 广告报表缺少列：" + "、".join(missing))
    return [_normalize_meta_ad_row(row) for row in rows if (row.get("广告系列名称") or "").strip()]
```

- [ ] **Step 2: Run parser tests**

Run: `python -m pytest tests/test_order_analytics_ads.py -q`

Expected: parser and candidate tests pass.

- [ ] **Step 3: Add import and summary tests**

Extend `tests/test_order_analytics_ads.py` with monkeypatched `query`, `query_one`, `execute`, and `get_conn` tests for product matching candidates and period summary behavior.

- [ ] **Step 4: Implement import, stats, periods, rematch, and summary functions**

Add:

```python
def import_meta_ad_rows(rows: list[dict], filename: str, file_bytes: bytes, import_frequency: str) -> dict:
    ...

def match_meta_ads_to_products() -> int:
    ...

def get_meta_ad_stats() -> dict:
    ...

def get_meta_ad_periods() -> list[dict]:
    ...

def get_meta_ad_summary(batch_id: int | None = None, start_date: str | None = None, end_date: str | None = None) -> dict:
    ...
```

Expected behavior: upsert by report period plus campaign name, update duplicate period rows, and aggregate Shopify orders by matched `product_id` for the same period.

- [ ] **Step 5: Run DAO tests**

Run: `python -m pytest tests/test_order_analytics_ads.py -q`

Expected: all DAO tests pass.

## Task 4: Routes

**Files:**
- Modify: `web/routes/order_analytics.py`
- Test: route/template tests

- [ ] **Step 1: Write failing route tests**

Test that `/order-analytics` page contains “数据分析” and `data-tab="ads"`, and test that `/order-analytics/ad-upload` calls DAO import functions when a file is posted.

- [ ] **Step 2: Run route tests and confirm RED**

Run: `python -m pytest tests/test_order_analytics_ads.py tests/test_av_sync_menu_routes.py -q`

Expected: fails because routes and tab are missing.

- [ ] **Step 3: Add routes**

Add endpoints:

```python
@bp.route("/order-analytics/ad-upload", methods=["POST"])
@login_required
@admin_required
def ad_upload():
    ...

@bp.route("/order-analytics/ad-stats")
@login_required
@admin_required
def ad_stats():
    return jsonify(oa.get_meta_ad_stats())

@bp.route("/order-analytics/ad-periods")
@login_required
@admin_required
def ad_periods():
    return jsonify(oa.get_meta_ad_periods())

@bp.route("/order-analytics/ad-summary")
@login_required
@admin_required
def ad_summary():
    ...

@bp.route("/order-analytics/ad-match", methods=["POST"])
@login_required
@admin_required
def ad_match():
    return jsonify({"matched": oa.match_meta_ads_to_products()})
```

- [ ] **Step 4: Run route tests**

Run: `python -m pytest tests/test_order_analytics_ads.py tests/test_av_sync_menu_routes.py -q`

Expected: route tests pass after the template task is complete.

## Task 5: Template And Navigation

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `web/templates/layout.html`

- [ ] **Step 1: Rename labels**

Change page title and sidebar label from “订单分析” to “数据分析”. Change old tabs to “订单导入” and “订单分析”.

- [ ] **Step 2: Add advertising tab markup**

Add a third tab button with `data-tab="ads"` and a `panelAds` panel with upload, stats, period selector, summary table, and unmatched list containers.

- [ ] **Step 3: Add advertising JavaScript**

Add `doAdUpload`, `loadAdStats`, `loadAdPeriods`, `loadAdSummary`, `renderAdSummary`, and `matchAdProducts` functions using the new endpoints.

- [ ] **Step 4: Run template tests**

Run: `python -m pytest tests/test_order_analytics_ads.py tests/test_av_sync_menu_routes.py -q`

Expected: tests pass.

## Task 6: Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_order_analytics_ads.py tests/test_av_sync_menu_routes.py -q`

Expected: all pass.

- [ ] **Step 2: Run broader safety tests**

Run: `python -m pytest tests/test_appcore_medias_link_check_bootstrap.py tests/test_appcore_pushes.py tests/test_medias_mk_copywriting_routes.py -q`

Expected: all pass.

- [ ] **Step 3: Inspect git diff**

Run: `git diff --stat`

Expected: changes are limited to data analysis DAO, route, template, layout, migration, tests, and docs.
