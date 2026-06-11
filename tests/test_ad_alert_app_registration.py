from __future__ import annotations

from pathlib import Path


def test_ad_alert_blueprint_is_registered_and_csrf_guarded():
    source = Path("web/app.py").read_text(encoding="utf-8")

    assert "from web.routes.ad_alerts import bp as ad_alerts_bp" in source
    assert '"ad_alerts"' in source
    assert "app.register_blueprint(ad_alerts_bp)" in source
