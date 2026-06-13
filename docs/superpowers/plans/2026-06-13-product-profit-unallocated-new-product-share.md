# Product Profit Unallocated New Product Share Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 新品/非新品 labels and spend-share statistics to the `/product-profit` unallocated campaign view.

**Architecture:** Keep the source of truth in `appcore/order_analytics/product_profit_ads.py` by enriching `generate_unmatched_ads_report()` output. The frontend only renders fields already returned by the API, so the calculation is testable and reusable. No schema or allocation formula changes.

**Tech Stack:** Python 3.12, Flask routes already exposed through `/order-analytics/product-profit/ads.json`, Jinja template with vanilla JavaScript, pytest focused tests.

---

## File Map

- Modify `appcore/order_analytics/product_profit_ads.py`: import `product_ad_launch`, add helpers to enrich `unmatched[]` rows, and return `unallocated_launch_segment_summary`.
- Modify `web/templates/product_profit_dashboard.html`: add one summary strip, one table column, and JS rendering helpers.
- Modify `tests/test_product_profit_ads.py`: backend RED/GREEN tests for classification and spend-share summary.
- Modify `tests/test_product_profit_dashboard_assets.py`: static template tests for the new column and JS hooks.
- Keep `docs/superpowers/specs/2026-06-13-product-profit-unallocated-new-product-share-design.md` as the docs anchor.
- Add this plan in the implementation commit so the code commit contains a `.md` change.

## Task 1: Backend RED Tests

**Files:**
- Modify: `tests/test_product_profit_ads.py`
- Test: `tests/test_product_profit_ads.py`

- [ ] **Step 1: Add failing backend test for launch segment enrichment**

Append this test after `test_generate_unmatched_ads_report_aggregates_rows_counted_by_summary`:

```python
def test_generate_unmatched_ads_report_labels_new_and_non_new_unallocated_campaigns():
    fake_rows = [
        {
            "report_date": date(2026, 6, 12),
            "ad_account_id": "act_1",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "fresh-rjc",
            "campaign_name": "Fresh Campaign",
            "spend_usd": Decimal("70.00"),
            "result_count": 3,
            "purchase_value_usd": Decimal("140.00"),
            "allocation_reason": "matched_no_units",
            "matched_product_id": 101,
            "matched_product_code": "fresh-rjc",
            "matched_product_name": "Fresh Product",
        },
        {
            "report_date": date(2026, 6, 12),
            "ad_account_id": "act_2",
            "ad_account_name": "omurio",
            "normalized_campaign_code": "old-rjc",
            "campaign_name": "Old Campaign",
            "spend_usd": Decimal("20.00"),
            "result_count": 1,
            "purchase_value_usd": Decimal("30.00"),
            "allocation_reason": "matched_no_units",
            "matched_product_id": 202,
            "matched_product_code": "old-rjc",
            "matched_product_name": "Old Product",
        },
        {
            "report_date": date(2026, 6, 12),
            "ad_account_id": "act_3",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "mystery",
            "campaign_name": "Mystery Campaign",
            "spend_usd": Decimal("10.00"),
            "result_count": 0,
            "purchase_value_usd": Decimal("0.00"),
            "allocation_reason": "unmatched_product",
        },
    ]

    def fake_query(sql, params=()):
        if "FROM product_ad_launch_dates" in sql:
            return [
                {"product_id": 101, "ad_launch_date": date(2026, 6, 10)},
                {"product_id": 202, "ad_launch_date": date(2026, 5, 20)},
            ]
        raise AssertionError(f"unexpected query: {sql}")

    with patch("appcore.order_analytics._open_day_freshness.ensure_open_day_profit_lines_fresh"), \
         patch.object(ppa, "_load_unmatched_campaign_metrics", return_value=fake_rows), \
         patch.object(ppa.product_ad_launch, "beijing_today", return_value=date(2026, 6, 13)), \
         patch.object(ppa.product_ad_launch, "seed_missing_fallback_launch_dates", return_value=0), \
         patch.object(ppa, "query", side_effect=fake_query):
        result = ppa.generate_unmatched_ads_report(
            date_from=date(2026, 6, 12),
            date_to=date(2026, 6, 12),
        )

    by_code = {row["normalized_campaign_code"]: row for row in result["unmatched"]}
    assert by_code["fresh-rjc"]["launch_segment"] == "new_product"
    assert by_code["fresh-rjc"]["launch_segment_label"] == "新品"
    assert by_code["fresh-rjc"]["is_new_product"] is True
    assert by_code["fresh-rjc"]["ad_launch_date"] == "2026-06-10"

    assert by_code["old-rjc"]["launch_segment"] == "non_new_product"
    assert by_code["old-rjc"]["launch_segment_label"] == "非新品"
    assert by_code["old-rjc"]["is_new_product"] is False
    assert by_code["old-rjc"]["ad_launch_date"] == "2026-05-20"

    assert by_code["mystery"]["launch_segment"] == "non_new_product"
    assert by_code["mystery"]["is_new_product"] is False
    assert by_code["mystery"]["ad_launch_date"] is None

    summary = result["unallocated_launch_segment_summary"]
    assert summary["window_days"] == 7
    assert summary["total_spend_usd"] == 100.0
    assert summary["new_product"] == {
        "label": "新品",
        "spend_usd": 70.0,
        "share_pct": 70.0,
        "campaign_count": 1,
    }
    assert summary["non_new_product"] == {
        "label": "非新品",
        "spend_usd": 30.0,
        "share_pct": 30.0,
        "campaign_count": 2,
    }
```

