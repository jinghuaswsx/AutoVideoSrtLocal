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
    assert "/ad-alerts/api/ad-list" in source
    assert "/ad-alerts/api/evaluate" in source
    assert "/ad-alerts/api/threshold" in source
    assert 'href="/ad-alerts/"' in source
    assert 'href="/ad-alerts/problem"' in source
    assert 'data-level="campaign"' in source
    assert 'data-level="adset"' in source
    assert 'data-level="ad"' in source
    assert "今天有消耗且今天成效为 0" in source
    assert "最近 7 天" in source
    assert "最近 30 天" in source
    assert "整体" in source
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


def test_ad_alert_template_toast_feedback_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert "function showToast(message, type)" in source
    assert "oc-ad-alert-toast-container" in source
    assert "oc-ad-alert-toast" in source
    assert "@keyframes ocToastIn" in source
    assert "@keyframes ocToastOut" in source
    assert "showToast('阈值必须大于 0', 'error')" in source
    assert "showToast('阈值已更新为 ' + state.threshold.toFixed(2), 'success')" in source
    assert "showToast('保存失败：' + (error.message || '未知错误'), 'error')" in source
    assert "showToast('评估完成，共 ' + evaluations.length + ' 条建议', 'info')" in source


def test_ad_alert_template_url_persistence_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert "function syncUrlParams()" in source
    assert "function restoreUrlParams()" in source
    assert "if (state.activeTab !== 'alerts') return;" in source
    assert "params.set('severity', state.severity)" in source
    assert "params.set('search', state.search)" in source
    assert "params.set('start_date', startVal)" in source
    assert "params.set('end_date', endVal)" in source
    assert "window.history.replaceState(null, '', nextUrl)" in source
    assert "new URLSearchParams(window.location.search)" in source
    assert "state.severity = params.get('severity') || ''" in source
    assert "state.search = params.get('search') || ''" in source
    assert "restoreUrlParams();" in source
    assert "  } else {\n    restoreUrlParams();\n    loadList();\n  }" in source
    assert "syncUrlParams();" in source
    assert source.index("renderList(data.items || []);") < source.index("syncUrlParams();")
