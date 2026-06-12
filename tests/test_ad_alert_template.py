from __future__ import annotations

from pathlib import Path


def test_ad_alert_template_contract():
    template = Path("web/templates/ad_alerts.html")
    assert template.exists()
    source = template.read_text(encoding="utf-8")

    assert '{% extends "layout.html" %}' in source
    assert "/ad-alerts/api/list" in source
    assert "/ad-alerts/api/detail" in source
    assert "/ad-alerts/api/problem-ads" in source
    assert "/ad-alerts/api/threshold" in source
    assert 'data-ad-alert-tab="alerts"' in source
    assert 'data-ad-alert-tab="problem"' in source
    assert 'data-level="campaign"' in source
    assert 'data-level="adset"' in source
    assert 'data-level="ad"' in source
    assert "今天有消耗且今天成效为 0" in source
    assert "最近 7 天" in source
    assert "最近 30 天" in source
    assert "整体" in source
    assert "X-CSRFToken" in source
    assert "<svg" in source
    assert "Chart" not in source
    assert ".oc-ad-alert-" in source
    assert ".alert-" not in source
    assert "#8b5cf6" not in source.lower()
    assert "purple" not in source.lower()
