from __future__ import annotations


def test_medias_product_link_domains_payload_endpoint_returns_options(
    authed_client_no_db, monkeypatch
):
    product = {"id": 10, "product_code": "demo-rjc"}
    options = [
        {
            "id": 1,
            "domain": "newjoyloo.com",
            "enabled": True,
            "product_enabled": True,
            "effective_enabled": True,
        },
        {
            "id": 2,
            "domain": "omurio.com",
            "enabled": True,
            "product_enabled": False,
            "effective_enabled": False,
        },
    ]

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.list_product_domain_options",
        lambda product_id: options,
        raising=False,
    )

    resp = authed_client_no_db.get("/medias/api/products/10/product-link-domains")

    assert resp.status_code == 200
    assert resp.get_json() == {"product": product, "domains": options}


def test_medias_product_link_domains_post_saves_enabled_ids(
    authed_client_no_db, monkeypatch
):
    product = {"id": 10, "product_code": "demo-rjc"}
    captured = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.set_product_domain_enabled_ids",
        lambda product_id, ids: captured.update({"product_id": product_id, "ids": ids}),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.list_product_domain_options",
        lambda product_id: [{"id": 2, "domain": "omurio.com", "product_enabled": True}],
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.product_link_domains.resolve_product_page_url_rows",
        lambda p, lang: [{"domain": "omurio.com", "url": "https://omurio.com/products/demo-rjc"}],
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.mk_import._probe_product_link",
        lambda url: (True, "探测通过"),
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/10/product-link-domains",
        json={"enabled_domain_ids": [2, "3", "bad", 0]},
    )

    assert resp.status_code == 200
    assert captured == {"product_id": 10, "ids": [2, 3]}
    json_data = resp.get_json()
    assert json_data["domains"] == [
        {"id": 2, "domain": "omurio.com", "product_enabled": True}
    ]
    assert json_data["probe_results"] == [
        {
            "key": "domain_link_probe_omurio.com",
            "title": "发布域名链接探测 (omurio.com)",
            "status": "done",
            "message": "商品链接探测通过",
            "logs": ["https://omurio.com/products/demo-rjc", "探测通过"],
        }
    ]


def test_medias_product_link_domains_post_allows_empty_enabled_ids(
    authed_client_no_db, monkeypatch
):
    product = {"id": 10, "product_code": "demo-rjc"}
    captured = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.set_product_domain_enabled_ids",
        lambda product_id, ids: captured.update({"product_id": product_id, "ids": ids}),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.list_product_domain_options",
        lambda product_id: [],
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.product_link_domains.resolve_product_page_url_rows",
        lambda p, lang: [],
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/10/product-link-domains",
        json={"enabled_domain_ids": []},
    )


    assert resp.status_code == 200
    assert captured == {"product_id": 10, "ids": []}
    assert resp.get_json() == {"ok": True, "domains": [], "probe_results": []}

