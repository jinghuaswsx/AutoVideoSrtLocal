from __future__ import annotations

from pathlib import Path


def test_mk_selection_video_cover_uses_portrait_thumb_frame():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "--mk-video-cover-w: 90px;" in template
    assert "--mk-video-cover-h: 160px;" in template
    assert "mk-video-cover-frame" in template


def test_mk_media_proxy_fetches_wedev_media_with_server_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"image-bytes"
        headers = {"content-type": "image/jpeg"}

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

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 200
    assert response.data == b"image-bytes"
    assert response.content_type == "image/jpeg"
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.jpg"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "image/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 20


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
