from __future__ import annotations

from pathlib import Path


def test_admin_settings_exposes_ad_alert_threshold():
    admin_source = Path("web/routes/admin.py").read_text(encoding="utf-8")
    template_source = Path("web/templates/admin_settings.html").read_text(encoding="utf-8")

    assert 'request.form.get("ad_alert_roas_threshold"' in admin_source
    assert "ad_alerts.set_threshold" in admin_source
    assert "ad_alerts.get_threshold()" in admin_source
    assert "ad_alert_threshold=ad_alert_threshold" in admin_source
    assert 'name="ad_alert_roas_threshold"' in template_source
    assert "ad_alert_threshold" in template_source
    assert "广告预警阈值" in template_source
