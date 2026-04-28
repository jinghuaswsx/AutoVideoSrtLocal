from pathlib import Path


def test_tos_backup_sync_script_exposes_manual_modes():
    script = Path("scripts/tos_backup_sync.py")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "--files-only" in source
    assert "--db-only" in source
    assert "run_backup" in source


def test_tos_backup_restore_script_exposes_recovery_modes():
    script = Path("scripts/tos_backup_restore.py")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "--download-only" in source
    assert "--files-only" in source
    assert "--db-only" in source
    assert "run_restore" in source
