from __future__ import annotations


def test_build_supply_pairing_search_response_enriches_items_with_1688_urls():
    from web.services.media_supply_pairing import build_supply_pairing_search_response

    captured = {}

    def fake_search(query, *, status):
        captured["query"] = query
        captured["status"] = status
        return {
            "items": [
                {"id": "1", "sourceUrl": "https://detail.1688.com/offer/111.html"},
                {"id": "2", "alibabaProductId": "222"},
                {"id": "3", "sourceUrl": "https://example.test/not-1688"},
            ],
            "query": query,
            "total": 3,
        }

    def fake_extract(item):
        if item["id"] == "1":
            return item["sourceUrl"]
        if item["id"] == "2":
            return "https://detail.1688.com/offer/222.html"
        return "https://example.test/not-1688"

    result = build_supply_pairing_search_response(
        {"q": " SKU-1 "},
        search_supply_pairing_fn=fake_search,
        extract_1688_url_fn=fake_extract,
    )

    assert result.status_code == 200
    assert result.payload["ok"] is True
    assert result.payload["total"] == 3
    assert result.payload["items"][0]["extracted_1688_url"].endswith("/111.html")
    assert result.payload["items"][1]["extracted_1688_url"].endswith("/222.html")
    assert result.payload["items"][2]["extracted_1688_url"] is None
    assert captured == {"query": "SKU-1", "status": ""}


def test_build_supply_pairing_search_response_uses_explicit_status():
    from web.services.media_supply_pairing import build_supply_pairing_search_response

    captured = {}

    result = build_supply_pairing_search_response(
        {"q": "sku", "status": "2"},
        search_supply_pairing_fn=lambda query, *, status: captured.update(status=status) or {"items": []},
        extract_1688_url_fn=lambda item: None,
    )

    assert result.status_code == 200
    assert captured == {"status": "2"}


def test_build_supply_pairing_search_response_rejects_missing_query_before_search():
    from web.services.media_supply_pairing import build_supply_pairing_search_response

    called = []

    result = build_supply_pairing_search_response(
        {"q": "   "},
        search_supply_pairing_fn=lambda *args, **kwargs: called.append("search"),
        extract_1688_url_fn=lambda item: None,
    )

    assert result.status_code == 400
    assert result.payload["error"] == "missing_query"
    assert called == []


def test_build_supply_pairing_search_response_maps_dxm_errors():
    from web.services.media_supply_pairing import build_supply_pairing_search_response

    def raise_error(query, *, status):
        raise RuntimeError("dxm unreachable")

    result = build_supply_pairing_search_response(
        {"q": "sku"},
        search_supply_pairing_fn=raise_error,
        extract_1688_url_fn=lambda item: None,
    )

    assert result.status_code == 502
    assert result.payload == {"error": "dxm_failed", "message": "dxm unreachable"}
