from types import SimpleNamespace


def _product(**overrides):
    base = {
        "id": 7,
        "name": "demo",
        "product_code": "demo-rjc",
        "mk_id": None,
        "color_people": None,
        "source": None,
        "ad_supported_langs": "it",
        "archived": 0,
        "created_at": None,
        "updated_at": None,
        "localized_links_json": None,
        "link_check_tasks_json": None,
        "shopify_image_status_json": None,
    }
    base.update(overrides)
    return base


def test_get_product_detail_includes_shopify_image_status(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.medias.get_product",
        lambda pid: _product(
            shopify_image_status_json=(
                '{"it":{"replace_status":"auto_done","link_status":"needs_review"}}'
            ),
        ),
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {})
    monkeypatch.setattr("web.routes.medias.medias.list_copywritings", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_items", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_raw_sources", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_product_skus", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_xmyc_unit_prices", lambda skus: {})
    monkeypatch.setattr(
        "web.services.media_product_detail.product_roas.get_configured_rmb_per_usd",
        lambda: None,
    )

    response = authed_user_client_no_db.get("/medias/api/products/7")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["product"]["shopify_image_status"]["it"]["replace_status"] == "auto_done"
    assert payload["product"]["shopify_image_status"]["it"]["link_status"] == "needs_review"


def test_get_product_detail_includes_enabled_product_link_domains(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.medias.get_product",
        lambda pid: _product(product_code="dino-glider-launcher-toy-rjc"),
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {})
    monkeypatch.setattr("web.routes.medias.medias.list_copywritings", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_items", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_raw_sources", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_product_skus", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_xmyc_unit_prices", lambda skus: {})
    monkeypatch.setattr(
        "web.services.media_product_detail.product_roas.get_configured_rmb_per_usd",
        lambda: None,
    )
    monkeypatch.setattr(
        "appcore.product_link_domains.resolve_product_page_url_rows",
        lambda product, lang: [
            {
                "domain": "newjoyloo.com",
                "lang": "en",
                "status_key": "newjoyloo.com:en",
                "url": "https://newjoyloo.com/products/dino-glider-launcher-toy-rjc",
            },
            {
                "domain": "omurio.com",
                "lang": "en",
                "status_key": "omurio.com:en",
                "url": "https://omurio.com/products/dino-glider-launcher-toy-rjc",
            },
        ],
    )

    response = authed_user_client_no_db.get("/medias/api/products/7")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["product"]["product_link_domains"] == [
        {"domain": "newjoyloo.com"},
        {"domain": "omurio.com"},
    ]


def test_confirm_shopify_image_lang_marks_normal(authed_user_client_no_db, monkeypatch):
    called = {}
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: _product())
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda code: code == "it")
    monkeypatch.setattr(
        "web.routes.medias.shopify_image_tasks.confirm_lang",
        lambda pid, lang, user_id: called.setdefault(
            "status",
            {
                "replace_status": "confirmed",
                "link_status": "normal",
                "confirmed_by": user_id,
            },
        ),
    )

    response = authed_user_client_no_db.post("/medias/api/products/7/shopify-image/it/confirm")
    payload = response.get_json()

    assert response.status_code == 200
    assert called["status"]["confirmed_by"] == 2
    assert payload["status"]["replace_status"] == "confirmed"
    assert payload["status"]["link_status"] == "normal"


def test_requeue_shopify_image_lang_resets_then_creates_task(authed_user_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: _product())
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda code: code == "it")
    monkeypatch.setattr(
        "web.routes.medias.shopify_image_tasks.reset_lang",
        lambda pid, lang: calls.append(("reset", pid, lang)),
    )
    monkeypatch.setattr(
        "web.routes.medias.shopify_image_tasks.create_or_reuse_task",
        lambda pid, lang: {"id": 44, "status": "pending", "product_id": pid, "lang": lang},
    )

    response = authed_user_client_no_db.post("/medias/api/products/7/shopify-image/it/requeue")
    payload = response.get_json()

    assert response.status_code == 202
    assert calls == [("reset", 7, "it")]
    assert payload["task"]["id"] == 44


def test_medias_js_wires_shopify_image_actions():
    js = open("web/static/medias.js", encoding="utf-8").read()

    assert "function edRenderShopifyImageStatus" in js
    assert "function edProductLinkRowsForLang" in js
    assert "product.product_link_domains" in js
    assert "/shopify-image/${encodeURIComponent(lang)}/confirm" in js
    assert "/shopify-image/${encodeURIComponent(lang)}/unavailable" in js
    assert "/shopify-image/${encodeURIComponent(lang)}/requeue" in js


def test_product_links_modal_always_renders_shopify_action_buttons():
    js = open("web/static/medias.js", encoding="utf-8").read()
    row_actions = js[
        js.index("function edProductLinksRowActions"):
        js.index("function edProductLinksRowHtml")
    ]

    assert 'data-product-links-action="shopify-confirm"' in row_actions
    assert 'data-product-links-action="shopify-requeue"' in row_actions
    assert 'data-product-links-action="shopify-unavailable"' in row_actions
    assert "status.replace_status !== 'confirmed'" not in row_actions
    assert "status.link_status !== 'unavailable'" not in row_actions
