from appcore import medias


def _capture_sql(monkeypatch):
    captured: list[tuple[str, tuple]] = []

    def fake_query(sql, params=None):
        captured.append((sql, tuple(params or ())))
        return []

    def fake_query_one(sql, params=None):
        captured.append((sql, tuple(params or ())))
        return {"c": 0}

    monkeypatch.setattr(medias, "query", fake_query)
    monkeypatch.setattr(medias, "query_one", fake_query_one)
    return captured


def _joined(captured):
    return "\n".join(s for s, _ in captured)


def test_list_products_default_filters_skip_removed_match_and_roas(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None)
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text
    assert "purchase_price IS NOT NULL" not in text
    assert "media_product_ad_summary_cache" not in text


def test_list_products_ignores_removed_match_filter_matched(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None)
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text


def test_list_products_ignores_removed_match_filter_unmatched(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None)
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text


def test_list_products_filter_xmyc_invalid_falls_back(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None)
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text


def test_list_products_roas_complete_requires_all_fields(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, roas_status="complete")
    text = _joined(captured)
    assert "p.standalone_price IS NOT NULL" in text
    assert "p.purchase_price IS NOT NULL" in text
    assert "p.packet_cost_estimated IS NOT NULL" in text
    assert "p.packet_cost_actual IS NOT NULL" in text


def test_list_products_roas_missing_estimated(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, roas_status="missing_estimated")
    text = _joined(captured)
    assert "p.packet_cost_estimated IS NULL" in text
    assert "p.packet_cost_actual IS NULL" not in text


def test_list_products_roas_missing_actual(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, roas_status="missing_actual")
    text = _joined(captured)
    assert "p.packet_cost_actual IS NULL" in text
    assert "p.packet_cost_estimated IS NULL" not in text


def test_list_products_roas_filter_still_applies_with_removed_match_param(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, roas_status="complete")
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text
    assert "p.packet_cost_estimated IS NOT NULL" in text


def test_list_products_filters_delivery_status(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, delivery_status="active")
    text = _joined(captured)
    assert "media_product_ad_summary_cache" in text
    assert "delivery_status=%s" in text
    assert captured[-1][1][-3] == "active"


def test_list_products_filters_stability_status(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, stability_status="secondary_stable")
    text = _joined(captured)
    assert "media_product_stability_snapshots" in text
    assert "stab.status=%s" in text
    assert "secondary_stable" in captured[-1][1]


def test_list_products_filters_stable_marks(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, stability_status="stable_7d")
    text = _joined(captured)
    assert "media_product_stability_snapshots" in text
    assert "stab.status=%s AND stab.stable_7d=1" in text
    assert "stable" in captured[-1][1]


def test_list_products_stability_status_invalid_falls_back(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, stability_status="junk")
    text = _joined(captured)
    assert "media_product_stability_snapshots" not in text


def test_list_products_delivery_status_invalid_falls_back(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, delivery_status="paused")
    text = _joined(captured)
    assert "media_product_ad_summary_cache" not in text


def test_list_products_filters_created_at(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, created_from="2026-06-01", created_to="2026-06-03")
    text = _joined(captured)
    assert "p.created_at >= %s" in text
    assert "p.created_at <= %s" in text
    params = captured[1][1]
    assert "2026-06-01 00:00:00" in params
    assert "2026-06-03 23:59:59" in params


