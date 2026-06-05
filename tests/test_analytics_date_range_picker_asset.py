from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "web" / "static" / "analytics_date_range_picker.js").read_text(encoding="utf-8")


def _function_block(name: str, end_marker: str) -> str:
    start = SCRIPT.index(f"function {name}")
    end = SCRIPT.index(end_marker, start)
    return SCRIPT[start:end]


def test_mobile_date_range_panel_is_fixed_to_viewport_bottom():
    """Docs-anchor: docs/superpowers/specs/2026-06-05-analytics-date-range-picker-mobile-auto-apply-design.md"""
    expected_snippets = [
        "Docs-anchor: docs/superpowers/specs/2026-06-05-analytics-date-range-picker-mobile-auto-apply-design.md",
        "@media(max-width:640px)",
        ".analytics-range-picker{display:block;width:100%;min-width:0;}",
        ".analytics-range-panel{position:fixed;left:var(--space-3,12px);right:var(--space-3,12px);bottom:0;top:auto;width:auto;max-height:min(78vh,680px);overflow:auto;transform:none;",
        "padding-bottom:calc(var(--space-4,16px) + env(safe-area-inset-bottom,0px));",
        ".analytics-range-calendars{grid-template-columns:1fr;gap:var(--space-3,12px);}",
        ".analytics-calendar-day{min-height:40px;font-size:14px;}",
    ]
    for snippet in expected_snippets:
        assert snippet in SCRIPT, f"missing mobile picker CSS snippet: {snippet}"


def test_second_date_click_auto_applies_without_confirm_button():
    """Docs-anchor: docs/superpowers/specs/2026-06-05-analytics-date-range-picker-mobile-auto-apply-design.md"""
    select_day_block = _function_block("selectDay", "function applyRange")

    assert "第二个日期会自动生效" in SCRIPT
    assert "确认后生效" not in SCRIPT
    assert "data-range-apply" not in SCRIPT
    assert "analytics-range-apply" not in SCRIPT
    assert "applyRange();" in select_day_block
    assert select_day_block.index("waitingForEnd = false;") < select_day_block.index("applyRange();")
    assert "return;" in select_day_block[select_day_block.index("applyRange();") :]
