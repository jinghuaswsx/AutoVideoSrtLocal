from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mk_copywriting_fetch_strips_rjc_and_matches_first_product_link(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "id": 3725,
                            "product_links": [
                                "https://newjoyloo.com/products/not-the-requested-product",
                                "https://newjoyloo.com/products/dino-glider-launcher-toy",
                            ],
                            "texts": [{
                                "title": "Wrong title",
                                "message": "Wrong message",
                                "description": "Wrong description",
                            }],
                        },
                        {
                            "id": 2603,
                            "product_links": [
                                "https://newjoyloo.com/products/dino-glider-launcher-toy",
                            ],
                            "texts": [{
                                "title": "Ready. Aim. LAUNCH! 🌪️",
                                "message": "Experience the thrill! 🤩 Instant mechanical launch.",
                                "description": "Fly High Today ✈️",
                            }],
                        },
                    ],
                },
            }

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-copywriting?product_code=dino-glider-launcher-toy-RJC"
    )

    assert response.status_code == 200
    assert captured["url"] == "https://wedev.example/api/marketing/medias"
    assert captured["params"] == {
        "page": 1,
        "q": "dino-glider-launcher-toy",
        "source": "",
        "level": "",
        "show_attention": 0,
    }
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "application/json"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 15

    payload = response.get_json()
    assert payload["source_item_id"] == 2603
    assert payload["query"] == "dino-glider-launcher-toy"
    assert payload["copywriting"] == (
        "标题: Ready. Aim. LAUNCH! 🌪️\n"
        "文案: Experience the thrill! 🤩 Instant mechanical launch.\n"
        "描述: Fly High Today ✈️"
    )


def test_mk_copywriting_fetch_reports_expired_wedev_credentials(
    authed_client_no_db,
    monkeypatch,
):
    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {"is_guest": True, "message": "登录已失效"}

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {"Authorization": "Bearer synced-token"},
    )
    monkeypatch.setattr(
        "web.routes.medias.requests.get",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    response = authed_client_no_db.get(
        "/medias/api/mk-copywriting?product_code=dino-glider-launcher-toy"
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "mk_credentials_expired"


def test_add_material_modal_has_mk_copywriting_fetch_button():
    html = (ROOT / "web" / "templates" / "_medias_edit_modal.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="mkCopyFetchBtn"' in html
    assert "一键从明空后台获取英文文案" in html
    assert "/medias/api/mk-copywriting" in script
    assert "fillCopywritingFromMkSystem" in script


def test_edit_material_modal_has_en_mk_copywriting_fetch_button():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edCwTranslateSlot"' in html
    assert "edMkCopyFetchBtn" in script
    assert "edFillCopywritingFromMkSystem" in script
    assert "一键从明空后台获取英文文案" in script
    assert "/medias/api/mk-copywriting" in script
