import json
import base64


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
    assert "username" not in preview
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


def test_build_unsuitable_product_push_preview_uses_text_and_link_types(monkeypatch):
    from appcore import pushes

    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(
        pushes,
        "build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr(
        pushes,
        "get_product_links_target_url",
        lambda: "https://os.wedev.vip/dify/shopify/medias/links",
    )

    def fail_material_target():
        raise AssertionError("unsuitable push must not use material push target")

    monkeypatch.setattr(pushes, "get_push_target_url", fail_material_target)

    preview = pushes.build_unsuitable_product_push_preview({
        "id": 10,
        "name": "Demo",
        "product_code": "demo-rjc",
        "mk_id": 3836,
        "importance": 2,
        "selling_points": "point",
    })

    assert preview["structured"]["type"] == "unsuitable_product"
    assert [item["type"] for item in preview["types"]] == ["copy", "links"]
    copy_type = preview["types"][0]
    link_type = preview["types"][1]
    assert copy_type["label"] == "推送文案"
    assert copy_type["target_url"] == "https://os.wedev.vip/api/marketing/medias/3836/texts"
    assert copy_type["payload"] == {"texts": [{
        "lang": "英语",
        "title": "这个产品有问题，不做，不要投放不要投放不要投放",
        "message": "这个产品有问题，不做，不要投放不要投放不要投放",
        "description": "这个产品有问题，不做，不要投放不要投放不要投放",
    }]}
    assert link_type["label"] == "推送链接"
    assert link_type["target_url"] == "https://os.wedev.vip/dify/shopify/medias/links"
    assert link_type["payload"] == {
        "handle": "demo-rjc",
        "product_links": ["https://newjoyloo.com/products/demo-error-rjc"],
    }
    assert preview["payload"] == {
        "types": [
            {"type": "copy", "payload": copy_type["payload"]},
            {"type": "links", "payload": link_type["payload"]},
        ],
    }


def test_push_unsuitable_product_posts_to_text_and_link_endpoints(monkeypatch):
    from appcore import pushes

    calls = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"code":0,"message":"","data":null}'

        def json(self):
            return {"code": 0, "message": "", "data": None}

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(
        pushes,
        "build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr(pushes, "build_localized_texts_headers", lambda: {
        "Content-Type": "application/json",
        "Authorization": "Bearer token",
    })
    monkeypatch.setattr(pushes, "get_product_links_target_url", lambda: "https://os.wedev.vip/dify/shopify/medias/links")
    monkeypatch.setattr(pushes, "get_product_links_username", lambda: "蔡靖华")
    monkeypatch.setattr(pushes, "get_product_links_password", lambda: "你的密码")
    monkeypatch.setattr(pushes.requests, "post", fake_post)
    monkeypatch.setattr(
        pushes,
        "get_push_target_url",
        lambda: (_ for _ in ()).throw(AssertionError("must not use material push target")),
    )

    result = pushes.push_unsuitable_product({
        "id": 10,
        "name": "Demo",
        "product_code": "demo-rjc",
        "mk_id": 3836,
    })

    assert result["ok"] is True
    assert [item["type"] for item in result["results"]] == ["copy", "links"]
    assert [call["url"] for call in calls] == [
        "https://os.wedev.vip/api/marketing/medias/3836/texts",
        "https://os.wedev.vip/dify/shopify/medias/links",
    ]
    assert calls[0]["json"] == {"texts": [{
        "lang": "英语",
        "title": "这个产品有问题，不做，不要投放不要投放不要投放",
        "message": "这个产品有问题，不做，不要投放不要投放不要投放",
        "description": "这个产品有问题，不做，不要投放不要投放不要投放",
    }]}
    assert calls[1]["json"] == {
        "handle": "demo-rjc",
        "product_links": ["https://newjoyloo.com/products/demo-error-rjc"],
    }


