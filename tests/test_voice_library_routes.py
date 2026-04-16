"""voice_library blueprint 的 HTTP 路由测试。

使用 authed_client_no_db + unittest.mock.patch，避免触达真实数据库。
风格对齐 tests/test_voice_library.py。
"""
from unittest.mock import patch

import pytest


def test_filters_no_language_returns_languages_and_empty_options(authed_client_no_db):
    with patch(
        "web.routes.voice_library.medias.list_enabled_languages_kv",
        return_value=[("de", "德语"), ("fr", "法语")],
    ):
        resp = authed_client_no_db.get("/voice-library/api/filters")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["languages"] == [
        {"code": "de", "name_zh": "德语"},
        {"code": "fr", "name_zh": "法语"},
    ]
    assert data["genders"] == ["male", "female"]
    assert data["use_cases"] == []
    assert data["accents"] == []
    assert data["ages"] == []
    assert data["descriptives"] == []


def test_filters_scoped_by_language(authed_client_no_db):
    with patch(
        "web.routes.voice_library.medias.list_enabled_languages_kv",
        return_value=[("de", "德语")],
    ), patch(
        "web.routes.voice_library.list_filter_options",
        return_value={
            "use_cases": ["narrative"],
            "accents": [],
            "ages": [],
            "descriptives": [],
        },
    ) as m:
        resp = authed_client_no_db.get("/voice-library/api/filters?language=de")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["use_cases"] == ["narrative"]
    m.assert_called_once_with(language="de")


def test_list_requires_language(authed_client_no_db):
    resp = authed_client_no_db.get("/voice-library/api/list")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "language is required"


def test_list_passes_all_filters(authed_client_no_db):
    with patch(
        "web.routes.voice_library.list_voices",
        return_value={"total": 2, "page": 1, "page_size": 48, "items": []},
    ) as m:
        resp = authed_client_no_db.get(
            "/voice-library/api/list?language=de&gender=male"
            "&use_case=narrative,advertisement&accent=german&age=middle_aged"
            "&descriptive=deep&q=marcus&page=2&page_size=12"
        )
    assert resp.status_code == 200
    m.assert_called_once_with(
        language="de",
        gender="male",
        use_cases=["narrative", "advertisement"],
        accents=["german"],
        ages=["middle_aged"],
        descriptives=["deep"],
        q="marcus",
        page=2,
        page_size=12,
    )


def test_list_service_value_error_returns_400(authed_client_no_db):
    with patch(
        "web.routes.voice_library.list_voices",
        side_effect=ValueError("boom"),
    ):
        resp = authed_client_no_db.get("/voice-library/api/list?language=de")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "boom"


def test_auth_required():
    from web.app import create_app
    client = create_app().test_client()
    resp = client.get("/voice-library/api/list?language=de", follow_redirects=False)
    assert resp.status_code in (302, 401)


def test_page_renders(authed_client_no_db):
    resp = authed_client_no_db.get("/voice-library/")
    assert resp.status_code == 200
    assert b"voice-library-root" in resp.data
