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
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.task_state._db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.task_state._sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.task.db_query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.task.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.de_translate.db_query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.de_translate.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.fr_translate.db_query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.fr_translate.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.video_review.get_retention_hours", lambda project_type: 24)
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

    def test_accepts_local_mp4_upload(self, client):
        resp = client.post("/api/tasks", data={"video": _make_file("test.mp4"), "source_language": "en"},
                           content_type="multipart/form-data")
        assert resp.status_code == 201
        data = resp.get_json()
        assert "task_id" in data

    @pytest.mark.parametrize("ext", REJECTED_EXTS)
    def test_rejects_non_video_extensions(self, client, ext):
        resp = client.post("/api/tasks", data={"video": _make_file(f"malicious{ext}")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


class TestDeTranslateUploadValidation:

    def test_accepts_local_mp4_upload(self, client):
        resp = client.post("/api/de-translate/start", data={"video": _make_file("test.mp4")},
                           content_type="multipart/form-data")
        assert resp.status_code == 201
        data = resp.get_json()
        assert "task_id" in data


class TestFrTranslateUploadValidation:

    def test_accepts_local_mp4_upload(self, client):
        resp = client.post("/api/fr-translate/start", data={"video": _make_file("test.mp4")},
                           content_type="multipart/form-data")
        assert resp.status_code == 201
        data = resp.get_json()
        assert "task_id" in data


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


# ── copywriting upload (/api/copywriting/upload) ──

class TestCopywritingUploadValidation:
    """copywriting 上传端点应校验视频扩展名。"""

    @pytest.mark.parametrize("ext", REJECTED_EXTS)
    def test_rejects_non_video_extensions(self, client, ext):
        resp = client.post("/api/copywriting/upload",
                           data={"video": _make_file(f"malicious{ext}")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


# ── 图片扩展名校验 (单元测试) ──

class TestImageExtensionValidation:
    """web.upload_util.validate_image_extension 单元测试。"""

    def test_accepts_jpg(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("photo.jpg") is True

    def test_accepts_png(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("photo.png") is True

    def test_accepts_webp(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("photo.webp") is True

    def test_rejects_exe(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("malware.exe") is False

    def test_rejects_php(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("shell.php") is False

    def test_rejects_empty(self):
        from web.upload_util import validate_image_extension
        assert validate_image_extension("") is False


# ── 文件名清洗 (单元测试) ──

class TestSecureFilename:
    """web.upload_util.secure_filename_component 单元测试。"""

    def test_strips_path_traversal(self):
        from web.upload_util import secure_filename_component
        result = secure_filename_component("../../etc/passwd")
        assert "/" not in result
        assert ".." not in result or result.startswith("_")

    def test_preserves_normal_name(self):
        from web.upload_util import secure_filename_component
        assert secure_filename_component("photo.jpg") == "photo.jpg"

    def test_truncates_long_name(self):
        from web.upload_util import secure_filename_component
        long_name = "a" * 200 + ".jpg"
        result = secure_filename_component(long_name)
        assert len(result) <= 100

    def test_replaces_special_chars(self):
        from web.upload_util import secure_filename_component
        result = secure_filename_component("file name (1).jpg")
        assert " " not in result
        assert "(" not in result

    def test_empty_returns_unnamed(self):
        from web.upload_util import secure_filename_component
        assert secure_filename_component("") == "unnamed"
