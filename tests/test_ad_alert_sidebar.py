from __future__ import annotations

from pathlib import Path


def test_ad_alert_sidebar_entry_is_independent_below_data_dashboard_group():
    source = Path("web/templates/layout.html").read_text(encoding="utf-8")

    assert "request.path.startswith('/ad-alerts')" in source
    assert '<a href="/ad-alerts"' in source
    assert "广告预警" in source
    link_index = source.index('href="/ad-alerts"')
    group_start = source.index("sidebar-data-dashboard-group")
    material_start = source.index("{% set material_creation_href")
    data_group_end = source.index("</details>", group_start)
    admin_guard = source.rfind("{% if current_user.is_admin %}", group_start, link_index)

    assert group_start < data_group_end < link_index < material_start
    assert admin_guard != -1
