from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _template_source() -> str:
    return (ROOT / "web" / "templates" / "order_analytics.html").read_text(encoding="utf-8")


def _realtime_panel_source() -> str:
    template = _template_source()
    panel_start = template.index('<section class="oa-panel active" id="panelRealtime">')
    panel_end = template.index("<!-- ═══════ Tab 0: 产品看板 ═══════ -->", panel_start)
    return template[panel_start:panel_end]


def _ads_panel_source() -> str:
    template = _template_source()
    panel_start = template.index('<div class="oa-panel" id="panelAds">')
    panel_end = template.index("<!-- ═══════ Tab: 广告账户 ═══════ -->", panel_start)
    return template[panel_start:panel_end]


def _new_product_launch_panel_source() -> str:
    template = _template_source()
    panel_start = template.index('<section class="oa-panel" id="panelNewProductLaunch">')
    panel_end = template.index("<!-- ═══════ Tab 0: 产品看板 ═══════ -->", panel_start)
    return template[panel_start:panel_end]


def test_realtime_query_button_is_in_date_toolbar_target_area():
    """查询按钮应紧跟自定义日期范围，产品搜索不占用日期工具栏目标位。"""
    panel = _realtime_panel_source()

    toolbar_start = panel.index('class="oad-toolbar-row oar-realtime-toolbar-row"')
    toolbar_end = panel.index('id="realtimeRangeNote"', toolbar_start)
    toolbar_row = panel[toolbar_start:toolbar_end]

    assert toolbar_row.index('id="realtimeEndDate"') < toolbar_row.index('id="realtimeRefresh"')
    assert 'id="realtimeProductSearchInput"' not in toolbar_row
    assert panel.index('id="realtimeRefresh"') < panel.index('id="realtimeRangeNote"')
    assert panel.index('id="realtimeRangeNote"') < panel.index('id="realtimeProductSearchInput"')


def test_new_product_launch_analysis_tab_is_next_to_realtime():
    template = _template_source()
    topbar_start = template.index('<span class="oa-tabs oa-tabs-topbar"')
    topbar_end = template.index("</span>", topbar_start)
    topbar = template[topbar_start:topbar_end]
    mobile_start = template.index('<nav class="oa-mobile-tabs"')
    mobile_end = template.index("</nav>", mobile_start)
    mobile = template[mobile_start:mobile_end]

    assert topbar.index('data-tab="realtime"') < topbar.index('data-tab="newProductLaunch"')
    assert mobile.index('data-tab="realtime"') < mobile.index('data-tab="newProductLaunch"')
    assert "新品投放分析" in topbar
    assert "新品投放分析" in mobile


def test_new_product_launch_panel_has_three_scope_tabs_and_request_param():
    template = _template_source()
    panel = _new_product_launch_panel_source()

    assert 'id="panelNewProductLaunch"' in panel
    assert 'data-new-product-scope="new"' in panel
    assert 'data-new-product-scope="old"' in panel
    assert 'data-new-product-scope="unmatched"' in panel
    assert "新品分析" in panel
    assert "老品数据" in panel
    assert "未匹配产品" in panel
    assert "var newProductLaunchState" in template
    assert "scope: 'new'" in template
    assert "product_launch_scope" in template
    assert "loadNewProductLaunchOverview" in template


def test_new_product_launch_store_filter_uses_realtime_option_code():
    panel = _new_product_launch_panel_source()
    store_filter = panel[
        panel.index('id="nplSiteFilter"'):
        panel.index("</select>", panel.index('id="nplSiteFilter"'))
    ]

    assert 'value="{{ option.code }}"' in store_filter
    assert 'value="{{ option.value }}"' not in store_filter


def test_new_product_launch_renders_data_quality_without_realtime_dom_conflict():
    template = _template_source()
    panel = _new_product_launch_panel_source()
    render_block = template[
        template.index("function renderNewProductLaunchOverview"):
        template.index("function renderNewProductLaunchOrders")
    ]

    assert 'data-npl-dq-bar' in panel
    assert "function renderNewProductLaunchDataQualityBar" in template
    assert "renderNewProductLaunchDataQualityBar(data && data.data_quality);" in render_block
    assert "window.renderDataQualityBar(data && data.data_quality);" not in render_block


def test_new_product_launch_roas_prefers_scoped_roas_points():
    template = _template_source()
    render_block = template[
        template.index("function renderNewProductLaunchRoas"):
        template.index("// ── 入口：同步刷新顶部卡片 + 子 tab", template.index("function renderNewProductLaunchRoas"))
    ]

    assert "var rows = data.roas_points || [];" in render_block
    assert "var rows = data.hourly || [];" not in render_block


def test_realtime_bj_hint_is_inserted_after_query_button():
    """北京时间提示不能插入到日期范围和查询按钮之间。"""
    template = _template_source()

    assert "var realtimeActions = anchor.parentElement.querySelector('.oar-realtime-actions');" in template
    assert "var insertAfter = realtimeActions || anchor;" in template
    assert "anchor.parentElement.insertBefore(hint, insertAfter.nextSibling);" in template


