# Realtime Unallocated Campaign Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking the realtime order-profit "未分摊广告费" KPI switches to the campaign tab and shows only the campaigns behind that unallocated spend.

**Architecture:** Backend annotates each realtime campaign with allocation metadata using the same product matching and profit-line units口径 as ad-cost fallback. The existing `realtime-overview` response carries `unallocated_campaigns` and a small summary, while the frontend stores the last campaign rows and applies a local "unallocated only" filter when the KPI is clicked.

**Tech Stack:** Python 3.12, Flask route facade, pytest, Jinja template with vanilla JavaScript.

---

## File Structure

- Modify `docs/superpowers/specs/2026-05-10-realtime-unallocated-campaign-navigation.md`: design anchor for the change.
- Modify `appcore/order_analytics/CLAUDE.md`: link the spec near existing realtime dashboard specs.
- Modify `tests/test_order_analytics_realtime_profit_details.py`: backend campaign allocation tests.
- Modify `tests/test_order_analytics_true_roas.py`: template wiring tests for clickable KPI and campaign filter.
- Modify `appcore/order_analytics/realtime.py`: campaign allocation helpers and response fields.
- Modify `web/templates/order_analytics.html`: clickable KPI, campaign filter state, table status column.

## Task 1: Backend Failing Tests

**Files:**
- Modify: `tests/test_order_analytics_realtime_profit_details.py`

- [x] **Step 1: Add tests for campaign allocation metadata**

Append tests that monkeypatch `oa.query` and `realtime.resolve_ad_product_match`, then call a new helper `_annotate_campaign_allocation`:

```python
def test_annotate_campaign_allocation_marks_unmatched_product(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", lambda code: None)
    monkeypatch.setattr(oa, "query", lambda *a, **kw: [])

    campaigns = [{"campaign_name": "unknown-campaign", "normalized_campaign_code": "unknown-campaign", "spend_usd": 12.34}]
    result = realtime_oa._annotate_campaign_allocation(campaigns, date(2026, 5, 9), date(2026, 5, 9))

    assert result["campaigns"][0]["allocation_status"] == "unallocated"
    assert result["campaigns"][0]["allocation_reason"] == "unmatched_product"
    assert result["campaigns"][0]["unallocated_spend_usd"] == 12.34
    assert result["unallocated_campaign_summary"] == {"count": 1, "spend_usd": 12.34}
```

Add two sibling tests:

```python
def test_annotate_campaign_allocation_marks_matched_product_without_units(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", lambda code: {"id": 427, "product_code": "fully-automatic-water-blaster-rjc", "name": "ARP9电动水枪"})
    monkeypatch.setattr(oa, "query", lambda *a, **kw: [])

    campaigns = [{"campaign_name": "fully-automatic-water-blaster", "normalized_campaign_code": "fully-automatic-water-blaster", "spend_usd": 79.07}]
    result = realtime_oa._annotate_campaign_allocation(campaigns, date(2026, 5, 9), date(2026, 5, 9))

    row = result["campaigns"][0]
    assert row["allocation_status"] == "unallocated"
    assert row["allocation_reason"] == "matched_no_units"
    assert row["matched_product_id"] == 427
    assert row["matched_product_code"] == "fully-automatic-water-blaster-rjc"
    assert row["matched_product_name"] == "ARP9电动水枪"
    assert result["unallocated_campaigns"] == [row]
```

```python
def test_annotate_campaign_allocation_marks_allocated_when_units_exist(monkeypatch):
    from appcore.order_analytics import realtime as realtime_oa

    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", lambda code: {"id": 316, "product_code": "sonic-lens-refresher-rjc", "name": "隐形眼镜清洗器"})

    def fake_query(sql, args=()):
        assert "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id" in sql
        return [{"business_date": date(2026, 5, 9), "product_id": 316, "units": 26}]

    monkeypatch.setattr(oa, "query", fake_query)

    campaigns = [{"campaign_name": "sonic-lens-refresher-rjc", "normalized_campaign_code": "sonic-lens-refresher-rjc", "spend_usd": 221.41}]
    result = realtime_oa._annotate_campaign_allocation(campaigns, date(2026, 5, 9), date(2026, 5, 9))

    row = result["campaigns"][0]
    assert row["allocation_status"] == "allocated"
    assert row["allocation_reason"] == "allocated"
    assert row["unallocated_spend_usd"] == 0.0
    assert result["unallocated_campaigns"] == []
    assert result["unallocated_campaign_summary"] == {"count": 0, "spend_usd": 0.0}
```

- [x] **Step 2: Run tests and confirm RED**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py -k annotate_campaign_allocation -q
```

Expected: FAIL with `AttributeError` because `_annotate_campaign_allocation` is not implemented.

## Task 2: Backend Implementation

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Test: `tests/test_order_analytics_realtime_profit_details.py`

- [x] **Step 1: Implement minimal allocation helpers**

Add helper functions after `_format_realtime_campaign_details`:

```python
def _campaign_code(row: dict[str, Any]) -> str:
    return str(row.get("normalized_campaign_code") or row.get("campaign_name") or "").strip().lower()