- [ ] **Step 2: Run backend RED test**

Run:

```bash
pytest tests/test_product_profit_ads.py::test_generate_unmatched_ads_report_labels_new_and_non_new_unallocated_campaigns -q
```

Expected: FAIL because `product_profit_ads` has no `product_ad_launch` attribute or result rows do not include `launch_segment`.

## Task 2: Backend Implementation

**Files:**
- Modify: `appcore/order_analytics/product_profit_ads.py`
- Test: `tests/test_product_profit_ads.py`

- [ ] **Step 1: Import launch helper**

Near existing imports in `appcore/order_analytics/product_profit_ads.py`, add:

```python
from . import product_ad_launch
```

- [ ] **Step 2: Add constants and helpers near `_allocation_reason_label`**

Add:

```python
_LAUNCH_SEGMENT_NEW = "new_product"
_LAUNCH_SEGMENT_NON_NEW = "non_new_product"


def _empty_launch_segment_summary(window_days: int) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "total_spend_usd": 0.0,
        _LAUNCH_SEGMENT_NEW: {
            "label": "新品",
            "spend_usd": 0.0,
            "share_pct": 0.0,
            "campaign_count": 0,
        },
        _LAUNCH_SEGMENT_NON_NEW: {
            "label": "非新品",
            "spend_usd": 0.0,
            "share_pct": 0.0,
            "campaign_count": 0,
        },
    }


def _load_product_launch_dates(product_ids: set[int]) -> dict[int, date]:
    if not product_ids:
        return {}
    product_ad_launch.seed_missing_fallback_launch_dates()
    product_list = sorted(product_ids)
    rows = query(
        f"SELECT product_id, ad_launch_date FROM product_ad_launch_dates "
        f"WHERE product_id IN ({_sql_in(product_list)})",
        tuple(product_list),
    ) or []
    out: dict[int, date] = {}
    for row in rows:
        try:
            product_id = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        launch_date = _date_value(row.get("ad_launch_date"))
        if product_id > 0 and launch_date:
            out[product_id] = launch_date
    return out


def _attach_launch_segments_to_unmatched(
    unmatched_rows: list[dict[str, Any]],
    *,
    window_days: int | None = None,
) -> dict[str, Any]:
    normalized_window_days = product_ad_launch.normalize_product_launch_window_days(window_days)
    summary = _empty_launch_segment_summary(normalized_window_days)
    product_ids = {
        int(row["matched_product_id"])
        for row in unmatched_rows
        if row.get("matched_product_id") is not None
    }
    launch_dates = _load_product_launch_dates(product_ids)
    today = product_ad_launch.beijing_today()

    for row in unmatched_rows:
        product_id = row.get("matched_product_id")
        launch_date = None
        if product_id is not None:
            try:
                launch_date = launch_dates.get(int(product_id))
            except (TypeError, ValueError):
                launch_date = None

        is_new = False
        if launch_date is not None:
            is_new = (
                product_ad_launch.classify_launch_date(
                    launch_date,
                    today=today,
                    window_days=normalized_window_days,
                )
                == "new"
            )

        segment = _LAUNCH_SEGMENT_NEW if is_new else _LAUNCH_SEGMENT_NON_NEW
        row["launch_segment"] = segment
        row["launch_segment_label"] = "新品" if is_new else "非新品"
        row["is_new_product"] = bool(is_new)
        row["ad_launch_date"] = launch_date.isoformat() if launch_date else None
        row["product_launch_window_days"] = normalized_window_days

        spend = round(float(row.get("spend_usd") or 0), 2)
        summary[segment]["spend_usd"] = round(summary[segment]["spend_usd"] + spend, 2)
        summary[segment]["campaign_count"] += 1

    total_spend = round(
        summary[_LAUNCH_SEGMENT_NEW]["spend_usd"]
        + summary[_LAUNCH_SEGMENT_NON_NEW]["spend_usd"],
        2,
    )
    summary["total_spend_usd"] = total_spend
    if total_spend > 0:
        for key in (_LAUNCH_SEGMENT_NEW, _LAUNCH_SEGMENT_NON_NEW):
            summary[key]["share_pct"] = round(summary[key]["spend_usd"] / total_spend * 100, 2)
    return summary
```

