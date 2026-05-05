from __future__ import annotations


def test_build_mk_selection_response_handles_legacy_rankings_schema_without_mk_columns():
    from web.services.media_mk_selection import build_mk_selection_response

    calls: list[tuple[str, list]] = []

    def fake_ranking_columns():
        return {
            "id",
            "product_id",
            "product_name",
            "product_url",
            "store",
            "sales_count",
            "order_count",
            "revenue_main",
            "revenue_split",
            "media_product_id",
            "snapshot_date",
            "rank_position",
        }

    def fake_db_query(sql, args=()):
        calls.append((sql, list(args)))
        if "SELECT COUNT(*) AS cnt" in sql:
            assert "mk_product_name" not in sql
            assert args == ["2026-04-23", "%tooth%"]
            return [{"cnt": 0}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert "NULL AS mk_product_id" in sql
            assert "NULL AS mk_product_name" in sql
            assert "0 AS mk_total_spends" in sql
            assert "0 AS mk_video_count" in sql
            assert "0 AS mk_total_ads" in sql
            assert "ORDER BY dr.rank_position ASC" in sql
            assert args == ["2026-04-23", "%tooth%", 50, 0]
            return []
        raise AssertionError(sql)

    result = build_mk_selection_response(
        {"keyword": "tooth"},
        ranking_columns_fn=fake_ranking_columns,
        db_query_fn=fake_db_query,
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }
    assert len(calls) == 2


def test_build_mk_selection_response_rejects_invalid_pagination_without_db_query():
    from web.services.media_mk_selection import build_mk_selection_response

    def fail_ranking_columns():
        raise AssertionError("invalid pagination should stop before schema lookup")

    def fail_db_query(*_args, **_kwargs):
        raise AssertionError("invalid pagination should stop before db query")

    result = build_mk_selection_response(
        {"page": "bad"},
        ranking_columns_fn=fail_ranking_columns,
        db_query_fn=fail_db_query,
    )

    assert result.status_code == 400
    assert result.payload["error"] == "invalid_pagination"


def test_build_mk_detail_response_uses_server_side_credentials():
    from web.services.media_mk_selection import build_mk_detail_response

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"item": {"id": 3719}}}

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    result = build_mk_detail_response(
        3719,
        build_headers_fn=lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token",
            "Accept": "application/json",
        },
        get_base_url_fn=lambda: "https://wedev.example",
        is_login_expired_fn=lambda data: False,
        http_get_fn=fake_get,
    )

    assert result.status_code == 200
    assert result.payload == {"data": {"item": {"id": 3719}}}
    assert captured["url"] == "https://wedev.example/api/marketing/medias/3719"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["timeout"] == 15


def test_build_mk_detail_response_rejects_missing_credentials_without_request():
    from web.services.media_mk_selection import build_mk_detail_response

    def fail_get(*_args, **_kwargs):
        raise AssertionError("missing credentials should stop before request")

    result = build_mk_detail_response(
        3719,
        build_headers_fn=lambda: {"Accept": "application/json"},
        get_base_url_fn=lambda: "https://wedev.example",
        is_login_expired_fn=lambda data: False,
        http_get_fn=fail_get,
    )

    assert result.status_code == 500
    assert "error" in result.payload


def test_build_mk_detail_response_maps_expired_login_to_401():
    from web.services.media_mk_selection import build_mk_detail_response

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"is_guest": True}

    result = build_mk_detail_response(
        3719,
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        is_login_expired_fn=lambda data: True,
        http_get_fn=lambda *_args, **_kwargs: FakeResponse(),
    )

    assert result.status_code == 401
    assert "error" in result.payload


def test_build_mk_media_proxy_response_fetches_wedev_media_with_server_credentials():
    from web.services.media_mk_selection import build_mk_media_proxy_response

    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"image-bytes"
        headers = {"content-type": "image/jpeg; charset=utf-8"}

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    result = build_mk_media_proxy_response(
        "uploads2/202505/1747910543.jpg",
        build_headers_fn=lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token",
            "Content-Type": "application/json",
        },
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=fake_get,
    )

    assert result.status_code == 200
    assert result.content == b"image-bytes"
    assert result.content_type == "image/jpeg"
    assert result.cache_control == "private, max-age=3600"
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.jpg"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Accept"] == "image/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 20


def test_build_mk_media_proxy_response_rejects_missing_credentials_without_request():
    from web.services.media_mk_selection import build_mk_media_proxy_response

    def fail_get(*_args, **_kwargs):
        raise AssertionError("missing credentials should stop before request")

    result = build_mk_media_proxy_response(
        "uploads2/202505/1747910543.jpg",
        build_headers_fn=lambda: {},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=fail_get,
    )

    assert result.status_code == 500
    assert result.payload
    assert "error" in result.payload


def test_cache_mk_video_fetches_and_writes_wedev_video_with_server_credentials(tmp_path):
    from web.services.media_mk_selection import cache_mk_video

    captured = {}
    destination = tmp_path / "cache" / "demo.mp4"
    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "video/mp4; charset=utf-8", "content-length": str(len(payload))}

        @staticmethod
        def iter_content(chunk_size=1024 * 1024):
            captured["chunk_size"] = chunk_size
            yield payload[:8]
            yield payload[8:]

        @staticmethod
        def close():
            captured["closed"] = True

    def fake_get(url, *, headers=None, timeout=None, stream=False):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    object_key = cache_mk_video(
        "uploads2/202505/1747910543.mp4",
        cache_object_key_fn=lambda _path: "cache/demo.mp4",
        storage_exists_fn=lambda _key: destination.exists(),
        build_headers_fn=lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token",
            "Content-Type": "application/json",
        },
        get_base_url_fn=lambda: "https://wedev.example",
        safe_local_path_for_fn=lambda _key: destination,
        http_get_fn=fake_get,
        max_bytes=1024 * 1024,
    )

    assert object_key == "cache/demo.mp4"
    assert destination.read_bytes() == payload
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.mp4"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Accept"] == "video/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 60
    assert captured["stream"] is True
    assert captured["closed"] is True


def test_cache_mk_video_rejects_missing_credentials_without_request(tmp_path):
    import pytest
    from web.services.media_mk_selection import MkCredentialsMissingError, cache_mk_video

    def fail_get(*_args, **_kwargs):
        raise AssertionError("missing credentials should stop before request")

    with pytest.raises(MkCredentialsMissingError):
        cache_mk_video(
            "uploads2/202505/1747910543.mp4",
            cache_object_key_fn=lambda _path: "cache/demo.mp4",
            storage_exists_fn=lambda _key: False,
            build_headers_fn=lambda: {},
            get_base_url_fn=lambda: "https://wedev.example",
            safe_local_path_for_fn=lambda _key: tmp_path / "cache" / "demo.mp4",
            http_get_fn=fail_get,
        )
