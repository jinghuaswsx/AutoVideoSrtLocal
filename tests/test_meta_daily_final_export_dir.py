from __future__ import annotations

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