def test_push_unsuitable_product_can_post_only_copy_type(monkeypatch):
    from appcore import pushes

    calls = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "ok"

        def json(self):
            raise ValueError

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes, "build_localized_texts_target_url", lambda mk_id: "https://texts.example.test")
    monkeypatch.setattr(pushes, "build_localized_texts_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(pushes, "get_product_links_target_url", lambda: "https://links.example.test")
    monkeypatch.setattr(pushes, "get_product_links_username", lambda: "user")
    monkeypatch.setattr(pushes, "get_product_links_password", lambda: "pass")
    monkeypatch.setattr(pushes.requests, "post", fake_post)

    result = pushes.push_unsuitable_product({
        "id": 10,
        "product_code": "demo-rjc",
        "mk_id": 3836,
    }, only_type="copy")

    assert result["ok"] is True
    assert [item["type"] for item in result["results"]] == ["copy"]
    assert [call["url"] for call in calls] == ["https://texts.example.test"]


def test_push_unsuitable_product_can_post_only_links_type(monkeypatch):
    from appcore import pushes

    calls = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"code":0}'

        def json(self):
            return {"code": 0}

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes, "build_localized_texts_target_url", lambda mk_id: "https://texts.example.test")
    monkeypatch.setattr(pushes, "build_localized_texts_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(pushes, "get_product_links_target_url", lambda: "https://links.example.test")
    monkeypatch.setattr(pushes, "get_product_links_username", lambda: "user")
    monkeypatch.setattr(pushes, "get_product_links_password", lambda: "pass")
    monkeypatch.setattr(pushes.requests, "post", fake_post)

    result = pushes.push_unsuitable_product({
        "id": 10,
        "product_code": "demo-rjc",
        "mk_id": 3836,
    }, only_type="links")

    assert result["ok"] is True
    assert [item["type"] for item in result["results"]] == ["links"]
    assert [call["url"] for call in calls] == ["https://links.example.test"]


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


def test_push_product_links_posts_strict_payload_with_utf8_basic_auth(monkeypatch):
    from appcore import pushes

    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"code":0,"message":"","data":null}'

        def json(self):
            return {"code": 0, "message": "", "data": None}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes.medias, "list_enabled_language_codes", lambda: ["en", "de"])
    monkeypatch.setattr(pushes.medias, "get_language_name", lambda code: code)
    monkeypatch.setattr(pushes.requests, "post", fake_post)

    def fake_setting(key):
        return {
            "push_product_links_base_url": "https://os.wedev.vip",
            "push_product_links_username": "蔡靖华",
            "push_product_links_password": "你的密码",
        }.get(key, "")

    monkeypatch.setattr(pushes.system_settings, "get_setting", fake_setting)

    result = pushes.push_product_links({
        "id": 10,
        "product_code": "demo-rjc",
        "localized_links_json": json.dumps({
            "de": "https://newjoyloo.com/de/products/demo-rjc",
        }),
    })

    assert result["ok"] is True
    assert captured["url"] == "https://os.wedev.vip/dify/shopify/medias/links"
    assert captured["json"] == {
        "handle": "demo-rjc",
        "product_links": ["https://newjoyloo.com/de/products/demo-rjc"],
    }
    assert set(captured["json"]) == {"handle", "product_links"}
    token = base64.b64encode("蔡靖华:你的密码".encode("utf-8")).decode("ascii")
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": f"Basic {token}",
    }
    assert "auth" not in captured
    assert "payload" not in result
    assert "target_url" not in result


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


def test_medias_unsuitable_product_push_payload_endpoint_returns_preview(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "product_code": "demo-rjc"}
    preview = {
        "target_url": "",
        "structured": {"type": "unsuitable_product"},
        "payload": {"types": []},
        "types": [
            {"type": "copy", "label": "推送文案", "payload": {"texts": [{"lang": "英语"}]}},
            {"type": "links", "label": "推送链接", "payload": {"product_links": ["https://newjoyloo.com/products/demo-error-rjc"]}},
        ],
        "texts": [{"lang": "英语", "title": "T", "message": "M", "description": "D"}],
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_unsuitable_product_push_preview",
        lambda row: preview,
        raising=False,
    )

    resp = authed_client_no_db.get("/medias/api/products/10/product-unsuitable-push/payload")

    assert resp.status_code == 200
    assert resp.get_json() == preview


def test_medias_unsuitable_product_push_endpoint_posts_to_downstream(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "product_code": "demo-rjc"}
    result = {
        "ok": True,
        "results": [
            {"type": "copy", "ok": True, "upstream_status": 200},
            {"type": "links", "ok": True, "upstream_status": 200},
        ],
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.pushes.push_unsuitable_product",
        lambda row: result,
        raising=False,
    )

    resp = authed_client_no_db.post("/medias/api/products/10/product-unsuitable-push")

    assert resp.status_code == 200
    assert resp.get_json() == result


def test_medias_unsuitable_product_push_endpoint_accepts_single_type(
    authed_client_no_db, monkeypatch,
):
    product = {"id": 10, "product_code": "demo-rjc"}
    captured = {}
    result = {
        "ok": True,
        "results": [{"type": "copy", "ok": True}],
    }

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)

    def fake_push(row, only_type=None):
        captured["only_type"] = only_type
        return result

    monkeypatch.setattr(
        "web.routes.medias.pushes.push_unsuitable_product",
        fake_push,
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/10/product-unsuitable-push",
        json={"type": "copy"},
    )

    assert resp.status_code == 200
    assert captured["only_type"] == "copy"
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
    assert "投放链接 JSON 预览" in template
    assert "小语种文案 JSON 预览" in template
    assert "推送用户" not in script
    link_preview_start = script.index("function productLinksPushPreviewJson")
    link_preview_end = script.index("function setProductLinksPushActiveTab")
    assert "request_payload" not in script[link_preview_start:link_preview_end]
    assert "product-links-push/payload" in script
    assert "product-links-push" in script
    assert "product-localized-texts-push/payload" in script
    assert "product-localized-texts-push" in script
    assert "id=\"productLinksPushModalMask\"" in template
    assert "id=\"productCopyPushModalMask\"" in template


