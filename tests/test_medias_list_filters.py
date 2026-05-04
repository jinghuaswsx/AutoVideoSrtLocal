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


def test_list_products_default_filters_skip_xmyc_and_roas(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None)
    text = _joined(captured)
    assert "xmyc_storage_skus" not in text
    assert "purchase_price IS NOT NULL" not in text


def test_list_products_filter_xmyc_matched(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, xmyc_match="matched")
    text = _joined(captured)
    assert "EXISTS (SELECT 1 FROM xmyc_storage_skus" in text
    assert "NOT EXISTS" not in text


def test_list_products_filter_xmyc_unmatched(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, xmyc_match="unmatched")
    text = _joined(captured)
    assert "NOT EXISTS (SELECT 1 FROM xmyc_storage_skus" in text


def test_list_products_filter_xmyc_invalid_falls_back(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, xmyc_match="bogus")
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


def test_list_products_combines_xmyc_and_roas_filters(monkeypatch):
    captured = _capture_sql(monkeypatch)
    medias.list_products(None, xmyc_match="unmatched", roas_status="complete")
    text = _joined(captured)
    assert "NOT EXISTS (SELECT 1 FROM xmyc_storage_skus" in text
    assert "p.packet_cost_estimated IS NOT NULL" in text


def test_api_list_products_passes_filters(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_products(user_id, *, keyword="", archived=False, offset=0, limit=20,
                           xmyc_match="all", roas_status="all"):
        captured["xmyc_match"] = xmyc_match
        captured["roas_status"] = roas_status
        captured["keyword"] = keyword
        return [], 0

    monkeypatch.setattr(medias, "list_products", fake_list_products)
    monkeypatch.setattr(medias, "count_items_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "count_raw_sources_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "list_item_filenames_by_product", lambda pids, limit_per=5: {})
    monkeypatch.setattr(medias, "lang_coverage_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "get_product_covers_batch", lambda pids: {})

    resp = authed_client_no_db.get(
        "/medias/api/products?xmyc_match=unmatched&roas_status=missing_actual&keyword=foo"
    )
    assert resp.status_code == 200
    assert captured["xmyc_match"] == "unmatched"
    assert captured["roas_status"] == "missing_actual"
    assert captured["keyword"] == "foo"


def test_api_list_products_normalizes_invalid_filter_values(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_products(user_id, *, keyword="", archived=False, offset=0, limit=20,
                           xmyc_match="all", roas_status="all"):
        captured["xmyc_match"] = xmyc_match
        captured["roas_status"] = roas_status
        return [], 0

    monkeypatch.setattr(medias, "list_products", fake_list_products)
    monkeypatch.setattr(medias, "count_items_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "count_raw_sources_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "list_item_filenames_by_product", lambda pids, limit_per=5: {})
    monkeypatch.setattr(medias, "lang_coverage_by_product", lambda pids: {})
    monkeypatch.setattr(medias, "get_product_covers_batch", lambda pids: {})

    resp = authed_client_no_db.get("/medias/api/products?xmyc_match=garbage&roas_status=junk")
    assert resp.status_code == 200
    assert captured["xmyc_match"] == "all"
    assert captured["roas_status"] == "all"


def test_medias_list_html_has_filter_dropdowns():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="filterXmycMatch"' in html
    assert 'id="filterRoasStatus"' in html
    assert "已配对" in html
    assert "未配对" in html
    assert "数据已完成" in html
    assert "缺失（预估）" in html
    assert "缺失（实际）" in html

    assert "filterXmycMatch" in js
    assert "filterRoasStatus" in js
    assert "xmyc_match" in js
    assert "roas_status" in js
