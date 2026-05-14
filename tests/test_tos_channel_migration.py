from __future__ import annotations

from pathlib import Path
import types


class FakeTargetClient:
    def __init__(self, existing=None):
        self.existing = set(existing or [])
        self.uploaded: dict[str, bytes] = {}

    def head_object(self, bucket, object_key):
        if object_key not in self.existing:
            raise RuntimeError("NoSuchKey")
        return types.SimpleNamespace(content_length=len(self.uploaded.get(object_key, b"")))

    def put_object_from_file(self, bucket, object_key, local_path):
        self.uploaded[object_key] = Path(local_path).read_bytes()
        self.existing.add(object_key)


def _fake_target_config():
    from appcore.tos_channel_migration import TosChannelConfig

    return TosChannelConfig(
        code="tos_wj",
        access_key="ak",
        secret_key="sk",
        region="cn-shanghai",
        bucket="avs-rjc",
        public_endpoint="tos-cn-shanghai.volces.com",
        private_endpoint="tos-cn-shanghai.ivolces.com",
    )


def test_copy_protected_files_to_channel_uploads_all_collected_refs(monkeypatch, tmp_path):
    from appcore import tos_channel_migration as migration
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_backup_storage import SyncResult

    local_a = tmp_path / "a.mp4"
    local_a.write_bytes(b"video")
    local_b = tmp_path / "covers" / "b.jpg"
    fake_client = FakeTargetClient()

    monkeypatch.setattr(migration, "load_tos_channel_config", lambda code: _fake_target_config())
    monkeypatch.setattr(migration, "_build_target_client", lambda target: fake_client)
    monkeypatch.setattr(
        migration.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [
            ProtectedFileRef(str(local_a), ("project_video",)),
            ProtectedFileRef(str(local_b), ("raw_source_cover",)),
        ],
    )
    monkeypatch.setattr(
        migration.tos_backup_storage,
        "backup_object_key_for_local_path",
        lambda local_path: f"FILES/test/{Path(local_path).name}",
    )

    def fake_ensure(local_path):
        path = Path(local_path)
        if not path.exists():
            path.parent.mkdir(parents=True)
            path.write_bytes(b"cover")
        return SyncResult(
            local_path=str(path),
            object_key=f"FILES/test/{path.name}",
            action="synced",
            local_exists=True,
            remote_exists=True,
        )

    monkeypatch.setattr(migration.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure)

    summary = migration.copy_protected_files_to_channel()

    assert summary["files_checked"] == 2
    assert summary["actions"] == {"uploaded": 2}
    assert fake_client.uploaded == {
        "FILES/test/a.mp4": b"video",
        "FILES/test/b.jpg": b"cover",
    }


def test_copy_protected_files_to_channel_skips_existing_object(monkeypatch, tmp_path):
    from appcore import tos_channel_migration as migration
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_backup_storage import SyncResult

    local_a = tmp_path / "a.mp4"
    local_a.write_bytes(b"video")
    fake_client = FakeTargetClient(existing={"FILES/test/a.mp4"})

    monkeypatch.setattr(migration, "load_tos_channel_config", lambda code: _fake_target_config())
    monkeypatch.setattr(migration, "_build_target_client", lambda target: fake_client)
    monkeypatch.setattr(
        migration.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [ProtectedFileRef(str(local_a), ("project_video",))],
    )
    monkeypatch.setattr(
        migration.tos_backup_storage,
        "backup_object_key_for_local_path",
        lambda local_path: "FILES/test/a.mp4",
    )
    monkeypatch.setattr(
        migration.tos_backup_storage,
        "ensure_local_copy_for_local_path",
        lambda local_path: SyncResult(str(local_path), "FILES/test/a.mp4", "synced", True, True),
    )

    summary = migration.copy_protected_files_to_channel()

    assert summary["actions"] == {"skipped_existing": 1}
    assert fake_client.uploaded == {}


def test_latest_mysql_dump_copies_to_mysqldump_prefix(monkeypatch, tmp_path):
    from appcore import tos_channel_migration as migration

    fake_client = FakeTargetClient()
    monkeypatch.setattr(migration, "load_tos_channel_config", lambda code: _fake_target_config())
    monkeypatch.setattr(migration, "_build_target_client", lambda target: fake_client)
    monkeypatch.setattr(
        migration.tos_backup_restore,
        "latest_db_dump_key",
        lambda: "DB/test/2026-05-13/appdb_2026-05-13_020000.sql.gz",
    )
    monkeypatch.setattr(migration.tos_backup_storage, "db_backup_prefix", lambda: "DB/test")
    monkeypatch.setattr(migration.config, "TOS_BACKUP_ENV", "test")

    def fake_download(object_key, local_path):
        Path(local_path).write_bytes(b"dump")
        return str(local_path)

    monkeypatch.setattr(migration.tos_backup_storage, "download_to_file", fake_download)

    summary = migration.copy_latest_mysql_dump_to_channel(output_dir=tmp_path)

    assert summary["source_object_key"] == "DB/test/2026-05-13/appdb_2026-05-13_020000.sql.gz"
    assert summary["target_object_key"] == "mysqldump/test/2026-05-13/appdb_2026-05-13_020000.sql.gz"
    assert summary["action"] == "uploaded"
    assert fake_client.uploaded[summary["target_object_key"]] == b"dump"
