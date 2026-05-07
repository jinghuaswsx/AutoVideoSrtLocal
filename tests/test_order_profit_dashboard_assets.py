from pathlib import Path


TEMPLATE = Path("web/templates/order_profit_dashboard.html").read_text(encoding="utf-8")


def test_order_profit_dashboard_escapes_api_backed_html_fields():
    assert "function escapeHtml(value)" in TEMPLATE

    unsafe_interpolations = [
        "${o.dxm_package_id}",
        "${o.buyer_country || '-'}",
        "${l.product_code || '#'+l.product_id || '-'}",
        "${l.product_sku || ''}",
        "${(l.missing_fields||[]).join('/')}",
        "${p.product_code || '#'+p.product_id}",
        "${p.name || ''}",
        "${c.normalized_campaign_code}",
        "${o.normalized_campaign_code}",
        "${o.reason || '-'}",
        "${o.created_by || '-'}",
    ]
    for snippet in unsafe_interpolations:
        assert snippet not in TEMPLATE

    expected_escaped_paths = [
        "escapeHtml(o.dxm_package_id || '-')",
        "escapeHtml(o.buyer_country || '-')",
        "escapeHtml(l.product_code || ('#' + l.product_id) || '-')",
        "escapeHtml(l.product_sku || '')",
        "escapeHtml((l.missing_fields || []).join('/'))",
        "escapeHtml(p.name || '')",
        "escapeHtml(c.normalized_campaign_code || '')",
        "escapeHtml(o.normalized_campaign_code || '')",
        "escapeHtml(o.reason || '-')",
        "escapeHtml(o.created_by || '-')",
    ]
    for snippet in expected_escaped_paths:
        assert snippet in TEMPLATE

def test_order_profit_campaign_product_picker_is_searchable_and_tall():
    assert ".op-product-picker-trigger" in TEMPLATE
    assert "min-height: 60px" in TEMPLATE
    assert 'data-op-product-search' in TEMPLATE
    assert 'placeholder="搜索 product_code / 中文产品名"' in TEMPLATE
    assert "function filterCampaignProductOptions" in TEMPLATE
    assert "function productSearchText" in TEMPLATE
    assert "<select class=\"op-product-select\"" not in TEMPLATE


def test_order_profit_dashboard_has_incomplete_products_modal():
    expected_snippets = [
        'id="opIncompleteCard"',
        'role="button"',
        'id="opIncompleteModal"',
        'id="opIncompleteProductsList"',
        "openIncompleteProductsModal",
        "/order-profit/api/incomplete_products?",
        "/medias/?q=",
        "当前时间范围",
    ]
    for snippet in expected_snippets:
        assert snippet in TEMPLATE


def test_order_profit_incomplete_products_modal_sanitizes_internal_links():
    modal_block = TEMPLATE[
        TEMPLATE.index("async function openIncompleteProductsModal"):
        TEMPLATE.index("async function refreshOrders")
    ]

    assert "function safeInternalHref(url, fallback)" in TEMPLATE
    assert "const href = safeInternalHref(p.medias_search_url, '/medias/?q=' + encodeURIComponent(p.product_code || ''));" in modal_block
    assert "const href = p.medias_search_url || ('/medias/?q=' + encodeURIComponent(p.product_code || ''));" not in modal_block


def test_order_profit_dashboard_renders_complete_profit_summary_cards():
    expected_labels = [
        "总营收",
        "完整利润",
        "未核算营收",
        "未核算成本",
    ]
    for label in expected_labels:
        assert label in TEMPLATE

    expected_bindings = [
        "opTotalRevenue",
        "opCompleteProfit",
        "opUnaccountedRevenue",
        "opUnaccountedCost",
        "data.total_revenue_usd",
        "data.unaccounted_revenue_usd",
        "data.profit_with_estimate_usd",
        "estimated.purchase_usd",
        "estimated.shipping_cost_usd",
    ]
    for snippet in expected_bindings:
        assert snippet in TEMPLATE
