from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _layout_source() -> str:
    return (ROOT / "web" / "templates" / "layout.html").read_text(encoding="utf-8")


def test_mobile_header_has_selected_module_sync_contract():
    source = _layout_source()

    assert 'data-mobile-brand-icon' in source
    assert 'data-mobile-brand-text' in source
    assert 'syncMobileModuleBrand' in source
    assert "document.querySelector('.sidebar-nav a.active')" in source
    assert "textEl.textContent = label" in source
    assert "iconEl.textContent = icon" in source
    assert "brand.setAttribute('href', href)" in source


def test_order_analytics_sidebar_entry_provides_mobile_header_source():
    source = _layout_source()

    assert '<a href="/order-analytics" {% if request.path.startswith' in source
    assert '<a href="/order-analytics" target="_blank"' not in source
    assert '<span class="nav-icon">📊</span> 数据分析' in source


def test_sidebar_collection_headers_expand_and_open_first_visible_child():
    source = _layout_source()

    assert 'data-sidebar-group-summary' in source
    assert 'data-default-href="{{ data_dashboard_href }}"' in source
    assert 'data-default-href="{{ material_creation_href }}"' in source
    assert 'data-default-href="{{ video_translate_href }}"' in source
    assert 'data-default-href="{{ settings_href }}"' in source
    assert "group.open = true" in source
    assert "window.location.href = href" in source
    assert "window.open(href, '_blank', 'noopener,noreferrer')" not in source
