# 广告分析未匹配广告计划子 Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在广告分析面板新增 `未匹配广告计划` 子 Tab，展示素材管理库里没有相关产品的 Campaign 级广告计划。

**Architecture:** 复用现有 `/order-analytics/ad-summary` 的 `unmatched` 数据，不新增后端接口。前端新增独立子面板、独立控件和轻量渲染函数，继续调用现有人工配对弹窗。

**Tech Stack:** Flask/Jinja 模板、原生 JavaScript、pytest 模板断言。

---

### Task 1: 模板契约测试

**Files:**
- Modify: `tests/test_order_analytics_ads.py`

- [ ] **Step 1: Write the failing test**

```python
def test_ads_analysis_page_has_unmatched_campaigns_subtab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-ads-subtab="unmatched-campaigns"' in body
    assert 'data-subpanel="unmatched-campaigns"' in body
    assert 'id="adUnmatchedSearchInput"' in body
    assert 'data-ads-account-filter="unmatched-campaigns"' in body
    assert 'id="adUnmatchedRefresh">查询</button>' in body
    assert "function loadAdUnmatchedCampaigns()" in body
    assert "function renderAdUnmatchedCampaigns(rows)" in body
    assert "openAdMatchModal(row)" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_analytics_ads.py::test_ads_analysis_page_has_unmatched_campaigns_subtab -q`

Expected: FAIL because the new subtab markup and functions do not exist yet.

### Task 2: 模板与前端实现

**Files:**
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: Add the new subtab markup**

Add a button with `data-ads-subtab="unmatched-campaigns"` and a subpanel with `data-subpanel="unmatched-campaigns"`.

- [ ] **Step 2: Add the JavaScript behavior**

Implement `loadAdUnmatchedCampaigns()` and `renderAdUnmatchedCampaigns(rows)`. The loader calls `/order-analytics/ad-summary` with the unmatched panel's date range, search query, and ad account filter, then renders only `data.unmatched`.

- [ ] **Step 3: Run the failing test again**

Run: `pytest tests/test_order_analytics_ads.py::test_ads_analysis_page_has_unmatched_campaigns_subtab -q`

Expected: PASS.

### Task 3: Regression verification

**Files:**
- Existing tests only.

- [ ] **Step 1: Run focused regression tests**

Run:

```bash
pytest tests/test_order_analytics_ads.py::test_ads_analysis_page_has_unmatched_campaigns_subtab tests/test_order_analytics_ads.py::test_ads_level_search_queries_bottom_list_without_dropdown tests/test_order_analytics_template_layout.py::test_ads_subtabs_use_large_click_targets -q
```

Expected: all selected tests pass.
