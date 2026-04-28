from __future__ import annotations

from datetime import datetime
from pathlib import Path


def test_sync_protected_files_reconciles_all_refs(monkeypatch, tmp_path):
    from appcore import tos_backup_job
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_backup_storage import SyncResult

    local_a = tmp_path / "a.mp4"
    local_b = tmp_path / "b.jpg"
    monkeypatch.setattr(
        tos_backup_job.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [
            ProtectedFileRef(str(local_a), ("project_video",)),
            ProtectedFileRef(str(local_b), ("product_detail_image",)),
        ],
    )

    def fake_reconcile(local_path):
        path = Path(local_path)
        return SyncResult(
            local_path=str(path),
            object_key=f"FILES/test/{path.name}",
            action="uploaded" if path.name == "a.mp4" else "downloaded",
            local_exists=path.name == "a.mp4",
            remote_exists=path.name == "b.jpg",
        )

    monkeypatch.setattr(tos_backup_job.tos_backup_storage, "reconcile_local_file", fake_reconcile)

    summary = tos_backup_job.sync_protected_files()

    assert summary["files_checked"] == 2
    assert summary["actions"] == {"uploaded": 1, "downloaded": 1}
    assert summary["failed"] == 0


def test_db_dump_key_uses_previous_day_under_db_prefix(monkeypatch):
    from appcore import tos_backup_job

    monkeypatch.setattr(tos_backup_job.config, "DB_NAME", "appdb")
    monkeypatch.setattr(tos_backup_job.tos_backup_storage, "db_backup_prefix", lambda: "DB/test")

    key = tos_backup_job.build_db_dump_key(
        backup_date=tos_backup_job.previous_backup_date(datetime(2026, 4, 28, 2, 0, 0)),
        run_time=datetime(2026, 4, 28, 2, 0, 0),
    )

    assert key == "DB/test/2026-04-27/appdb_2026-04-27_020000.sql.gz"


def test_upload_mysql_dump_uploads_dump_to_db_key(monkeypatch, tmp_path):
    from appcore import tos_backup_job

    dump_path = tmp_path / "dump.sql.gz"
    dump_path.write_bytes(b"gz")
    uploaded = []

    monkeypatch.setattr(tos_backup_job.config, "DB_NAME", "appdb")
    monkeypatch.setattr(tos_backup_job.tos_backup_storage, "db_backup_prefix", lambda: "DB/test")
    monkeypatch.setattr(tos_backup_job, "dump_mysql_to_file", lambda backup_date, run_time=None, output_dir=None: dump_path)
    monkeypatch.setattr(
        tos_backup_job.tos_backup_storage,
        "upload_local_file",
        lambda local_path, object_key: uploaded.append((Path(local_path), object_key)) or object_key,
    )

    result = tos_backup_job.upload_mysql_dump(run_time=datetime(2026, 4, 28, 2, 0, 0))

    assert uploaded == [(dump_path, "DB/test/2026-04-27/appdb_2026-04-27_020000.sql.gz")]
    assert result["backup_date"] == "2026-04-27"
    assert result["object_key"] == "DB/test/2026-04-27/appdb_2026-04-27_020000.sql.gz"


def test_cleanup_expired_db_dumps_deletes_only_older_than_retention(monkeypatch):
    from appcore import tos_backup_job

    deleted = []
    monkeypatch.setattr(tos_backup_job.tos_backup_storage, "db_backup_prefix", lambda: "DB/test")
    monkeypatch.setattr(
        tos_backup_job.tos_backup_storage,
        "list_object_keys",
        lambda prefix: [
            "DB/test/2026-04-20/appdb.sql.gz",
            "DB/test/2026-04-21/appdb.sql.gz",
            "DB/test/not-a-date/appdb.sql.gz",
        ],
    )
    monkeypatch.setattr(tos_backup_job.tos_backup_storage, "delete_object", lambda key: deleted.append(key))

    summary = tos_backup_job.cleanup_expired_db_dumps(
        run_time=datetime(2026, 4, 28, 2, 0, 0),
        retention_days=7,
    )

    assert summary == {"db_dumps_scanned": 3, "db_dumps_deleted": 1}
    assert deleted == ["DB/test/2026-04-20/appdb.sql.gz"]


def test_run_scheduled_backup_marks_failed_when_file_sync_has_failures(monkeypatch):
    from appcore import tos_backup_job

    finished = []
    monkeypatch.setattr(tos_backup_job, "_start_scheduled_run", lambda scheduled_for=None: 42)
    monkeypatch.setattr(
        tos_backup_job,
        "run_backup",
        lambda run_time=None: {
            "skipped": False,
            "files": {"files_checked": 3, "failed": 2},
            "db_dump": {"object_key": "DB/test/2026-04-27/appdb.sql.gz"},
        },
    )
    monkeypatch.setattr(
        tos_backup_job,
        "_finish_scheduled_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )

    summary = tos_backup_job.run_scheduled_backup(scheduled_for=datetime(2026, 4, 28, 2, 0, 0))

    assert summary["files"]["failed"] == 2
    assert finished == [
        (
            42,
            {
                "status": "failed",
                "summary": summary,
                "error_message": "file sync failed for 2 protected files",
                "output_file": "DB/test/2026-04-27/appdb.sql.gz",
            },
        )
    ]
