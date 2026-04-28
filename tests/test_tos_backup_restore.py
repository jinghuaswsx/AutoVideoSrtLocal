from __future__ import annotations

import gzip
from pathlib import Path


def test_latest_db_dump_key_picks_newest_dump(monkeypatch):
    from appcore import tos_backup_restore

    monkeypatch.setattr(tos_backup_restore.tos_backup_storage, "db_backup_prefix", lambda: "DB/test")
    monkeypatch.setattr(
        tos_backup_restore.tos_backup_storage,
        "list_object_keys",
        lambda prefix: [
            "DB/test/2026-04-26/appdb_2026-04-26_020000.sql.gz",
            "DB/test/2026-04-27/appdb_2026-04-27_020000.sql.gz",
            "DB/test/not-a-date/appdb.sql.gz",
            "DB/prod/2026-04-28/appdb_2026-04-28_020000.sql.gz",
        ],
    )

    assert tos_backup_restore.latest_db_dump_key() == "DB/test/2026-04-27/appdb_2026-04-27_020000.sql.gz"


def test_download_latest_db_dump_downloads_to_output_dir(monkeypatch, tmp_path):
    from appcore import tos_backup_restore

    downloaded = []
    monkeypatch.setattr(tos_backup_restore, "latest_db_dump_key", lambda: "DB/test/2026-04-27/appdb.sql.gz")

    def fake_download(object_key, local_path):
        downloaded.append((object_key, Path(local_path)))
        Path(local_path).write_bytes(b"dump")
        return str(local_path)

    monkeypatch.setattr(tos_backup_restore.tos_backup_storage, "download_to_file", fake_download)

    result = tos_backup_restore.download_latest_db_dump(output_dir=tmp_path)

    assert result["object_key"] == "DB/test/2026-04-27/appdb.sql.gz"
    assert Path(result["local_file"]) == tmp_path / "appdb.sql.gz"
    assert downloaded == [("DB/test/2026-04-27/appdb.sql.gz", tmp_path / "appdb.sql.gz")]


def test_restore_mysql_dump_streams_gzip_to_mysql_without_password_arg(monkeypatch, tmp_path):
    from appcore import tos_backup_restore

    dump_path = tmp_path / "appdb.sql.gz"
    with gzip.open(dump_path, "wb") as handle:
        handle.write(b"CREATE TABLE demo(id int);")

    captured = {}

    class FakeStdin:
        def __init__(self):
            self.data = bytearray()
            self.closed = False

        def write(self, value):
            self.data.extend(value)
            return len(value)

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self, args, stdin=None, stderr=None, env=None):
            captured["args"] = args
            captured["env"] = env
            captured["stderr_handle"] = stderr
            self.stdin = FakeStdin()
            self.returncode = 0
            captured["process"] = self

        def wait(self):
            return self.returncode

    monkeypatch.setattr(tos_backup_restore.config, "MYSQL_BIN", "mysql")
    monkeypatch.setattr(tos_backup_restore.config, "DB_HOST", "127.0.0.1")
    monkeypatch.setattr(tos_backup_restore.config, "DB_PORT", 3306)
    monkeypatch.setattr(tos_backup_restore.config, "DB_USER", "root")
    monkeypatch.setattr(tos_backup_restore.config, "DB_PASSWORD", "secret")
    monkeypatch.setattr(tos_backup_restore.config, "DB_NAME", "auto_video")
    monkeypatch.setattr(tos_backup_restore.subprocess, "Popen", FakeProcess)

    result = tos_backup_restore.restore_mysql_dump(dump_path)

    assert result["local_file"] == str(dump_path)
    assert "--password=secret" not in captured["args"]
    assert captured["env"]["MYSQL_PWD"] == "secret"
    assert "auto_video" in captured["args"]
    assert bytes(captured["process"].stdin.data) == b"CREATE TABLE demo(id int);"
    assert captured["process"].stdin.closed is True


def test_restore_referenced_files_pulls_all_db_refs(monkeypatch, tmp_path):
    from appcore import tos_backup_restore
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_backup_storage import SyncResult

    local_a = tmp_path / "a.mp4"
    local_b = tmp_path / "b.jpg"
    monkeypatch.setattr(
        tos_backup_restore.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [
            ProtectedFileRef(str(local_a), ("project_video",)),
            ProtectedFileRef(str(local_b), ("product_detail_image",)),
        ],
    )

    def fake_ensure(local_path):
        path = Path(local_path)
        return SyncResult(
            local_path=str(path),
            object_key=f"FILES/test/{path.name}",
            action="downloaded",
            local_exists=False,
            remote_exists=True,
        )

    monkeypatch.setattr(tos_backup_restore.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure)

    summary = tos_backup_restore.restore_referenced_files()

    assert summary["files_checked"] == 2
    assert summary["actions"] == {"downloaded": 2}
    assert summary["failed"] == 0


def test_run_restore_restores_db_before_files(monkeypatch, tmp_path):
    from appcore import tos_backup_restore

    calls = []
    dump_path = tmp_path / "appdb.sql.gz"

    monkeypatch.setattr(
        tos_backup_restore,
        "download_latest_db_dump",
        lambda output_dir=None: calls.append("download_db") or {"local_file": str(dump_path), "object_key": "DB/test/appdb.sql.gz"},
    )
    monkeypatch.setattr(
        tos_backup_restore,
        "restore_mysql_dump",
        lambda local_file: calls.append(("restore_db", Path(local_file))) or {"local_file": str(local_file)},
    )
    monkeypatch.setattr(
        tos_backup_restore,
        "restore_referenced_files",
        lambda: calls.append("restore_files") or {"files_checked": 1, "failed": 0},
    )

    summary = tos_backup_restore.run_restore(output_dir=tmp_path)

    assert calls == ["download_db", ("restore_db", dump_path), "restore_files"]
    assert summary["status"] == "success"
    assert summary["db_dump"]["object_key"] == "DB/test/appdb.sql.gz"
    assert summary["files"]["files_checked"] == 1


def test_run_restore_marks_failed_when_file_restore_has_failures(monkeypatch, tmp_path):
    from appcore import tos_backup_restore

    monkeypatch.setattr(
        tos_backup_restore,
        "restore_referenced_files",
        lambda: {"files_checked": 2, "failed": 1},
    )

    summary = tos_backup_restore.run_restore(output_dir=tmp_path, restore_db=False, restore_files=True)

    assert summary["status"] == "failed"
    assert summary["error_message"] == "file restore failed for 1 protected files"
