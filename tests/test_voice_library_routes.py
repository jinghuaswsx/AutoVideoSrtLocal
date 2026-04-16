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


# ---------------------------------------------------------------------------
# Task 4.1: POST /voice-library/api/match/upload-url
# ---------------------------------------------------------------------------


def test_match_upload_url_returns_signed(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.generate_signed_upload_url",
        lambda key, expires=600: "https://signed",
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "demo.mp4", "content_type": "video/mp4"},
    )
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["upload_url"] == "https://signed"
    assert data["object_key"].startswith("voice_match/1/")
    assert data["object_key"].endswith("/demo.mp4")
    assert data["expires_in"] == 600


def test_match_upload_url_rejects_bad_content_type(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "x.exe", "content_type": "application/x-msdownload"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported content_type"


def test_match_upload_url_sanitizes_filename(authed_client_no_db, monkeypatch):
    captured = {}

    def fake(key, expires=600):
        captured["key"] = key
        return "https://signed"

    monkeypatch.setattr(
        "web.routes.voice_library.generate_signed_upload_url", fake
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "../../evil.mp4", "content_type": "video/mp4"},
    )
    assert resp.status_code == 200
    assert "/../" not in captured["key"]
    assert (
        captured["key"].endswith("/.._.._evil.mp4")
        or captured["key"].endswith("/___evil.mp4")
        or "evil" in captured["key"]
    )


# ---------------------------------------------------------------------------
# Task 4.2: POST /voice-library/api/match/start
# ---------------------------------------------------------------------------


def test_match_start_returns_task_id(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.create_task",
        lambda **kw: "vm_fake",
    )
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de", "fr"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "de",
            "gender": "male",
        },
    )
    assert resp.status_code == 202
    assert resp.get_json()["task_id"] == "vm_fake"


def test_match_start_rejects_foreign_object_key(authed_client_no_db):
    # authed user is id=1; posting key under /2/ should 403
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "object_key": "voice_match/2/abc/demo.mp4",
            "language": "de",
            "gender": "male",
        },
    )
    assert resp.status_code == 403


def test_match_start_rejects_disabled_language(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "fr",
            "gender": "male",
        },
    )
    assert resp.status_code == 400


def test_match_start_rejects_invalid_gender(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "de",
            "gender": "other",
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Task 4.3: GET /voice-library/api/match/status/<task_id>
# ---------------------------------------------------------------------------


def test_match_status_returns_task_state(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.get_task",
        lambda tid, user_id: {
            "task_id": tid, "status": "done", "progress": 100,
            "error": None, "result": {"candidates": []},
        },
    )
    resp = authed_client_no_db.get("/voice-library/api/match/status/vm_x")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "done"


def test_match_status_missing_returns_404(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.get_task",
        lambda *a, **kw: None,
    )
    resp = authed_client_no_db.get("/voice-library/api/match/status/vm_nope")
    assert resp.status_code == 404
