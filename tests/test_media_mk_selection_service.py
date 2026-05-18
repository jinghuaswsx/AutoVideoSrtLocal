from __future__ import annotations

import hashlib


def test_build_mk_json_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_mk_selection import (
        MkSelectionResponse,
        build_mk_json_flask_response,
    )

    result = MkSelectionResponse({"error": "forbidden"}, 403)

    with authed_client_no_db.application.app_context():
        response, status_code = build_mk_json_flask_response(result)

    assert status_code == 403
    assert response.get_json() == {"error": "forbidden"}


def test_build_mk_admin_required_response_returns_forbidden_payload():
    from web.services.media_mk_selection import build_mk_admin_required_response

    result = build_mk_admin_required_response()

    assert result.status_code == 403
    assert result.payload == {"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u8bbf\u95ee"}


def test_build_mk_selection_refresh_response_is_explicitly_not_implemented():
    from web.services.media_mk_selection import build_mk_selection_refresh_response

    result = build_mk_selection_refresh_response()

    assert result.status_code == 501
    assert result.payload == {
        "ok": False,
        "error": "not_implemented",
        "message": "\u660e\u7a7a\u9009\u54c1\u5237\u65b0\u540e\u53f0\u4efb\u52a1\u5c1a\u672a\u5b9e\u73b0",
    }


def test_normalize_mk_media_path_accepts_relative_media_paths_only():
    from web.services.media_mk_selection import normalize_mk_media_path

    assert normalize_mk_media_path(r".\medias\uploads2\202505\demo.jpg") == "uploads2/202505/demo.jpg"
    assert normalize_mk_media_path("/medias/uploads2/demo.mp4") == "uploads2/demo.mp4"
    assert normalize_mk_media_path("uploads2/demo.mp4") == "uploads2/demo.mp4"
    assert normalize_mk_media_path("https://wedev.example/medias/uploads2/demo.mp4") == ""
    assert normalize_mk_media_path("../secret.mp4") == ""
    assert normalize_mk_media_path("uploads2/../secret.mp4") == ""
    assert normalize_mk_media_path("   ") == ""


def test_build_mk_video_cache_object_key_hashes_path_and_keeps_safe_video_extension():
    from web.services.media_mk_selection import build_mk_video_cache_object_key

    digest = hashlib.sha256("uploads2/demo.mov".encode("utf-8")).hexdigest()

    assert build_mk_video_cache_object_key("uploads2/demo.mov", cache_prefix="mk/videos") == (
        f"mk/videos/{digest}.mov"
    )
    assert build_mk_video_cache_object_key("uploads2/demo.exe", cache_prefix="mk/videos").endswith(".mp4")


def test_guess_mk_video_type_accepts_only_video_mimetypes():
    from web.services.media_mk_selection import guess_mk_video_type

    assert guess_mk_video_type("uploads2/demo.mp4") == "video/mp4"
    assert guess_mk_video_type("uploads2/demo.unknown") == ""
    assert guess_mk_video_type("uploads2/demo.jpg") is None
    assert guess_mk_video_type(
        "uploads2/demo.custom",
        guess_type_fn=lambda path: ("video/webm; charset=utf-8", None),
    ) == "video/webm"


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
        if "SELECT MAX(snapshot_date) AS snapshot_date" in sql:
            assert args == []
            return [{"snapshot_date": "2026-04-23"}]
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
        "snapshot": "2026-04-23",
    }
    assert len(calls) == 3


def test_build_mk_selection_snapshots_response_lists_recent_archive_dates():
    from datetime import date
    from web.services.media_mk_selection import build_mk_selection_snapshots_response

    def fake_db_query(sql, args=()):
        assert "FROM dianxiaomi_rankings" in sql
        assert "GROUP BY snapshot_date" in sql
        assert args == [30]
        return [
            {"snapshot_date": date(2026, 5, 18), "listing_count": 1534},
            {"snapshot_date": "2026-05-17", "listing_count": 1498},
        ]

    result = build_mk_selection_snapshots_response(
        {},
        db_query_fn=fake_db_query,
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [
            {"snapshot": "2026-05-18", "listing_count": 1534},
            {"snapshot": "2026-05-17", "listing_count": 1498},
        ],
        "default_snapshot": "2026-05-18",
    }


