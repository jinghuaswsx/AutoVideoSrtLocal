"""安全测试：文件上传扩展名校验。

所有上传端点应该拒绝非视频文件（如 .php, .html, .exe）。
"""
import io
import json
import os

import pytest


ALLOWED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
REJECTED_EXTS = [".php", ".html", ".exe", ".sh", ".py", ".txt", ".jpg"]


# ── helpers ──

def _make_file(filename: str, content: bytes = b"fake-video-data"):
    return (io.BytesIO(content), filename)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Flask test client with mocked user, no DB dependency for upload tests."""
    fake_user = {"id": 1, "username": "test-admin", "role": "admin", "is_active": 1}
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

    from web.app import create_app
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
    return c


# ── task upload (/api/tasks) ──

class TestTaskUploadValidation:
    """task 蓝图上传端点应该校验视频扩展名。"""

    def test_accepts_mp4(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr("web.routes.task.store.create", lambda *a, **kw: {})
        monkeypatch.setattr("web.routes.task.store.update", lambda *a, **kw: None)
        monkeypatch.setattr("web.routes.task._extract_thumbnail", lambda *a, **kw: None)
        monkeypatch.setattr("web.routes.task._resolve_name_conflict", lambda uid, name: name)
        monkeypatch.setattr("web.routes.task.db_execute", lambda *a, **kw: None)
        monkeypatch.setattr("web.routes.task.db_query_one", lambda *a, **kw: None)
        monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr("web.routes.task.UPLOAD_DIR", str(tmp_path))

        resp = client.post("/api/tasks", data={"video": _make_file("test.mp4")},
                           content_type="multipart/form-data")
        assert resp.status_code == 201

    @pytest.mark.parametrize("ext", REJECTED_EXTS)
    def test_rejects_non_video_extensions(self, client, ext):
        resp = client.post("/api/tasks", data={"video": _make_file(f"malicious{ext}")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


# ── video_review upload (/api/video-review/upload) ──

class TestVideoReviewUploadValidation:

    def test_accepts_mp4(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr("web.routes.video_review.db_execute", lambda *a, **kw: None)
        monkeypatch.setattr("web.routes.video_review.OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr("web.routes.video_review.UPLOAD_DIR", str(tmp_path))

        resp = client.post("/api/video-review/upload",
                           data={"video": _make_file("test.mp4")},
                           content_type="multipart/form-data")
        assert resp.status_code == 201

    @pytest.mark.parametrize("ext", REJECTED_EXTS)
    def test_rejects_non_video_extensions(self, client, ext):
        resp = client.post("/api/video-review/upload",
                           data={"video": _make_file(f"malicious{ext}")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400


# ── video_creation upload (/api/video-creation/upload) ──

class TestVideoCreationUploadValidation:

    @pytest.mark.parametrize("ext", REJECTED_EXTS)
    def test_rejects_non_video_extensions(self, client, ext):
        resp = client.post("/api/video-creation/upload",
                           data={"video": _make_file(f"malicious{ext}")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