def test_medias_assets_include_unsuitable_product_push_entry():
    from pathlib import Path

    template = Path("web/templates/medias_list.html").read_text(encoding="utf-8")
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "data-product-unsuitable-push" in script
    assert "推送不合适" in script
    assert "openProductUnsuitablePushModal" in script
    assert "submitProductUnsuitablePush" in script
    assert "product-unsuitable-push/payload" in script
    assert "product-unsuitable-push" in script
    assert "id=\"productUnsuitablePushModalMask\"" in template
    assert "id=\"productUnsuitablePushInfo\"" in template
    assert "id=\"productUnsuitableCopyJson\"" in template
    assert "id=\"productUnsuitableLinksJson\"" in template
    assert "id=\"productUnsuitableCopySubmit\"" in template
    assert "id=\"productUnsuitableLinksSubmit\"" in template
    assert "function submitProductUnsuitablePushType" in script
    assert "renderProductUnsuitablePushPanel" in script
    assert "oc-unsuitable-modal" in template
    assert "width:min(1770px" in template
    assert "推送文案" in template
    assert "推送链接" in template



def test_medias_product_links_push_modal_uses_tabs_and_centered_footer():
    from pathlib import Path

    template = Path("web/templates/medias_list.html").read_text(encoding="utf-8")
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "oc-product-links-mask" in template
    assert ".oc-product-links-mask" in template
    assert "padding-top:100px" in template
    assert "width:min(1180px" in template
    assert "data-product-links-tab=\"links\"" in template
    assert "data-product-links-tab=\"json\"" in template
    assert "推送投放链接" in template
    assert "投放链接 JSON 预览" in template
    assert "data-product-links-panel=\"links\"" in template
    assert "data-product-links-panel=\"json\"" in template
    assert "id=\"productLinksPushCancel\"" not in template
    assert template.index("id=\"productLinksPushResponse\"") < template.index("id=\"productLinksPushSubmit\"")
    assert "oc-product-links-footer" in template
    assert ".oc-product-links-footer" in template
    assert ".oc-pl-response.success" in template
    assert ".oc-pl-response.danger" in template
    assert ".oc-product-links-active-area {\n  overflow:visible;" in template
    assert "max-height:min(460px, max(240px, calc(100vh - 380px)))" not in template
    assert ".oc-product-links-active-area {\n    max-height:" not in template
    assert ".oc-product-links-modal .oc-modal-body" in template
    assert "overflow-y:auto;" in template
    assert "id=\"productLinksPushInfo\"" in template

    assert "setProductLinksPushActiveTab" in script
    assert "window.setProductLinksPushActiveTab = setProductLinksPushActiveTab" in script
    assert "window.closeProductLinksPushModal = closeProductLinksPushModal" in script
    assert "window.submitProductLinksPush = submitProductLinksPush" in script
    assert "data-product-links-tab" in script
    assert "return JSON.stringify(data.payload || {}, null, 2);" in script
    assert "productLinksPushIsSuccess" in script
    assert "productLinksPushRenderResponse" in script


def test_medias_product_copy_push_modal_matches_links_push_tabs_and_footer():
    from pathlib import Path

    template = Path("web/templates/medias_list.html").read_text(encoding="utf-8")
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert 'id="productCopyPushModalMask" class="oc-modal-mask oc oc-product-links-mask"' in template
    assert 'role="tablist" aria-label="小语种文案推送内容"' in template
    assert 'data-product-copy-tab="texts"' in template
    assert 'data-product-copy-tab="json"' in template
    assert "推送小语种文案" in template
    assert "小语种文案 JSON 预览" in template
    assert 'id="productCopyPushPanelTexts"' in template
    assert 'id="productCopyPushPanelJson"' in template
    assert 'data-product-copy-panel="texts"' in template
    assert 'data-product-copy-panel="json"' in template
    assert 'id="productCopyPushInfo"' in template
    assert 'id="productCopyPushResponseTitle"' in template
    assert 'id="productCopyPushCancel"' not in template
    assert template.index('id="productCopyPushResponse"') < template.index('id="productCopyPushSubmit"')
    assert 'class="oc-modal-foot oc-product-links-footer"' in template
    assert 'id="productCopyPushSubmit" class="oc-btn primary oc-product-links-submit"' in template
    assert 'id="productLinksPushSubmit" class="oc-btn primary oc-product-links-submit"' in template
    assert ".oc-product-links-footer .oc-product-links-submit" in template
    assert "width:256px;" in template
    assert "height:64px;" in template
    assert "font-size:calc(var(--text-base, 14px) * 1.8);" in template
    assert "align-items:center;" in template
    assert "justify-content:center;" in template

    assert "function setProductCopyPushActiveTab" in script
    assert "setProductCopyPushActiveTab('texts')" in script
    assert "window.setProductCopyPushActiveTab = setProductCopyPushActiveTab" in script
    assert "data-product-copy-tab" in script
    assert "data-product-copy-panel" in script
    assert "renderProductCopyPushInfo" in script
    assert "productCopyPushRenderResponse" in script
