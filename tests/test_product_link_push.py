import json


def test_build_product_links_push_preview_uses_enabled_media_languages(monkeypatch):
    from appcore import pushes

    monkeypatch.setattr(
        pushes.medias,
        "list_enabled_language_codes",
        lambda: ["en", "de", "fr", "ja"],
    )
    monkeypatch.setattr(pushes.medias, "get_language_name", lambda code: code)
    monkeypatch.setattr(pushes.system_settings, "get_setting", lambda key: None)
    product = {
        "id": 10,
        "product_code": "demo-rjc",
        "localized_links_json": json.dumps({
            "de": "https://newjoyloo.com/de/products/demo-rjc-special",
        }),
    }

    preview = pushes.build_product_links_push_preview(product)

    assert preview["target_url"] == "https://os.wedev.vip/dify/shopify/medias/links"
    assert preview["username"] == "蔡靖华"
    assert preview["payload"] == {
        "handle": "demo-rjc",
        "product_links": [
            "https://newjoyloo.com/de/products/demo-rjc-special",
            "https://newjoyloo.com/fr/products/demo-rjc",
            "https://newjoyloo.com/ja/products/demo-rjc",
        ],
    }
    assert preview["links"][0] == {
        "lang": "de",
        "language_name": "de",
        "url": "https://newjoyloo.com/de/products/demo-rjc-special",
    }


def test_medias_product_links_push_payload_endpoint_returns_preview(
    authed_client_no_db, monkeypatch,
):
    product = {
        "id": 10,
        "product_code": "demo-rjc",
        "localized_links_json": "{}",
    }
    preview = {
        "target_url": "https://os.wedev.vip/dify/shopify/medias/links",
        "username": "蔡靖华",
        "payload": {
            "handle": "demo-rjc",
            "product_links": ["https://newjoyloo.com/de/products/demo-rjc"],
        },
        "links": [
            {
                "lang": "de",
                "language_name": "德语",
                "url": "https://newjoyloo.com/de/products/demo-rjc",
            },
        ],
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_product_links_push_preview",
        lambda row: preview,
        raising=False,
    )

    resp = authed_client_no_db.get("/medias/api/products/10/product-links-push/payload")

    assert resp.status_code == 200
    assert resp.get_json() == preview


def test_medias_product_links_push_endpoint_posts_to_downstream(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "product_code": "demo-rjc"}
    result = {
        "ok": True,
        "upstream_status": 200,
        "response_body": '{"code":0,"message":"","data":null}',
        "target_url": "https://os.wedev.vip/dify/shopify/medias/links",
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.push_product_links",
        lambda row: result,
        raising=False,
    )

    resp = authed_client_no_db.post("/medias/api/products/10/product-links-push")

    assert resp.status_code == 200
    assert resp.get_json() == result


def test_build_product_localized_texts_push_preview_reuses_product_texts(monkeypatch):
    from appcore import pushes

    texts = [
        {
            "title": "Titel",
            "message": "Text",
            "description": "Beschreibung",
            "lang": "德语",
        },
    ]

    monkeypatch.setattr(
        pushes,
        "resolve_localized_texts_payload",
        lambda item: texts if item == {"product_id": 10} else [],
    )
    monkeypatch.setattr(
        pushes,
        "build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )

    preview = pushes.build_product_localized_texts_push_preview({
        "id": 10,
        "mk_id": 3836,
        "listing_status": "上架",
    })

    assert preview == {
        "mk_id": 3836,
        "target_url": "https://os.wedev.vip/api/marketing/medias/3836/texts",
        "payload": {"texts": texts},
        "texts": texts,
    }


def test_medias_product_localized_texts_push_payload_endpoint_returns_preview(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "mk_id": 3836}
    preview = {
        "mk_id": 3836,
        "target_url": "https://os.wedev.vip/api/marketing/medias/3836/texts",
        "payload": {"texts": [{"lang": "德语", "title": "T", "message": "M", "description": "D"}]},
        "texts": [{"lang": "德语", "title": "T", "message": "M", "description": "D"}],
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_product_localized_texts_push_preview",
        lambda row: preview,
        raising=False,
    )

    resp = authed_client_no_db.get("/medias/api/products/10/product-localized-texts-push/payload")

    assert resp.status_code == 200
    assert resp.get_json() == preview


def test_medias_product_localized_texts_push_endpoint_posts_to_downstream(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "mk_id": 3836}
    result = {
        "ok": True,
        "upstream_status": 200,
        "response_body": "ok",
        "target_url": "https://os.wedev.vip/api/marketing/medias/3836/texts",
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.push_product_localized_texts",
        lambda row: result,
        raising=False,
    )

    resp = authed_client_no_db.post("/medias/api/products/10/product-localized-texts-push")

    assert resp.status_code == 200
    assert resp.get_json() == result


def test_medias_assets_include_product_link_push_entry():
    from pathlib import Path

    template = Path("web/templates/medias_list.html").read_text(encoding="utf-8")
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "<th>投放推送</th>" in script
    assert "<th>投放链接</th>" not in script
    assert "data-product-links-push" in script
    assert "data-product-copy-push" in script
    assert "推送链接" in script
    assert "推送文案" in script
    assert "openProductLinksPushModal" in script
    assert "openProductCopyPushModal" in script
    assert "产品链接 JSON 预览" in template
    assert "小语种文案 JSON 预览" in template
    assert "推送用户" in script
    assert "product-links-push/payload" in script
    assert "product-links-push" in script
    assert "product-localized-texts-push/payload" in script
    assert "product-localized-texts-push" in script
    assert "id=\"productLinksPushModalMask\"" in template
    assert "id=\"productCopyPushModalMask\"" in template