def test_build_mk_selection_response_defaults_to_latest_snapshot():
    from web.services.media_mk_selection import build_mk_selection_response

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
        if "SELECT MAX(snapshot_date) AS snapshot_date" in sql:
            return [{"snapshot_date": "2026-05-11"}]
        if "SELECT COUNT(*) AS cnt" in sql:
            assert args == ["2026-05-11"]
            return [{"cnt": 0}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert args == ["2026-05-11", 50, 0]
            return []
        raise AssertionError(sql)

    result = build_mk_selection_response(
        {},
        ranking_columns_fn=fake_ranking_columns,
        db_query_fn=fake_db_query,
    )

    assert result.status_code == 200
    assert result.payload["total"] == 0
    assert result.payload["snapshot"] == "2026-05-11"


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


def test_build_mk_video_materials_response_searches_mingkong_by_product_handle():
    from web.services.media_mk_selection import build_mk_video_materials_response

    http_calls = []

    def fake_db_query(sql, args=()):
        if "SELECT MAX(snapshot_date) AS snapshot_date" in sql:
            return [{"snapshot_date": "2026-05-17"}]
        if "SELECT COUNT(*) AS cnt" in sql:
            return [{"cnt": 1}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert "mk_product_id" not in sql
            assert args == ["2026-05-17", 24, 0]
            return [
                {
                    "rank_position": 7,
                    "product_id": "gid-1",
                    "product_name": "Cool Widget",
                    "product_url": "https://shop.example/products/cool-widget",
                    "store": "7662984",
                    "sales_count": 88,
                    "order_count": 80,
                    "revenue_main": "CNY 1234.00",
                }
            ]
        raise AssertionError(sql)

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "items": [
                        {
                            "id": 901,
                            "product_name": "Cool Widget MK",
                            "product_links": ["https://shop.example/products/cool-widget"],
                            "main_image": "uploads2/main.jpg",
                            "videos": [
                                {
                                    "name": "low.mp4",
                                    "path": "uploads2/low.mp4",
                                    "image_path": "uploads2/low.jpg",
                                    "spends": "10",
                                    "ads_count": 1,
                                },
                                {
                                    "name": "winner.mp4",
                                    "path": "./medias/uploads2/winner.mp4",
                                    "image_path": "./medias/uploads2/winner.jpg",
                                    "spends": "1.2万",
                                    "ads_count": 9,
                                    "author": "Bob",
                                    "upload_time": "2026-05-16T10:00:00",
                                    "duration_seconds": 12.5,
                                },
                                {
                                    "name": "hidden.mp4",
                                    "path": "uploads2/hidden.mp4",
                                    "hidden": True,
                                },
                            ],
                        }
                    ]
                }
            }

    def fake_http_get(url, *, params=None, headers=None, timeout=None):
        http_calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse()

    result = build_mk_video_materials_response(
        {},
        db_query_fn=fake_db_query,
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=fake_http_get,
    )

    assert result.status_code == 200
    assert http_calls == [
        {
            "url": "https://wedev.example/api/marketing/medias",
            "params": {"page": 1, "q": "cool-widget", "source": "", "level": "", "show_attention": 0},
            "headers": {"Authorization": "Bearer synced-token"},
            "timeout": 20,
        }
    ]
    assert result.payload["stats"] == {
        "source_products": 1,
        "mk_searches": 1,
        "mk_no_handle": 0,
        "mk_no_match": 0,
        "mk_request_failed": 0,
        "videos": 2,
    }
    assert result.payload["items"][0]["video_name"] == "winner.mp4"
    assert result.payload["items"][0]["video_path"] == "uploads2/winner.mp4"
    assert result.payload["items"][0]["video_image_path"] == "uploads2/winner.jpg"
    assert result.payload["items"][0]["video_spends"] == 12000.0
    assert result.payload["items"][0]["video_ads_count"] == 9
    assert result.payload["items"][0]["mk_product_id"] == 901
    assert result.payload["items"][0]["product_handle"] == "cool-widget"
    assert result.payload["items"][0]["rank_position"] == 7
    assert result.payload["items"][0]["sales_count"] == 88
    assert result.payload["items"][1]["video_name"] == "low.mp4"


def test_build_mk_video_materials_response_searches_direct_product_code_without_db():
    from web.services.media_mk_selection import build_mk_video_materials_response

    http_calls = []

    def fail_db_query(*_args, **_kwargs):
        raise AssertionError("direct product_code search should not require local rankings")

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "items": [
                        {
                            "id": 902,
                            "product_name": "Direct Widget MK",
                            "product_links": ["https://shop.example/products/cool-widget"],
                            "main_image": "uploads2/direct-main.jpg",
                            "videos": [
                                {
                                    "name": "direct-low.mp4",
                                    "path": "uploads2/direct-low.mp4",
                                    "spends": "12",
                                    "ads_count": 1,
                                },
                                {
                                    "name": "direct-winner.mp4",
                                    "path": "uploads2/direct-winner.mp4",
                                    "image_path": "uploads2/direct-winner.jpg",
                                    "spends": "2.4\u4e07",
                                    "ads_count": 8,
                                },
                            ],
                        }
                    ]
                }
            }

    def fake_http_get(url, *, params=None, headers=None, timeout=None):
        http_calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse()

    result = build_mk_video_materials_response(
        {"product_code": "cool-widget-RJC", "max_videos_per_product": "5"},
        db_query_fn=fail_db_query,
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=fake_http_get,
    )

    assert result.status_code == 200
    assert http_calls == [
        {
            "url": "https://wedev.example/api/marketing/medias",
            "params": {"page": 1, "q": "cool-widget", "source": "", "level": "", "show_attention": 0},
            "headers": {"Authorization": "Bearer synced-token"},
            "timeout": 20,
        }
    ]
    assert result.payload["stats"]["source_products"] == 0
    assert result.payload["stats"]["mk_searches"] == 1
    assert result.payload["stats"]["videos"] == 2
    assert result.payload["items"][0]["video_name"] == "direct-winner.mp4"
    assert result.payload["items"][0]["product_handle"] == "cool-widget"
    assert result.payload["items"][0]["mk_product_id"] == 902
    assert result.payload["items"][0]["rank_position"] is None
    assert result.payload["has_more_products"] is False