def _load_profit_units_for_products(date_from: date, date_to: date, product_ids: set[int]) -> dict[tuple[date, int], int]:
    if not product_ids:
        return {}
    dates = []
    current = date_from
    while current <= date_to:
        dates.append(current)
        current += timedelta(days=1)
    placeholders_dates = ", ".join(["%s"] * len(dates))
    placeholders_products = ", ".join(["%s"] * len(product_ids))
    rows = query(
        "SELECT d.meta_business_date AS business_date, p.product_id, COALESCE(SUM(d.quantity), 0) AS units "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        f"WHERE d.meta_business_date IN ({placeholders_dates}) "
        f"AND p.product_id IN ({placeholders_products}) "
        "GROUP BY d.meta_business_date, p.product_id",
        tuple(dates + sorted(product_ids)),
    )
    return {
        (row["business_date"], int(row["product_id"])): int(row.get("units") or 0)
        for row in rows or []
        if row.get("business_date") and row.get("product_id") is not None
    }
```

Then add `_annotate_campaign_allocation(campaigns, date_from, date_to)`.

- [x] **Step 2: Run backend allocation tests and confirm GREEN**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py -k annotate_campaign_allocation -q
```

Expected: PASS.

- [x] **Step 3: Wire response fields**

In each single-day response branch that already computes `campaign_details`, call:

```python
campaign_allocation = _annotate_campaign_allocation([dict(row) for row in campaign_details], target, target)
campaign_details = campaign_allocation["campaigns"]
```

Return:

```python
"campaigns": campaign_details,
"unallocated_campaigns": campaign_allocation["unallocated_campaigns"],
"unallocated_campaign_summary": campaign_allocation["unallocated_campaign_summary"],
```

For range response branches with no campaigns, return empty fields:

```python
"unallocated_campaigns": [],
"unallocated_campaign_summary": {"count": 0, "spend_usd": 0.0},
```

## Task 3: Frontend Failing Tests

**Files:**
- Modify: `tests/test_order_analytics_true_roas.py`

- [x] **Step 1: Add template assertions**

Add tests:

```python
def test_realtime_unallocated_ad_card_is_clickable_and_campaign_filter_is_wired(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    assert 'id="realtimeProfitUnallocatedAdCard"' in panel
    assert 'data-realtime-campaign-filter="unallocated"' in panel
    assert "function showRealtimeUnallocatedCampaigns()" in body
    assert "setRealtimeSubtab('campaigns')" in body
    assert "realtimeState.campaignFilter = 'unallocated'" in body
```

```python
def test_realtime_campaign_table_has_allocation_status_column(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    panel = _extract_realtime_panel(response.get_data(as_text=True))

    assert "<th>分摊状态</th>" in panel
    assert "formatCampaignAllocationStatus(row)" in response.get_data(as_text=True)
```

- [x] **Step 2: Run tests and confirm RED**

Run:

```bash
pytest tests/test_order_analytics_true_roas.py -k "unallocated_ad_card or allocation_status_column" -q
```

Expected: FAIL because hooks and status column do not exist yet.

## Task 4: Frontend Implementation

**Files:**
- Modify: `web/templates/order_analytics.html`
- Test: `tests/test_order_analytics_true_roas.py`

- [x] **Step 1: Add clickable KPI hook**

Change the unallocated KPI item to:

```html
<button type="button" class="oar-profit-summary-item oar-profit-summary-action" id="realtimeProfitUnallocatedAdCard" data-realtime-campaign-filter="unallocated" aria-label="查看未分摊广告费对应广告计划">
```

Close it with `</button>` instead of `</div>`.

- [x] **Step 2: Add campaign filter state and tab helper**

In `realtimeState`, add:

```javascript
campaignFilter: ''
```

Extract subtab switching into:

```javascript
function setRealtimeSubtab(name) {
  document.querySelectorAll('[data-realtime-subtab]').forEach(function(item) {
    item.classList.toggle('is-active', item.dataset.realtimeSubtab === name);
  });
  document.querySelectorAll('.oar-subpanel').forEach(function(panel) {
    panel.classList.toggle('is-active', panel.id === 'realtimeSub' + capitalize(name));
  });
}
```

Update existing subtab click handler to call `setRealtimeSubtab(btn.dataset.realtimeSubtab)`.

- [x] **Step 3: Add filtering functions**

Add:

```javascript
var realtimeLastCampaignRows = [];

function showRealtimeUnallocatedCampaigns() {
  realtimeState.campaignFilter = 'unallocated';
  setRealtimeSubtab('campaigns');
  renderRealtimeCampaigns(realtimeLastCampaignRows);
}

function clearRealtimeCampaignFilter() {
  realtimeState.campaignFilter = '';
  renderRealtimeCampaigns(realtimeLastCampaignRows);
}

function formatCampaignAllocationStatus(row) {
  if (!row || row.allocation_status !== 'unallocated') return '已分摊';
  if (row.allocation_reason === 'unmatched_product') return '未匹配 product';
  if (row.allocation_reason === 'matched_no_units') return '无可分摊订单';
  return '未分摊';
}
```

Wire click listener for `realtimeProfitUnallocatedAdCard`.

- [x] **Step 4: Update campaign table**

Add `<th>分摊状态</th>` before plan ID and add `addTextCell(tr, formatCampaignAllocationStatus(row));`. Empty rows use `colspan="10"`.

- [x] **Step 5: Run frontend tests and confirm GREEN**

Run:

```bash
pytest tests/test_order_analytics_true_roas.py -k "unallocated_ad_card or allocation_status_column" -q
```

Expected: PASS.

## Task 5: Full Verification

**Files:**
- Verify only

- [x] **Step 1: Run required test set**

Run:

```bash
pytest tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_template_layout.py -q
```

Expected: PASS.

- [x] **Step 2: Run git diff review**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only planned files changed.
