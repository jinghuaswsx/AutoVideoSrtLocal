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

    response = authed_user_client_no_db.get("/medias/api/products/7")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["product"]["shopify_image_status"]["it"]["replace_status"] == "auto_done"
    assert payload["product"]["shopify_image_status"]["it"]["link_status"] == "needs_review"


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
    assert "/shopify-image/${encodeURIComponent(lang)}/confirm" in js
    assert "/shopify-image/${encodeURIComponent(lang)}/unavailable" in js
    assert "/shopify-image/${encodeURIComponent(lang)}/requeue" in js
