from pathlib import Path


def test_tos_backup_sync_script_exposes_manual_modes():
    script = Path("scripts/tos_backup_sync.py")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "--files-only" in source
    assert "--db-only" in source
    assert "run_backup" in source
