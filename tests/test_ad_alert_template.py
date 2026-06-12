from __future__ import annotations

from pathlib import Path


def test_ad_alert_template_contract():
    template = Path("web/templates/ad_alerts.html")
    assert template.exists()
    source = template.read_text(encoding="utf-8")

    assert '{% extends "layout.html" %}' in source
    assert "/ad-alerts/api/list" in source
    assert "/ad-alerts/api/detail" in source
    assert "/ad-alerts/api/ad-list" in source
    assert "/ad-alerts/api/evaluate" in source
    assert "/ad-alerts/api/threshold" in source
    assert "X-CSRFToken" in source
    assert "function loadAdList" in source
    assert "function runAdEvaluation" in source
    assert "function runCardEvaluation" in source
    assert "function renderAdEvaluations" in source
    assert "adAlertEvaluateBtn" in source
    assert "oc-ad-alert-losing-ads" in source
    assert "oc-ad-alert-btn-ai" in source
    assert "oc-ad-alert-card-eval" in source
    assert '<article class="oc-ad-alert-card"' in source
    assert '<button type="button" class="oc-ad-alert-card"' not in source
    assert "<svg" in source
    assert "Chart" not in source
    assert ".oc-ad-alert-" in source
    assert ".alert-" not in source
    assert "#8b5cf6" not in source.lower()
    assert "purple" not in source.lower()