- [ ] **Step 3: Call helper from `generate_unmatched_ads_report()`**

Before the final return in `generate_unmatched_ads_report()`, after `total_unmatched_spend` is computed, add:

```python
    launch_segment_summary = _attach_launch_segments_to_unmatched(unmatched_list)
```

Then add this key to the returned dict:

```python
        "unallocated_launch_segment_summary": launch_segment_summary,
```

- [ ] **Step 4: Run backend GREEN test**

Run:

```bash
pytest tests/test_product_profit_ads.py::test_generate_unmatched_ads_report_labels_new_and_non_new_unallocated_campaigns -q
```

Expected: PASS.

- [ ] **Step 5: Run product ads focused tests**

Run:

```bash
pytest tests/test_product_profit_ads.py -q
```

Expected: PASS.

## Task 3: Frontend RED Tests

**Files:**
- Modify: `tests/test_product_profit_dashboard_assets.py`
- Test: `tests/test_product_profit_dashboard_assets.py`

- [ ] **Step 1: Add failing template test**

Append:

```python
def test_product_profit_unallocated_ads_show_launch_segment_summary_and_label():
    assert 'id="ppd-ads-unmatched-launch-summary"' in TEMPLATE
    assert "<th>新品标签</th>" in TEMPLATE
    assert "function renderUnallocatedLaunchSegmentSummary(summary)" in TEMPLATE
    assert "function formatLaunchSegmentLabel(row)" in TEMPLATE
    assert "unallocated_launch_segment_summary" in TEMPLATE
    assert "非新品 · 未匹配产品" in TEMPLATE
```

- [ ] **Step 2: Run frontend RED test**

Run:

```bash
pytest tests/test_product_profit_dashboard_assets.py::test_product_profit_unallocated_ads_show_launch_segment_summary_and_label -q
```

Expected: FAIL because the summary container and helper functions do not exist yet.

## Task 4: Frontend Implementation

**Files:**
- Modify: `web/templates/product_profit_dashboard.html`
- Test: `tests/test_product_profit_dashboard_assets.py`

- [ ] **Step 1: Add summary container and table column**

Inside the `ppd-ads-unmatched-details` block, after the `summary` element and before `ppd-ads-unmatched-wrap`, add:

```html
        <div class="ppd-list-summary" id="ppd-ads-unmatched-launch-summary" style="display:none; margin-top:10px;"></div>
```

In `#ppd-ads-unmatched-table thead`, add a column after `Campaign name`:

```html
                <th>新品标签</th>
```

- [ ] **Step 2: Render summary from API data**

In `renderAds(data)`, after `renderAdsDaily(data.daily || []);`, add:

```javascript
    renderUnallocatedLaunchSegmentSummary(data.unallocated_launch_segment_summary || null);
```