def test_build_mk_video_materials_response_requires_exact_mingkong_product_code():
    from web.services.media_mk_selection import build_mk_video_materials_response

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "items": [
                        {
                            "id": 902,
                            "product_name": "Wrong RJC Widget",
                            "product_links": ["https://shop.example/products/cool-widget-rjc"],
                            "videos": [
                                {
                                    "name": "wrong-rjc.mp4",
                                    "path": "uploads2/wrong-rjc.mp4",
                                    "spends": "99000",
                                    "ads_count": 99,
                                },
                            ],
                        },
                        {
                            "id": 903,
                            "product_name": "Exact Widget",
                            "product_links": ["https://shop.example/products/cool-widget"],
                            "videos": [
                                {
                                    "name": "exact.mp4",
                                    "path": "uploads2/exact.mp4",
                                    "spends": "10",
                                    "ads_count": 1,
                                },
                            ],
                        },
                    ]
                }
            }

    result = build_mk_video_materials_response(
        {"product_code": "cool-widget"},
        db_query_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no db")),
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=lambda *args, **kwargs: FakeResponse(),
    )

    assert result.status_code == 200
    assert result.payload["stats"]["videos"] == 1
    assert result.payload["items"][0]["mk_product_id"] == 903
    assert result.payload["items"][0]["video_name"] == "exact.mp4"


def test_build_mk_video_materials_response_treats_suffix_only_result_as_no_match():
    from web.services.media_mk_selection import build_mk_video_materials_response

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "items": [
                        {
                            "id": 902,
                            "product_links": ["https://shop.example/products/cool-widget-rjc"],
                            "videos": [
                                {
                                    "name": "wrong-rjc.mp4",
                                    "path": "uploads2/wrong-rjc.mp4",
                                    "spends": "99000",
                                    "ads_count": 99,
                                },
                            ],
                        }
                    ]
                }
            }

    result = build_mk_video_materials_response(
        {"product_code": "cool-widget"},
        db_query_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no db")),
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=lambda *args, **kwargs: FakeResponse(),
    )

    assert result.status_code == 200
    assert result.payload["stats"]["mk_no_match"] == 1
    assert result.payload["stats"]["videos"] == 0
    assert result.payload["items"] == []


def test_build_mk_video_materials_response_direct_product_defaults_to_all_visible_videos():
    from web.services.media_mk_selection import build_mk_video_materials_response

    def fail_db_query(*_args, **_kwargs):
        raise AssertionError("direct product_code search should not require local rankings")

    spends = ["3.05万", "2.56万", "3.89千", "1.94千", "1.50千", "1.29千", "598"]

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "items": [
                        {
                            "id": 903,
                            "product_name": "Fitness Band MK",
                            "product_links": ["https://shop.example/products/fitness-band"],
                            "videos": [
                                {
                                    "name": f"video-{index}.mp4",
                                    "path": f"uploads2/video-{index}.mp4",
                                    "spends": spend,
                                    "ads_count": index,
                                }
                                for index, spend in enumerate(spends, start=1)
                            ],
                        }
                    ]
                }
            }

    result = build_mk_video_materials_response(
        {"product_code": "fitness-band"},
        db_query_fn=fail_db_query,
        build_headers_fn=lambda: {"Authorization": "Bearer synced-token"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=lambda *args, **kwargs: FakeResponse(),
    )

    assert result.status_code == 200
    assert result.payload["stats"]["videos"] == 7
    assert [item["video_name"] for item in result.payload["items"]] == [
        "video-1.mp4",
        "video-2.mp4",
        "video-3.mp4",
        "video-4.mp4",
        "video-5.mp4",
        "video-6.mp4",
        "video-7.mp4",
    ]
    assert result.payload["items"][0]["video_spends"] == 30500.0
    assert result.payload["items"][0]["video_spends_text"] == "3.05万"


def test_build_mk_video_materials_response_rejects_missing_credentials_without_request():
    from web.services.media_mk_selection import build_mk_video_materials_response

    def fail_db_query(*_args, **_kwargs):
        raise AssertionError("missing credentials should stop before db query")

    def fail_http_get(*_args, **_kwargs):
        raise AssertionError("missing credentials should stop before wedev request")

    result = build_mk_video_materials_response(
        {},
        db_query_fn=fail_db_query,
        build_headers_fn=lambda: {"Accept": "application/json"},
        get_base_url_fn=lambda: "https://wedev.example",
        http_get_fn=fail_http_get,
    )

    assert result.status_code == 500
    assert result.payload["error"] == "明空凭据未配置，请先在设置页同步 wedev 凭据"


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
