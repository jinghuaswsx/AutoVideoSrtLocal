from __future__ import annotations

from pathlib import Path


def test_ad_alert_template_contract():
    template = Path("web/templates/ad_alerts.html")
    assert template.exists()
    source = template.read_text(encoding="utf-8")

    assert '{% extends "layout.html" %}' in source
    assert "/ad-alerts/api/list" in source
    assert "/ad-alerts/api/detail" in source
    assert "/ad-alerts/api/high-loss-ads" in source
    assert "/ad-alerts/api/high-loss-ads/share" in source
    assert "/ad-alerts/api/problem-ads" in source
    assert "/ad-alerts/api/ad-list" in source
    assert "/ad-alerts/api/evaluate" in source
    assert "/ad-alerts/api/threshold" in source
    assert 'href="/ad-alerts/"' in source
    assert 'href="/ad-alerts/alerts"' in source
    assert 'href="/ad-alerts/problem"' in source
    assert "高额亏损广告" in source
    assert "最近 7 天有消耗" in source
    assert "function loadHighLossAds" in source
    assert "function renderHighLossAds" in source
    assert "function createHighLossShare" in source
    assert 'id="adAlertHighLossShare"' in source
    assert 'id="adAlertHighLossShareUrl"' in source
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


def test_ad_alert_problem_mobile_table_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert "oc-ad-alert-problem-table-wrap" in source
    assert "oc-ad-alert-problem-table" in source
    assert "oc-ad-alert-problem-row" in source
    assert "oc-ad-alert-problem-ad-cell" in source
    assert "oc-ad-alert-problem-image-cell" in source
    assert "oc-ad-alert-problem-account-cell" in source
    assert "oc-ad-alert-problem-metric-cell" in source
    assert "data-mobile-label" in source
    assert "@media (max-width: 760px)" in source
    assert ".oc-ad-alert-problem-table thead" in source
    assert "grid-template-columns: 84px minmax(0, 1fr)" in source
    assert ".oc-ad-alert-problem-table .oc-ad-alert-col-hidden" in source


def test_ad_alert_filters_and_modals_mobile_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert 'class="oc-ad-alert-date-filters"' in source
    assert "oc-ad-alert-date-input" in source
    assert ".oc-ad-alert-date-filters {" in source
    assert ".oc-ad-alert-date-filters .oc-ad-alert-btn" in source
    assert ".oc-ad-alert-detail-modal" in source
    assert ".oc-ad-alert-threshold-modal" in source
    assert "width: calc(100vw - 24px)" in source


def test_ad_alert_high_loss_share_public_template_contract():
    template = Path("web/templates/ad_alerts_high_loss_share.html")
    assert template.exists()
    source = template.read_text(encoding="utf-8")

    assert "高额亏损广告分享" in source
    assert "过期时间" in source
    assert "此页面为只读分享结果" in source
    assert "metric_block" in source
    assert "item.metrics.last_7d" in source
    assert "item.consecutive_loss_days" in source


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
    assert "  } else if (state.activeTab === 'problem') {\n    loadProblemAds();\n  } else {\n    restoreUrlParams();\n    loadList();\n  }" in source
    assert "syncUrlParams();" in source
    assert source.index("renderList(data.items || []);") < source.index("syncUrlParams();")


def test_ad_alert_template_problem_column_picker_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert "var columnGroups = {" in source
    assert "yesterday: { label: '昨天', default: false }" in source
    assert 'id="adAlertColumnPickerBtn"' in source
    assert 'id="adAlertColumnDropdown"' in source
    assert ".oc-ad-alert-column-dropdown" in source
    assert ".oc-ad-alert-col-hidden" in source
    assert "sessionStorage.getItem('problem_ads_cols')" in source
    assert "sessionStorage.setItem('problem_ads_cols', JSON.stringify(state.visibleCols))" in source
    assert "function renderColumnPicker()" in source
    assert "function applyColumnVisibility()" in source
    assert "applyColumnVisibility();" in source
    assert '<colgroup span="3" class="oc-ad-alert-col-today"></colgroup>' in source
    assert '<colgroup span="3" class="oc-ad-alert-col-yesterday"></colgroup>' in source
    assert '<th colspan="3" class="oc-ad-alert-col-yesterday oc-ad-alert-group-start">昨天</th>' in source
    assert "problemMetricCells(m.yesterday, 'yesterday')" in source
    assert "oc-ad-alert-col-' + groupKey" in source


def test_ad_alert_template_ai_eval_export_contract():
    template = Path("web/templates/ad_alerts.html")
    source = template.read_text(encoding="utf-8")

    assert ".oc-ad-alert-btn-sm" in source
    assert "evalCacheKey: ''" in source
    assert "state.evalCacheKey = cacheKey" in source
    assert "oc-ad-alert-evaluation-actions" in source
    assert 'id="adAlertEvalCopyBtn"' in source
    assert "复制 JSON" in source
    assert "function bindEvaluationCopy(container, cacheKey)" in source
    assert "var data = adEvaluationCache[cacheKey]" in source
    assert "JSON.stringify(data, null, 2)" in source
    assert "navigator.clipboard.writeText(json)" in source
    assert "document.execCommand('copy')" in source
    assert "showToast('已复制评估结果', 'success')" in source
    assert "showToast('复制失败，请手动选择复制', 'error')" in source


def test_ad_alert_detail_pages_fill_contract():
    route_source = Path("web/routes/ad_alerts.py").read_text(encoding="utf-8")
    product_source = Path("web/templates/ad_alerts_product.html").read_text(encoding="utf-8")
    country_source = Path("web/templates/ad_alerts_country.html").read_text(encoding="utf-8")
    ad_source = Path("web/templates/ad_alerts_ad_detail.html").read_text(encoding="utf-8")

    assert "ad_alerts.get_product_alert_details(product_id" in route_source
    assert "ad_alerts.get_alert_detail(product_id, lang" in route_source
    assert "ad_alerts.get_ad_detail_and_trend(product_id, ad_code, ad_account_id)" in route_source

    assert "function loadDetails()" in product_source
    assert "/ad-alerts/api/product-detail/" in product_source
    assert "/ad-alerts/product/' + productId + '/country/" in product_source
    assert "data-account-id" in product_source

    assert 'id="countryAdList"' in country_source
    assert "function loadCountryAds()" in country_source
    assert "/ad-alerts/api/ad-list?product_id=" in country_source
    assert "/ad-alerts/api/product-detail/" in country_source
    assert "function renderCountryAds(ads, productAds)" in country_source
    assert "?ad_account_id=' + encodeURIComponent(accountId) + '&lang=' + encodeURIComponent(lang) + '&country='" in country_source
    assert "data-detail-url" in country_source

    assert 'id="adDetailMetricGrid"' in ad_source
    assert "总花费" in ad_source
    assert "总购买" in ad_source
    assert "国家" in ad_source
    assert "活跃天数" in ad_source
    assert "request.args.get('lang')" in ad_source
    assert "/ad-alerts/product/{{ product_id }}/country/" in ad_source
    assert "/ad-alerts/api/ad-detail?product_id=" in ad_source


def test_ad_alert_ad_detail_template_uses_result_count_for_results_column():
    source = Path("web/templates/ad_alerts_ad_detail.html").read_text(encoding="utf-8")

    assert "成效 (购买次数)" in source
    assert "intText(item.result_count)" in source
    assert "intText(item.purchase_value_usd)" not in source
