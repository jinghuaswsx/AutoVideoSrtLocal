from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools import meta_daily_final_sync


def test_ensure_export_dir_creates_clean_path(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    target = tmp_path / "2026-05-08" / "20260509_001149" / "newjoyloo_bak"
    recovery: list[dict] = []

    result = meta_daily_final_sync._ensure_export_dir(target, recovery)

    assert result == target
    assert target.is_dir()
    assert recovery == []


def test_ensure_export_dir_relocates_root_owned_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    blocker = tmp_path / "2026-05-08"
    blocker.mkdir()
    sentinel = blocker / "marker.txt"
    sentinel.write_text("legacy", encoding="utf-8")
    blocker.chmod(0o500)  # r-x only — mkdir under it raises PermissionError
    try:
        target = blocker / "20260509_001149" / "newjoyloo_bak"
        recovery: list[dict] = []

        result = meta_daily_final_sync._ensure_export_dir(target, recovery)

        assert result == target
        assert target.is_dir()
        relocated = next(
            (Path(item["relocated_to"]) for item in recovery if item.get("blocker") == str(blocker)),
            None,
        )
        assert relocated is not None
        assert relocated.exists()
        assert relocated.name.startswith("2026-05-08.conflicted-")
        # Legacy contents preserved under the relocated name (not deleted).
        assert (relocated / "marker.txt").read_text() == "legacy"
        # `blocker` now exists again as a fresh writable dir created by the
        # retried mkdir(parents=True). It is NOT the old inode.
        assert blocker.exists()
        assert blocker.stat().st_ino != relocated.stat().st_ino
        assert os.access(blocker, os.W_OK)
    finally:
        relocated_path = next(
            (Path(item["relocated_to"]) for item in recovery if item.get("blocker") == str(blocker)),
            None,
        )
        if relocated_path is not None and relocated_path.exists():
            relocated_path.chmod(0o755)


def test_ensure_export_dir_reraises_when_recovery_also_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    blocker = tmp_path / "2026-05-08"
    blocker.mkdir()
    blocker.chmod(0o500)
    target = blocker / "20260509_001149" / "newjoyloo_bak"

    def fail_rename(self, target_path):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "rename", fail_rename)

    try:
        with pytest.raises(PermissionError) as excinfo:
            meta_daily_final_sync._ensure_export_dir(target, recovery_log=[])
        assert str(blocker) in str(excinfo.value)
    finally:
        blocker.chmod(0o755)


def test_ensure_export_dir_refuses_to_relocate_export_root(tmp_path, monkeypatch):
    """Defense-in-depth: if the entire root is unwritable we must not
    rename the root itself — that would orphan all historical exports."""
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            meta_daily_final_sync._ensure_export_dir(tmp_path / "x" / "y", recovery_log=[])
        assert tmp_path.exists()
    finally:
        tmp_path.chmod(0o755)


def test_ensure_export_dir_skips_when_no_writable_blocker_found(tmp_path, monkeypatch):
    """If we hit PermissionError but cannot identify a fixable blocker
    inside our root, re-raise — silent fallback would mask a real
    misconfiguration outside the export tree."""
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    def fake_mkdir(self, *args, **kwargs):
        raise PermissionError(13, "boom", "/some/path/outside/tree")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    with pytest.raises(PermissionError):
        meta_daily_final_sync._ensure_export_dir(tmp_path / "a" / "b", recovery_log=[])
