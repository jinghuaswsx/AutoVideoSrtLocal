from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _realtime_panel_source() -> str:
    template = (ROOT / "web" / "templates" / "order_analytics.html").read_text(encoding="utf-8")
    panel_start = template.index('<section class="oa-panel active" id="panelRealtime">')
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