def test_realtime_roas_trend_copy_matches_hourly_node_contract():
    panel = _realtime_panel_source()

    assert "每 10 分钟" not in panel
    assert "每 20 分钟同步" in panel
    assert "走势图按广告系统日小时节点展示" in panel


def test_realtime_global_break_even_roas_kpi_is_rendered():
    panel = _realtime_panel_source()

    assert 'id="realtimeGlobalBreakEvenRoas"' in panel
    assert "全局保本 ROAS" in panel


def test_realtime_global_break_even_roas_js_uses_three_decimals():
    template = _template_source()

    assert "profitSummary.global_break_even_roas" in template
    assert "globalBreakEvenRoasValue.toFixed(3)" in template
    assert "realtimeGlobalBreakEvenRoas" in template


def test_realtime_summary_splits_global_new_old_and_unmatched_scope_cards():
    panel = _realtime_panel_source()

    assert 'data-realtime-scope-card="global"' in panel
    assert 'data-realtime-scope-card="new"' in panel
    assert 'data-realtime-scope-card="old"' in panel
    assert 'data-realtime-scope-card="unmatched"' in panel
    assert "全局数据" in panel
    assert "新品数据" in panel
    assert "老品数据" in panel
    assert "未匹配广告和订单" in panel
    assert "未匹配广告和订单同口径核算" in panel
    assert "无新品/老品过滤" in panel
    assert "product_launch_scope=new" in panel
    assert "product_launch_scope=old" in panel
    assert "product_launch_scope=unmatched" in panel
    assert 'id="realtimeNewRevenue"' in panel
    assert 'id="realtimeOldRevenue"' in panel
    assert 'id="realtimeUnmatchedSpend"' in panel


def test_realtime_top_cards_fetch_scoped_new_old_and_unmatched_summaries():
    template = _template_source()
    load_block = template[
        template.index("function loadRealtimeTopCards"):
        template.index("function renderRealtimeOrders")
    ]

    assert "fetchRealtimeScopeSummary(baseParams, 'global')" in load_block
    assert "fetchRealtimeScopeSummary(baseParams, 'new')" in load_block
    assert "fetchRealtimeScopeSummary(baseParams, 'old')" in load_block
    assert "fetchRealtimeScopeSummary(baseParams, 'unmatched')" in load_block
    assert "params.set('product_launch_scope', scope);" in load_block
    assert "renderRealtimeScopeSummary('new'" in load_block
    assert "renderRealtimeScopeSummary('old'" in load_block
    assert "renderRealtimeScopeSummary('unmatched'" in load_block
    assert "product_id 为空订单同口径核算" in load_block


def test_order_analytics_mobile_tables_keep_shared_header_and_body_layout():
    """移动端表格不能把 thead/tbody 拆成两张表，否则表头和数据列会错位。"""
    template = _template_source()
    panel = _realtime_panel_source()
    ads_panel = _ads_panel_source()
    campaign_start = panel.index('<div class="oar-subpanel" id="realtimeSubCampaigns">')
    campaign_end = panel.index('<div class="oar-subpanel" id="realtimeSubTrend">', campaign_start)
    campaign_panel = panel[campaign_start:campaign_end]

    assert 'id="realtimeCampaignBody"' in campaign_panel
    assert 'class="oa-table oar-compact-table oar-campaign-table"' in campaign_panel
    assert "\n  .oa-table-scroll table.oa-table:not(.mobile-no-scroll)" in template
    assert "#panelRealtime .oa-table-scroll table.oa-table:not(.mobile-no-scroll)" not in template
    assert "display: table-header-group;" in template
    assert "display: table-row-group;" in template
    assert "display: table-footer-group;" in template
    assert ".oar-campaign-table th:first-child" in template
    assert ".oar-campaign-table td:first-child" in template
    assert 'id="adTable"' in ads_panel
    assert 'data-ads-list-table="{{ level }}"' in ads_panel
    assert 'id="amsTable"' in ads_panel


def test_ads_level_name_columns_expose_copy_buttons():
    """Campaign / Ad Set / Ad 子 Tab 的名称列都必须提供复制当前行名称的按钮。"""
    template = _template_source()

    assert 'data-ads-copy-name="' in template
    assert "adsCopyLabels" in template
    assert "复制Campaign名" in template
    assert "复制Ad Set名" in template
    assert "复制广告名" in template
    assert "function adsCopyText" in template
    assert "ev.stopPropagation();" in template
    assert "var copyLabel = adsCopyLabels[level];" in template
    assert "if (copyLabel)" in template
    assert "if (level === 'ad')" not in template
    assert 'data-ads-copy-name="' not in template[: template.index("function adsRenderList")]


