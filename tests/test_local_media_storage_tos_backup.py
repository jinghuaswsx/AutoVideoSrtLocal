from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_reloaded_modules():
    yield
    os.environ["TOS_BACKUP_ENABLED"] = "0"
    os.environ["FILE_STORAGE_MODE"] = "local_primary"
    for name in ("config", "appcore.tos_backup_storage", "appcore.local_media_storage"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _reload_local_media_storage(monkeypatch, tmp_path, *, mode="local_primary"):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("TOS_BACKUP_ENABLED", "1")
    monkeypatch.setenv("FILE_STORAGE_MODE", mode)
    monkeypatch.setenv("TOS_BACKUP_BUCKET", "autovideosrtlocal")
    monkeypatch.setenv("TOS_BACKUP_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_BACKUP_SECRET_KEY", "sk")
    monkeypatch.setenv("TOS_BACKUP_PREFIX", "FILES")
    monkeypatch.setenv("TOS_BACKUP_ENV", "test")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    else:
        import config  # noqa: F401
    if "appcore.tos_backup_storage" in sys.modules:
        importlib.reload(sys.modules["appcore.tos_backup_storage"])
    if "appcore.local_media_storage" in sys.modules:
        return importlib.reload(sys.modules["appcore.local_media_storage"])
    return importlib.import_module("appcore.local_media_storage")


def test_write_bytes_ensures_remote_copy(tmp_path, monkeypatch):
    storage = _reload_local_media_storage(monkeypatch, tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(
        storage.tos_backup_storage,
        "ensure_remote_copy_for_local_path",
        lambda local_path: calls.append(Path(local_path)),
    )

    storage.write_bytes("1/medias/10/a.jpg", b"image")

    assert calls == [storage.local_path_for("1/medias/10/a.jpg")]


def test_write_bytes_uploads_temp_file_first_in_tos_primary(tmp_path, monkeypatch):
    storage = _reload_local_media_storage(monkeypatch, tmp_path, mode="tos_primary")
    destination = storage.local_path_for("1/medias/10/a.jpg")
    uploaded = []
    monkeypatch.setattr(storage.tos_backup_storage, "backup_object_key_for_local_path", lambda local_path: f"FILES/test/{Path(local_path).name}")
    monkeypatch.setattr(storage.tos_backup_storage, "object_exists", lambda object_key: False)

    def fake_upload(local_path, object_key):
        path = Path(local_path)
        uploaded.append((path.name, object_key, destination.exists(), path.read_bytes()))
        return object_key

    monkeypatch.setattr(storage.tos_backup_storage, "upload_local_file", fake_upload)

    storage.write_bytes("1/medias/10/a.jpg", b"image")

    assert len(uploaded) == 1
    assert uploaded[0][0].startswith("media_store_")
    assert uploaded[0][1:] == ("FILES/test/a.jpg", False, b"image")
    assert destination.read_bytes() == b"image"


def test_download_to_materializes_from_tos_when_local_missing(tmp_path, monkeypatch):
    storage = _reload_local_media_storage(monkeypatch, tmp_path, mode="tos_primary")

    def fake_ensure_local(local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"remote-image")

    monkeypatch.setattr(storage.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure_local)

    destination = tmp_path / "restored.jpg"
    storage.download_to("1/medias/10/a.jpg", destination)

    assert destination.read_bytes() == b"remote-image"


def test_download_to_same_local_path_materializes_without_copying_over_itself(tmp_path, monkeypatch):
    storage = _reload_local_media_storage(monkeypatch, tmp_path, mode="tos_primary")

    def fake_ensure_local(local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"remote-image")

    monkeypatch.setattr(storage.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure_local)

    source = storage.local_path_for("1/medias/10/a.jpg")

    assert storage.download_to("1/medias/10/a.jpg", source) == str(source)
    assert source.read_bytes() == b"remote-image"


def test_exists_returns_true_from_remote_in_tos_primary(tmp_path, monkeypatch):
    storage = _reload_local_media_storage(monkeypatch, tmp_path, mode="tos_primary")
    monkeypatch.setattr(storage.tos_backup_storage, "object_exists", lambda object_key: object_key.endswith("a.jpg"))
    monkeypatch.setattr(storage.tos_backup_storage, "backup_object_key_for_local_path", lambda local_path: f"FILES/test/{Path(local_path).name}")

    assert storage.exists("1/medias/10/a.jpg") is True
    assert storage.exists("1/medias/10/missing.jpg") is False
