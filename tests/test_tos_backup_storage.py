from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_reloaded_modules():
    yield
    os.environ["TOS_BACKUP_ENABLED"] = "0"
    os.environ["FILE_STORAGE_MODE"] = "local_primary"
    for name in ("config", "appcore.tos_backup_storage"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _reload_backup_storage(monkeypatch):
    monkeypatch.setenv("TOS_BACKUP_ENABLED", "1")
    monkeypatch.setenv("FILE_STORAGE_MODE", "local_primary")
    monkeypatch.setenv("TOS_BACKUP_BUCKET", "autovideosrtlocal")
    monkeypatch.setenv("TOS_BACKUP_PREFIX", "FILES")
    monkeypatch.setenv("TOS_BACKUP_DB_PREFIX", "DB")
    monkeypatch.setenv("TOS_BACKUP_ENV", "test")
    monkeypatch.setenv("TOS_BACKUP_REGION", "cn-shanghai")
    monkeypatch.setenv("TOS_BACKUP_PUBLIC_ENDPOINT", "public.tos.example.com")
    monkeypatch.setenv("TOS_BACKUP_PRIVATE_ENDPOINT", "private.tos.example.com")
    monkeypatch.setenv("TOS_BACKUP_USE_PRIVATE_ENDPOINT", "false")
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk")
    monkeypatch.setenv("TOS_BACKUP_SIGNED_URL_EXPIRES", "3600")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    else:
        import config  # noqa: F401

    if "appcore.tos_backup_storage" in sys.modules:
        return importlib.reload(sys.modules["appcore.tos_backup_storage"])
    return importlib.import_module("appcore.tos_backup_storage")


def test_backup_object_key_maps_posix_path(monkeypatch):
    backup = _reload_backup_storage(monkeypatch)

    assert backup.backup_object_key_for_local_path(
        "/opt/autovideosrt-test/output/media_store/1/a.jpg"
    ) == "FILES/test/opt/autovideosrt-test/output/media_store/1/a.jpg"


def test_backup_object_key_maps_windows_path(monkeypatch):
    backup = _reload_backup_storage(monkeypatch)

    assert backup.backup_object_key_for_local_path(
        r"G:\Code\AutoVideoSrtLocal\output\media_store\1\a.jpg"
    ) == "FILES/test/G/Code/AutoVideoSrtLocal/output/media_store/1/a.jpg"


def test_no_proxy_contains_tos_domains(monkeypatch):
    backup = _reload_backup_storage(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    backup.ensure_tos_direct_no_proxy()

    for key in ("NO_PROXY", "no_proxy"):
        value = os.environ[key]
        assert "localhost" in value
        assert ".volces.com" in value
        assert ".ivolces.com" in value
        assert "volces.com" in value
        assert "ivolces.com" in value


class FakeBackupClient:
    def __init__(self, *, existing=None, payload=b"remote-payload"):
        self.existing = set(existing or [])
        self.payload = payload
        self.uploaded: dict[str, bytes] = {}
        self.downloaded: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def head_object(self, bucket, object_key):
        if object_key not in self.existing:
            raise RuntimeError("NoSuchKey")
        return types.SimpleNamespace(content_length=len(self.payload))

    def put_object_from_file(self, bucket, object_key, local_path):
        self.uploaded[object_key] = Path(local_path).read_bytes()
        self.existing.add(object_key)

    def get_object_to_file(self, bucket, object_key, local_path):
        if object_key not in self.existing:
            raise RuntimeError("NoSuchKey")
        self.downloaded.append((object_key, local_path))
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(self.payload)

    def delete_object(self, bucket, object_key):
        self.deleted.append((bucket, object_key))
        self.existing.discard(object_key)


def test_download_to_file_keeps_existing_file_if_download_fails(tmp_path, monkeypatch):
    backup = _reload_backup_storage(monkeypatch)
    destination = tmp_path / "existing.jpg"
    destination.write_bytes(b"old")

    class FailingClient(FakeBackupClient):
        def get_object_to_file(self, bucket, object_key, local_path):
            Path(local_path).write_bytes(b"partial")
            raise RuntimeError("network failed")

    monkeypatch.setattr(backup, "get_backup_client", lambda: FailingClient(existing={"key"}))

    try:
        backup.download_to_file("key", destination)
    except RuntimeError:
        pass

    assert destination.read_bytes() == b"old"


def test_reconcile_uploads_when_local_exists_and_tos_missing(tmp_path, monkeypatch):
    backup = _reload_backup_storage(monkeypatch)
    local_path = tmp_path / "output" / "media_store" / "1" / "a.jpg"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"local-image")
    fake = FakeBackupClient(existing=set())
    monkeypatch.setattr(backup, "get_backup_client", lambda: fake)

    result = backup.reconcile_local_file(local_path)

    assert result.action == "uploaded"
    assert result.local_exists is True
    assert result.remote_exists is False
    assert fake.uploaded[result.object_key] == b"local-image"


def test_reconcile_downloads_when_tos_exists_and_local_missing(tmp_path, monkeypatch):
    backup = _reload_backup_storage(monkeypatch)
    local_path = tmp_path / "output" / "media_store" / "1" / "a.jpg"
    object_key = backup.backup_object_key_for_local_path(local_path)
    fake = FakeBackupClient(existing={object_key}, payload=b"remote-image")
    monkeypatch.setattr(backup, "get_backup_client", lambda: fake)

    result = backup.reconcile_local_file(local_path)

    assert result.action == "downloaded"
    assert result.local_exists is False
    assert result.remote_exists is True
    assert local_path.read_bytes() == b"remote-image"
    assert fake.downloaded[0][0] == object_key
    assert Path(fake.downloaded[0][1]).parent == local_path.parent
    assert Path(fake.downloaded[0][1]).name.startswith("tos_backup_")


def test_reconcile_fails_when_both_sides_missing(tmp_path, monkeypatch):
    backup = _reload_backup_storage(monkeypatch)
    local_path = tmp_path / "output" / "media_store" / "missing.jpg"
    fake = FakeBackupClient(existing=set())
    monkeypatch.setattr(backup, "get_backup_client", lambda: fake)

    result = backup.reconcile_local_file(local_path)

    assert result.action == "failed"
    assert result.local_exists is False
    assert result.remote_exists is False
    assert "missing" in result.error.lower()
