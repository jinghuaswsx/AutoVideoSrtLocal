from __future__ import annotations


def test_mk_detail_proxy_uses_server_side_wedev_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"item": {"id": 3719, "videos": []}}}

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

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get("/medias/api/mk-detail/3719")

    assert response.status_code == 200
    assert response.get_json() == {"data": {"item": {"id": 3719, "videos": []}}}
    assert captured["url"] == "https://wedev.example/api/marketing/medias/3719"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["timeout"] == 15