def test_ads_page_supports_campaign_detail_deep_link_from_query_params():
    """素材管理广告计划入口应能新开 order-analytics 并自动进入 Campaign 详情。"""
    template = _template_source()

    assert "function adsApplyDeepLinkFromQuery()" in template
    assert "params.get('tab') !== 'ads'" in template
    assert "params.get('ads_level')" in template
    assert "params.get('ads_code')" in template
    assert "setAdsSubtab(level);" in template
    assert "adsOpenDetail(level, code, name || code, accountId);" in template
    assert "document.addEventListener('DOMContentLoaded', adsApplyDeepLinkFromQuery);" in template


def test_ads_deep_link_defaults_detail_range_to_recent_month():
    """素材广告计划深链进入 Campaign 详情时，日期默认最近一个月。"""
    template = _template_source()

    assert "function adsRecentMonthStartIso()" in template
    assert "function adsApplyDeepLinkDateRange(level)" in template
    assert "startDetailEl.value = adsRecentMonthStartIso();" in template
    assert "endDetailEl.value = adsDefaultEndIso();" in template
    assert "new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate()" in template

    deep_link = template[
        template.index("function adsApplyDeepLinkFromQuery()"):
        template.index("if (document.readyState === 'loading')")
    ]
    assert "adsApplyDeepLinkDateRange(level);" in deep_link
    assert deep_link.index("adsInitDateInputs(level);") < deep_link.index("adsApplyDeepLinkDateRange(level);")
    assert deep_link.index("adsApplyDeepLinkDateRange(level);") < deep_link.index("adsOpenDetail(level, code, name || code, accountId);")


def test_product_profit_actions_move_into_mobile_content_top():
    """移动端业务按钮应进入页面内容区顶部，不挤在全局顶栏最上方。"""
    template = _template_source()

    content_start = template.index("{% block content %}")
    mobile_actions_start = template.index('class="ppr-mobile-actions"', content_start)
    data_quality_start = template.index('{% include "_data_quality_bar.html" %}', content_start)
    mobile_actions = template[mobile_actions_start:data_quality_start]

    assert "docs/superpowers/specs/2026-05-10-data-analysis-mobile-actions-placement.md" in template
    assert mobile_actions_start < data_quality_start
    assert 'data-ppr="open-import"' in mobile_actions
    assert 'data-ppr="open-report"' in mobile_actions
    assert "Payments 导入" in mobile_actions
    assert "盈亏报表" in mobile_actions
    assert ".ppr-mobile-actions { display: none; }" in template
    assert ".topbar .ppr-actions { display: none; }" in template
    assert "display: grid;" in template
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in template


def test_order_analytics_secondary_screen_uses_compact_realtime_layout():
    """Portrait desktop and narrow secondary-screen layouts should reuse compact in-page controls."""
    template = _template_source()

    summary_row_start = template.index(".oar-summary-row {")
    summary_row_end = template.index("}", summary_row_start)
    summary_row_css = template[summary_row_start:summary_row_end]
    assert "align-items: start;" in summary_row_css

    assert "docs/superpowers/specs/2026-05-20-order-analytics-secondary-screen-adaptation-design.md" in template
    compact_query = "@media (min-width: 769px) and (max-width: 1180px), (min-width: 769px) and (orientation: portrait)"
    assert compact_query in template
    assert template.index(compact_query) > template.index(".ppr-mobile-actions { display: none; }")

    compact_start = template.index(compact_query)
    compact_end = template.index("@media (max-width: 768px)", compact_start)
    compact_css = template[compact_start:compact_end]

    assert ".topbar .oa-tabs-topbar { display: none; }" in compact_css
    assert ".topbar .ppr-actions { display: none; }" in compact_css
    assert ".oa-mobile-tabs {" in compact_css
    assert "display: block;" in compact_css
    assert ".ppr-mobile-actions {" in compact_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in compact_css
    assert ".oar-summary-row-main," in compact_css
    assert ".oar-summary-row-time {" in compact_css
    assert ".oar-summary-row {" in compact_css
    assert "align-items: start;" in compact_css


def test_manual_ad_spend_rendering_escapes_server_controlled_values():
    """Manual ad spend rows include DB/config values; they must not be injected as raw HTML."""
    template = _template_source()

    manual_block = template[template.index("function amsStatusBadge"): template.index("function amsSaveModal")]

    assert "label: escHtml(status)" in manual_block
    assert "var businessDateHtml = escHtml(row.business_date);" in manual_block
    assert "var businessDateAttr = escHtml(row.business_date);" in manual_block
    assert "var updatedByHtml = escHtml(updatedBy);" in manual_block
    assert "var accountLabelHtml = escHtml(acc.label || acc.code || '');" in manual_block
    assert "var accountIdHtml = escHtml(acc.account_id || '');" in manual_block
    assert "var accountCodeAttr = escHtml(acc.code || '');" in manual_block
    assert "var prefillAttr = escHtml(prefillVal);" in manual_block
    assert "var html = '<td>' + row.business_date + '</td>';" not in manual_block
    assert "wrap.innerHTML = (acc.label || acc.code)" not in manual_block
