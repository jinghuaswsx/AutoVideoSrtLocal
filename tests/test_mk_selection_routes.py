from __future__ import annotations

from pathlib import Path


def test_mk_selection_video_cards_use_single_preview_with_metrics():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "--mk-video-media-w:" in template
    assert "--mk-video-media-h:" in template
    assert "repeat(auto-fill, minmax(248px, 248px))" in template
    assert "mk-video-card-title" in template
    assert "mk-video-summary-row" in template
    assert "mk-video-tabs" in template
    assert "mk-video-frame" in template
    assert "mk-video-cover-frame" in template
    assert "mk-video-source-frame" not in template
    assert "mk-video-media-frame" not in template
    assert "投放热度" in template
    assert "90天消耗" in template


def test_mk_selection_video_cards_include_local_video_preview():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "mk-video-source" in template
    assert "data-mk-video-src" in template
    assert "activateMkVideoTab" in template
    assert "/medias/api/mk-video?path=" in template
    assert "controls" in template
    assert "loading=\"lazy\"" in template


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


def test_mk_video_proxy_caches_wedev_video_for_local_preview(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from appcore import local_media_storage

    captured = {"calls": 0}
    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(payload))}

        @staticmethod
        def iter_content(chunk_size=1024 * 1024):
            del chunk_size
            yield payload[:10]
            yield payload[10:]

    monkeypatch.setattr(local_media_storage, "MEDIA_STORE_DIR", tmp_path / "media_store")
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

    def fake_get(url, *, headers=None, timeout=None, stream=False):
        captured["calls"] += 1
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == "video/mp4"
    assert captured["calls"] == 1
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.mp4"
    assert captured["headers"]["Accept"] == "video/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["stream"] is True

    def fail_get(*_args, **_kwargs):
        raise AssertionError("cached video should be served without refetching")

    monkeypatch.setattr("web.routes.medias.requests.get", fail_get)

    cached_response = authed_client_no_db.get(
        "/medias/api/mk-video?path=medias/uploads2/202505/1747910543.mp4"
    )

    assert cached_response.status_code == 200
    assert cached_response.data == payload


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
