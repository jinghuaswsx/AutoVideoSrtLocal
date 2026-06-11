from __future__ import annotations

from pathlib import Path


def test_ad_alert_sidebar_entry_is_in_data_dashboard_group():
    source = Path("web/templates/layout.html").read_text(encoding="utf-8")

    assert "request.path.startswith('/ad-alerts')" in source
    assert '<a href="/ad-alerts"' in source
    assert "广告预警" in source
    assert source.index('href="/order-profit"') < source.index('href="/ad-alerts"')
