from __future__ import annotations

from flask import Flask
import pytest
from werkzeug.exceptions import NotFound


def test_is_media_available_returns_false_for_empty_or_invalid_key(monkeypatch):
    from web.services import media_object_storage as svc

    calls = []

    def fake_exists(object_key):
        calls.append(object_key)
        raise ValueError("invalid object key")

    monkeypatch.setattr(svc.local_media_storage, "exists", fake_exists)

    assert svc.is_media_available("") is False
    assert svc.is_media_available("..\\outside.mp4") is False
    assert calls == ["..\\outside.mp4"]


def test_download_media_object_raises_file_not_found_for_invalid_key(monkeypatch, tmp_path):
    from web.services import media_object_storage as svc

    monkeypatch.setattr(
        svc.local_media_storage,
        "exists",
        lambda object_key: (_ for _ in ()).throw(ValueError("invalid object key")),
    )

    with pytest.raises(FileNotFoundError):
        svc.download_media_object("../outside.mp4", tmp_path / "media.mp4")


def test_download_media_object_requires_existing_object(monkeypatch, tmp_path):
    from web.services import media_object_storage as svc

    monkeypatch.setattr(svc.local_media_storage, "exists", lambda object_key: False)

    with pytest.raises(FileNotFoundError):
        svc.download_media_object("1/medias/123/video.mp4", tmp_path / "media.mp4")


def test_download_media_object_delegates_to_local_storage(monkeypatch, tmp_path):
    from web.services import media_object_storage as svc

    calls = []
    destination = tmp_path / "media.mp4"
    monkeypatch.setattr(svc.local_media_storage, "exists", lambda object_key: True)
    monkeypatch.setattr(
        svc.local_media_storage,
        "download_to",
        lambda object_key, target: calls.append((object_key, target)) or str(target),
    )

    result = svc.download_media_object("1/medias/123/video.mp4", destination)

    assert result == str(destination)
    assert calls == [("1/medias/123/video.mp4", destination)]


def test_delete_media_object_ignores_empty_invalid_and_storage_errors(monkeypatch):
    from web.services import media_object_storage as svc

    deleted = []

    def fake_delete(object_key):
        deleted.append(object_key)
        raise RuntimeError("delete failed")

    monkeypatch.setattr(svc.local_media_storage, "delete", fake_delete)

    svc.delete_media_object(None)
    svc.delete_media_object("   ")
    svc.delete_media_object("1/medias/123/video.mp4")

    assert deleted == ["1/medias/123/video.mp4"]


def test_send_media_object_serves_safe_local_path(monkeypatch, tmp_path):
    from web.services import media_object_storage as svc

    local_file = tmp_path / "video.mp4"
    local_file.write_bytes(b"video")
    calls = []

    monkeypatch.setattr(svc, "is_media_available", lambda object_key: True)
    monkeypatch.setattr(svc.local_media_storage, "safe_local_path_for", lambda object_key: local_file)
    monkeypatch.setattr(
        svc,
        "send_file",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        result = svc.send_media_object("1/medias/123/video.mp4")

    assert result == "sent"
    assert calls[0][0] == (str(local_file),)
    assert calls[0][1]["mimetype"] == "video/mp4"


def test_send_media_object_aborts_when_safe_path_rejects_key(monkeypatch):
    from web.services import media_object_storage as svc

    monkeypatch.setattr(svc, "is_media_available", lambda object_key: True)
    monkeypatch.setattr(
        svc.local_media_storage,
        "safe_local_path_for",
        lambda object_key: (_ for _ in ()).throw(ValueError("invalid object key")),
    )

    app = Flask(__name__)
    with app.app_context():
        with pytest.raises(NotFound):
            svc.send_media_object("../outside.mp4")
