from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _template_source() -> str:
    return (ROOT / "web" / "templates" / "order_analytics.html").read_text(encoding="utf-8")


def _realtime_panel_source() -> str:
    template = _template_source()
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


def test_realtime_bj_hint_is_inserted_after_query_button():
    """北京时间提示不能插入到日期范围和查询按钮之间。"""
    template = _template_source()

    assert "var realtimeActions = anchor.parentElement.querySelector('.oar-realtime-actions');" in template
    assert "var insertAfter = realtimeActions || anchor;" in template
    assert "anchor.parentElement.insertBefore(hint, insertAfter.nextSibling);" in template
