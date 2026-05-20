from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _layout_source() -> str:
    return (ROOT / "web" / "templates" / "layout.html").read_text(encoding="utf-8")


def test_desktop_sidebar_has_collapse_and_reopen_contract():
    source = _layout_source()

    assert 'id="sidebarCollapseToggle"' in source
    assert 'aria-label="收起导航菜单"' in source
    assert 'html[data-sidebar-state="collapsed"] .sidebar' in source
    assert 'html[data-sidebar-state="collapsed"] .main-wrap' in source
    assert 'html[data-sidebar-state="collapsed"] .sidebar-toggle' in source
    assert "sidebarCollapsed" in source
    assert "setSidebarCollapsed(true)" in source
    assert "setSidebarCollapsed(false)" in source


def test_mobile_sidebar_drawer_contract_remains_intact():
    source = _layout_source()

    assert "body.classList.add('sidebar-open')" in source
    assert "body.classList.remove('sidebar-open')" in source
    assert "body.classList.contains('sidebar-open')" in source
    assert "backdrop.addEventListener('click', closeDrawer)" in source
    assert "e.key === 'Escape'" in source


def test_desktop_forced_drawer_mode_uses_hamburger_as_drawer_trigger():
    source = _layout_source()

    assert "function isForcedDrawerMode()" in source
    assert "function isDrawerMode()" in source
    assert "var sidebarTransform = window.getComputedStyle(sidebar).transform;" in source
    assert "return !isDesktop() || isForcedDrawerMode();" in source
    assert "if (isDesktop() && !isDrawerMode())" in source