- [ ] **Step 3: Add JavaScript helper functions before `renderAdsUnmatched`**

Add:

```javascript
  function renderUnallocatedLaunchSegmentSummary(summary) {
    var el = $('ppd-ads-unmatched-launch-summary');
    if (!el) return;
    if (!summary || !summary.total_spend_usd) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    var newItem = summary.new_product || {};
    var nonNewItem = summary.non_new_product || {};
    function segmentHtml(item, fallbackLabel) {
      var label = item.label || fallbackLabel;
      var spend = Number(item.spend_usd || 0);
      var share = Number(item.share_pct || 0);
      var count = Number(item.campaign_count || 0);
      return '<span class="ppd-summary-item"><span class="ppd-summary-label">' + escHtml(label) + '</span>'
        + '<span class="ppd-summary-value">' + money(spend) + '</span>'
        + '<span style="font-size:12px;color:var(--text-user-badge);">' + share.toFixed(2) + '% · ' + count.toLocaleString() + ' 个</span></span>';
    }
    el.innerHTML = segmentHtml(newItem, '新品') + segmentHtml(nonNewItem, '非新品');
    el.style.display = 'flex';
  }

  function formatLaunchSegmentLabel(row) {
    var label = row.launch_segment_label || (row.is_new_product ? '新品' : '非新品');
    if (row.allocation_reason === 'unmatched_product' && label === '非新品') {
      return '非新品 · 未匹配产品';
    }
    return label;
  }
```

- [ ] **Step 4: Add row cell**

In `renderAdsUnmatched(unmatched)`, after the campaign name `<td>`, add:

```javascript
        + '<td>' + escHtml(formatLaunchSegmentLabel(u)) + '</td>'
```

- [ ] **Step 5: Hide summary on empty/old response**

The helper from Step 3 hides the summary when missing or zero. No extra branch is needed.

- [ ] **Step 6: Run frontend GREEN test**

Run:

```bash
pytest tests/test_product_profit_dashboard_assets.py::test_product_profit_unallocated_ads_show_launch_segment_summary_and_label -q
```

Expected: PASS.

- [ ] **Step 7: Run full product dashboard asset tests**

Run:

```bash
pytest tests/test_product_profit_dashboard_assets.py -q
```

Expected: PASS.

## Task 5: Integrated Focused Verification

**Files:**
- Verify: `scripts/pytest_related.py`
- Verify: `tests/test_product_profit_ads.py`
- Verify: `tests/test_product_profit_dashboard_assets.py`

- [ ] **Step 1: Run repository related-test selector**

Run:

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

Expected: PASS, or a focused set that includes the files touched by this task.

- [ ] **Step 2: Run explicit focused tests**

Run:

```bash
pytest tests/test_product_profit_ads.py tests/test_product_profit_dashboard_assets.py -q
```

Expected: PASS.

- [ ] **Step 3: Run route smoke if tests touched template behavior only**

Run:

```bash
python3 -m compileall appcore/order_analytics/product_profit_ads.py web/routes/product_profit_report.py
```

Expected: exit code 0.

## Task 6: Commit

**Files:**
- Commit all task changes except unrelated `paseo.json`.

- [ ] **Step 1: Inspect diff**

Run:

```bash
git diff -- appcore/order_analytics/product_profit_ads.py web/templates/product_profit_dashboard.html tests/test_product_profit_ads.py tests/test_product_profit_dashboard_assets.py docs/superpowers/plans/2026-06-13-product-profit-unallocated-new-product-share.md
git status --short
```

Expected: only intended files plus unrelated untracked `paseo.json`.

- [ ] **Step 2: Commit implementation with docs anchor**

Run:

```bash
git add appcore/order_analytics/product_profit_ads.py \
        web/templates/product_profit_dashboard.html \
        tests/test_product_profit_ads.py \
        tests/test_product_profit_dashboard_assets.py \
        docs/superpowers/plans/2026-06-13-product-profit-unallocated-new-product-share.md
git commit -m "feat(product-profit): label unallocated ads by launch segment" \
  -m "Docs-anchor: docs/superpowers/specs/2026-06-13-product-profit-unallocated-new-product-share-design.md"
```

Expected: commit succeeds and does not include `paseo.json`.
