"""voice_library blueprint 的 HTTP 路由测试。

使用 authed_client_no_db + unittest.mock.patch，避免触达真实数据库。
风格对齐 tests/test_voice_library.py。
"""
from pathlib import Path
from unittest.mock import patch

import pytest

import web.routes.voice_library as voice_library


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


@pytest.fixture(autouse=True)
def clear_voice_library_upload_reservations():
    voice_library._upload_reservations.clear()
    yield
    voice_library._upload_reservations.clear()


# ---------------------------------------------------------------------------
# Task 4.1: POST /voice-library/api/match/upload-url
# ---------------------------------------------------------------------------


def test_match_upload_url_returns_local_upload_reservation(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.voice_library.UPLOAD_DIR", str(tmp_path / "uploads"))
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "demo.mp4", "content_type": "video/mp4"},
    )
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["upload_url"].startswith("/voice-library/api/match/upload/")
    assert data["upload_token"]
    assert data["filename"] == "demo.mp4"
    assert data["expires_in"] == 600
    reservation = voice_library._upload_reservations[data["upload_token"]]
    assert reservation["user_id"] == 1
    assert reservation["video_path"].endswith("demo.mp4")


def test_match_upload_url_rejects_bad_content_type(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "x.exe", "content_type": "application/x-msdownload"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported content_type"


def test_match_upload_url_sanitizes_filename(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.voice_library.UPLOAD_DIR", str(tmp_path / "uploads"))
    resp = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "../../evil.mp4", "content_type": "video/mp4"},
    )
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["filename"] in {".._.._evil.mp4", "___evil.mp4", "evil.mp4"}
    reservation = voice_library._upload_reservations[data["upload_token"]]
    assert "..\\" not in reservation["video_path"]
    assert "/../" not in reservation["video_path"].replace("\\", "/")


def test_match_local_upload_writes_reserved_file(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.voice_library.UPLOAD_DIR", str(tmp_path / "uploads"))
    bootstrap = authed_client_no_db.post(
        "/voice-library/api/match/upload-url",
        json={"filename": "demo.mp4", "content_type": "video/mp4"},
    ).get_json()

    response = authed_client_no_db.put(
        bootstrap["upload_url"],
        data=b"video-bytes",
        content_type="video/mp4",
    )

    assert response.status_code == 204
    reservation = voice_library._upload_reservations[bootstrap["upload_token"]]
    assert Path(reservation["video_path"]).read_bytes() == b"video-bytes"


# ---------------------------------------------------------------------------
# Task 4.2: POST /voice-library/api/match/start
# ---------------------------------------------------------------------------


def test_match_start_returns_task_id(authed_client_no_db, monkeypatch, tmp_path):
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "web.routes.voice_library._consume_upload_token",
        lambda upload_token: {
            "user_id": 1,
            "video_path": str(video_path),
            "filename": "demo.mp4",
            "uploaded": True,
        },
    )
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
            "upload_token": "upload-token-1",
            "language": "de",
            "gender": "male",
        },
    )
    assert resp.status_code == 202
    assert resp.get_json()["task_id"] == "vm_fake"


def test_match_start_rejects_foreign_upload_token(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library._consume_upload_token",
        lambda upload_token: {
            "user_id": 2,
            "video_path": "uploads/voice_match/demo.mp4",
            "filename": "demo.mp4",
            "uploaded": True,
        },
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "upload_token": "foreign-token",
            "language": "de",
            "gender": "male",
        },
    )
    assert resp.status_code == 403


def test_match_start_rejects_disabled_language(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library._consume_upload_token",
        lambda upload_token: {
            "user_id": 1,
            "video_path": "uploads/voice_match/demo.mp4",
            "filename": "demo.mp4",
            "uploaded": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "upload_token": "upload-token-1",
            "language": "fr",
            "gender": "male",
        },
    )
    assert resp.status_code == 400


def test_match_start_rejects_invalid_gender(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library._consume_upload_token",
        lambda upload_token: {
            "user_id": 1,
            "video_path": "uploads/voice_match/demo.mp4",
            "filename": "demo.mp4",
            "uploaded": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "upload_token": "upload-token-1",
            "language": "de",
            "gender": "other",
        },
    )
    assert resp.status_code == 400


def test_match_start_rejects_missing_local_upload(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.voice_library._consume_upload_token", lambda upload_token: None)
    monkeypatch.setattr(
        "web.routes.voice_library.medias.list_enabled_language_codes",
        lambda: ["de"],
    )
    resp = authed_client_no_db.post(
        "/voice-library/api/match/start",
        json={
            "upload_token": "missing-token",
            "language": "de",
            "gender": "male",
        },
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "upload token not found"


# ---------------------------------------------------------------------------
# Task 4.3: GET /voice-library/api/match/status/<task_id>
# ---------------------------------------------------------------------------


def test_match_status_returns_task_state(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.get_task",
        lambda tid, user_id: {
            "task_id": tid, "status": "done", "progress": 100,
            "error": None,
            "result": {"candidates": [], "sample_audio_path": "uploads/voice_match/vm_x/clip.wav"},
        },
    )
    resp = authed_client_no_db.get("/voice-library/api/match/status/vm_x")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "done"
    assert payload["result"]["sample_audio_url"].endswith("/voice-library/api/match/artifact/vm_x/sample-audio")
    assert "sample_audio_path" not in payload["result"]


def test_match_status_missing_returns_404(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.get_task",
        lambda *a, **kw: None,
    )
    resp = authed_client_no_db.get("/voice-library/api/match/status/vm_nope")
    assert resp.status_code == 404


def test_match_sample_audio_serves_owned_file(tmp_path, authed_client_no_db, monkeypatch):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"wav-data")
    monkeypatch.setattr(
        "web.routes.voice_library.vmt.get_task",
        lambda tid, user_id: {
            "task_id": tid,
            "result": {"sample_audio_path": str(clip)},
        },
    )

    resp = authed_client_no_db.get("/voice-library/api/match/artifact/vm_x/sample-audio")

    assert resp.status_code == 200
    assert resp.data == b"wav-data"
