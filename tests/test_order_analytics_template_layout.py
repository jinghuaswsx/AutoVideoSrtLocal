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
