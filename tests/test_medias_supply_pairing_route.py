from __future__ import annotations


def test_supply_pairing_search_400_when_q_missing(authed_client_no_db):
    resp = authed_client_no_db.get("/medias/api/supply-pairing/search")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "missing_query"


def test_supply_pairing_search_enriches_items_with_extracted_1688_url(
    authed_client_no_db, monkeypatch
):
    """The route must call extract_1688_url() and expose its result on every
    item, so the frontend can pick a 1688 link without re-implementing the
    sourceUrl -> alibabaProductId fallback."""
    captured = {}

    def fake_search(query, *, status, **kwargs):
        captured["query"] = query
        captured["status"] = status
        return {
            "items": [
                # paired item — sourceUrl is already 1688
                {
                    "id": "1",
                    "sku": "S1",
                    "name": "已配对A",
                    "sourceUrl": "https://detail.1688.com/offer/111.html?spm=foo",
                    "alibabaProductId": "111",
                },
                # waiting-list item — only alibabaProductId, sourceUrl null.
                # This is the case extract_1688_url's fallback unlocks.
                {
                    "id": "2",
                    "sku": "S2",
                    "name": "待配对B",
                    "sourceUrl": None,
                    "alibabaProductId": "222",
                },
                # waiting-list item with a non-1688 sourceUrl (Amazon)
                # but an alibabaProductId — should still extract 1688 link.
                {
                    "id": "3",
                    "sku": "S3",
                    "name": "亚马逊来源C",
                    "sourceUrl": "https://www.amazon.com/dp/B0DFCRMNCZ",
                    "alibabaProductId": "333",
                },
                # nothing usable
                {
                    "id": "4",
                    "sku": "S4",
                    "name": "无候选D",
                    "sourceUrl": None,
                    "alibabaProductId": None,
                },
            ],
            "query": query,
            "search_type_used": "1",
            "total": 4,
        }

    from web.routes.medias import products as products_route

    monkeypatch.setattr(
        products_route.supply_pairing, "search_supply_pairing", fake_search
    )

    resp = authed_client_no_db.get(
        "/medias/api/supply-pairing/search?q=ABC123"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    items = body["items"]
    assert len(items) == 4

    # 1: existing 1688 sourceUrl is preserved with its spm tracking
    assert items[0]["extracted_1688_url"].startswith(
        "https://detail.1688.com/offer/111.html"
    )
    # 2: alibabaProductId fallback constructs a clean offer URL
    assert items[1]["extracted_1688_url"] == (
        "https://detail.1688.com/offer/222.html"
    )
    # 3: even when sourceUrl is Amazon, alibabaProductId wins for the
    # 1688 link — caller wants a 1688 URL, not a generic source URL.
    assert items[2]["extracted_1688_url"] == (
        "https://detail.1688.com/offer/333.html"
    )
    # 4: nothing usable -> None (not the original sourceUrl)
    assert items[3]["extracted_1688_url"] is None

    # The default status must span both waiting (status=1) and paired
    # (status=2) records — pinning status="0" or "2" would silently drop
    # the ~337 waiting-list items on the MKTT account.
    assert captured["status"] == ""
    assert captured["query"] == "ABC123"


def test_supply_pairing_search_respects_explicit_status(
    authed_client_no_db, monkeypatch
):
    captured = {}

    def fake_search(query, *, status, **kwargs):
        captured["status"] = status
        return {"items": [], "query": query, "search_type_used": "1", "total": 0}

    from web.routes.medias import products as products_route

    monkeypatch.setattr(
        products_route.supply_pairing, "search_supply_pairing", fake_search
    )

    resp = authed_client_no_db.get(
        "/medias/api/supply-pairing/search?q=foo&status=2"
    )
    assert resp.status_code == 200
    assert captured["status"] == "2"


def test_supply_pairing_search_502_when_dxm_fails(authed_client_no_db, monkeypatch):
    def fake_search(query, *, status, **kwargs):
        raise RuntimeError("dxm unreachable")

    from web.routes.medias import products as products_route

    monkeypatch.setattr(
        products_route.supply_pairing, "search_supply_pairing", fake_search
    )

    resp = authed_client_no_db.get(
        "/medias/api/supply-pairing/search?q=foo"
    )
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["error"] == "dxm_failed"


def test_supply_pairing_search_route_delegates_response_building(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class Result:
        payload = {"ok": True, "items": [{"id": "1"}]}
        status_code = 203

    def fake_build(args):
        captured["q"] = args.get("q")
        captured["status"] = args.get("status")
        return Result()

    monkeypatch.setattr("web.routes.medias._build_supply_pairing_search_response", fake_build)

    resp = authed_client_no_db.get("/medias/api/supply-pairing/search?q=sku&status=2")

    assert resp.status_code == 203
    assert resp.get_json() == {"ok": True, "items": [{"id": "1"}]}
    assert captured == {"q": "sku", "status": "2"}