def test_api_list_products_passes_filters(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_products(user_id, *, keyword="", archived=False, offset=0, limit=20,
                           roas_status="all", delivery_status="all", **kwargs):
        captured["roas_status"] = roas_status
        captured["delivery_status"] = delivery_status
        captured["stability_status"] = kwargs.get("stability_status")
        captured["keyword"] = keyword
        captured["created_from"] = kwargs.get("created_from")
        captured["created_to"] = kwargs.get("created_to")
        return [], 0

    monkeypatch.setattr(medias, "list_products", fake_list_products)
    monkeypatch.setattr(medias, "count_items_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "count_raw_sources_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "list_item_filenames_by_product", lambda pids, limit_per=5: {})
    monkeypatch.setattr(medias, "lang_coverage_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "get_product_covers_batch", lambda pids: {})

    resp = authed_client_no_db.get(
        "/medias/api/products?roas_status=missing_actual&delivery_status=stopped&stability_status=stable_30d&keyword=foo&created_from=2026-06-01&created_to=2026-06-03"
    )
    assert resp.status_code == 200
    assert captured["roas_status"] == "missing_actual"
    assert captured["delivery_status"] == "stopped"
    assert captured["stability_status"] == "stable_30d"
    assert captured["keyword"] == "foo"
    assert captured["created_from"] == "2026-06-01"
    assert captured["created_to"] == "2026-06-03"


def test_api_list_products_normalizes_invalid_filter_values(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_products(user_id, *, keyword="", archived=False, offset=0, limit=20,
                           roas_status="all", delivery_status="all", **kwargs):
        captured["roas_status"] = roas_status
        captured["delivery_status"] = delivery_status
        captured["stability_status"] = kwargs.get("stability_status")
        return [], 0

    monkeypatch.setattr(medias, "list_products", fake_list_products)
    monkeypatch.setattr(medias, "count_items_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "count_raw_sources_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "list_item_filenames_by_product", lambda pids, limit_per=5: {})
    monkeypatch.setattr(medias, "lang_coverage_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "get_product_covers_batch", lambda pids: {})

    resp = authed_client_no_db.get("/medias/api/products?roas_status=junk&delivery_status=paused&stability_status=bad")
    assert resp.status_code == 200
    assert captured["roas_status"] == "all"
    assert captured["delivery_status"] == "all"
    assert captured["stability_status"] == "all"


def test_products_list_response_includes_product_stability_cache():
    from web.services.media_products_listing import build_products_list_response

    row = {
        "id": 9,
        "name": "Stable Product",
        "product_code": "stable-product-rjc",
        "created_at": None,
        "updated_at": None,
    }

    payload = build_products_list_response(
        {"page": "1"},
        list_products_fn=lambda *a, **k: ([row], 1),
        count_items_by_product_fn=lambda pids: {9: 3},
        count_raw_sources_by_product_fn=lambda pids: {},
        first_thumb_item_by_product_fn=lambda pids: {},
        list_item_filenames_by_product_fn=lambda pids, limit_per=5: {},
        lang_coverage_by_product_fn=lambda pids: {},
        get_product_covers_batch_fn=lambda pids: {},
        list_product_skus_batch_fn=lambda pids: {},
        list_yuncang_unit_prices_fn=lambda skus: {},
        get_latest_sku_actual_roas_fn=lambda skus: {},
        get_configured_rmb_per_usd_fn=lambda: 7.2,
        get_product_ad_summary_cache_fn=lambda pids: {},
        get_product_lang_ad_summary_cache_fn=lambda pids: {},
        get_product_order_stats_fn=lambda pids: {},
        get_product_stability_cache_fn=lambda pids: {
            9: {"status": "stable", "stable_marks": ["7天稳定"]}
        },
        serialize_product_fn=lambda p, *args, **kwargs: {
            "id": p["id"],
            "stability": kwargs.get("stability"),
        },
    )

    assert payload["items"][0]["stability"] == {"status": "stable", "stable_marks": ["7天稳定"]}



def test_medias_list_html_has_filter_dropdowns():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="filterXmycMatch"' not in html
    assert 'id="filterRoasStatus"' in html
    assert 'id="filterDeliveryStatus"' in html
    assert 'id="filterStabilityStatus"' in html
    assert "投放中" in html
    assert "终止投放" in html
    assert "未投" in html
    assert "稳定分级：全部" in html
    assert "二级稳定品" in html
    assert "投放未满7天" in html
    assert "数据已完成" in html
    assert "缺失（预估）" in html
    assert "缺失（实际）" in html

    assert "filterXmycMatch" not in js
    assert "filterRoasStatus" in js
    assert "filterDeliveryStatus" in js
    assert "filterStabilityStatus" in js
    assert "xmyc_match" not in js
    assert "roas_status" in js
    assert "delivery_status" in js
    assert "stability_status" in js
    assert "oc-delivery-pill" in js
    assert "oc-lang-push-zero" in js
    assert "总体ROAS" in js


def test_medias_toolbar_compacts_actions_and_filters():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    js = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    action_start = html.index('<div class="oc-header-action-buttons">')
    action_end = html.index('<nav class="oc-page-tabs"', action_start)
    action_block = html[action_start:action_end]
    assert 'id="createBtn"' in action_block
    assert "oc-tool-download-btn" in action_block

    assert ".oc-toolbar-filter-row { display:grid; grid-template-columns:minmax(180px,1.45fr) repeat(4,minmax(132px,1fr)) minmax(160px,1.15fr) minmax(150px,1.15fr);" in html
    mobile_start = html.index("@media (max-width: 760px)")
    mobile_end = html.index("/* ────────── Buttons", mobile_start)
    mobile_block = html[mobile_start:mobile_end]
    assert ".oc-header-actions { width:100%; min-width:0; align-items:stretch; flex:0 0 auto; }" in mobile_block
    assert 'id="searchBtn"' not in html
    assert "<span>搜索</span>" not in html[html.index("<!-- Toolbar -->"):html.index("<!-- List -->")]

    events_start = js.index("const searchBtn = $('searchBtn');")
    events_end = js.index("const filterRoas", events_start)
    events_block = js[events_start:events_end]
    assert "if (searchBtn) searchBtn.addEventListener('click', () => runSearchNow({ syncUrl: true }));" in events_block
    assert "kwInput.addEventListener('input', scheduleLiveSearch);" in events_block
    assert "if (searchBtn && kwInput)" not in events_block


def test_medias_mobile_adaptation_keeps_tables_scrollable_and_aligned():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "docs/superpowers/specs/2026-06-07-medias-mobile-adaptation-design.md" in html
    assert "height: 100dvh !important;" in html
    assert "env(safe-area-inset-bottom,0px)" in html

    anchor = html.index("docs/superpowers/specs/2026-06-07-medias-mobile-adaptation-design.md")
    mobile_start = html.index("@media (max-width: 640px)", anchor)
    mobile_block = html[mobile_start:html.index(".oc-page-tabs {", mobile_start)]

    assert "overflow-x:auto !important;" in mobile_block
    assert "width:2176px !important;" in mobile_block
    assert "width:2120px !important;" in mobile_block
    assert ".oc-table-medias thead,\n  .oc-vm-table thead {\n    display:table-header-group;" in mobile_block
    assert ".oc-table-medias tbody,\n  .oc-vm-table tbody {\n    display:table-row-group;" in mobile_block
    assert ".oc-table-medias tr,\n  .oc-vm-table tr {\n    display:table-row;" in mobile_block
    assert ".oc-table-medias th,\n  .oc-table-medias td,\n  .oc-vm-table th,\n  .oc-vm-table td {\n    display:table-cell;" in mobile_block

    assert "grid-template-areas:" not in mobile_block
    assert "display:none;" not in mobile_block[mobile_block.index(".oc-table-medias thead,"):]
    assert "::before { content:" not in mobile_block


def test_medias_mobile_filter_collapse_controls_cover_both_tabs():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert 'id="mediaProductFilters"' in html
    assert 'id="videoMaterialFilters"' in html
    assert 'aria-controls="mediaProductFilters"' in html
    assert 'aria-controls="videoMaterialFilters"' in html
    assert html.count('data-mobile-filter-toggle') >= 2
    assert html.count('data-mobile-filter-toolbar') >= 2
    assert '<use href="#ic-filter"/>' in html
    assert ".oc-mobile-filter-toggle {\n  display:none;" in html
    assert ".oc-mobile-filter-toggle { display:flex; }" in html
    assert ".oc-toolbar.is-filter-collapsed .oc-toolbar-filter-row" in html
    assert ".oc-vm-toolbar.is-filter-collapsed .oc-vm-filter-row" in html
    assert ".oc-vm-filter-row {\n  display:grid;" in html


def test_medias_mobile_filter_auto_collapses_on_list_scroll():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "docs/superpowers/specs/2026-06-07-medias-mobile-filter-collapse-design.md" in html
    assert "var mobileFilterMql = window.matchMedia ? window.matchMedia('(max-width: 768px)') : null;" in html
    assert "document.querySelectorAll('.oc-panel-page .oc-list').forEach(function(list)" in html
    assert "list.addEventListener('scroll', function()" in html
    assert "currentTop > 24 && currentTop > previousTop + 12" in html
    assert "setMobileFilterCollapsed(activeMobileFilterToolbar(), true);" in html
    assert "toolbar.classList.remove('is-filter-collapsed')" in html
    assert "window.addEventListener('resize', syncMobileFilterViewport);" in html
