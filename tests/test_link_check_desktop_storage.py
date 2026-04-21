from __future__ import annotations

from datetime import datetime


def test_create_workspace_uses_product_id_and_timestamp(monkeypatch, tmp_path):
    from link_check_desktop import storage

    monkeypatch.setattr(storage, "executable_root", lambda: tmp_path)

    workspace = storage.create_workspace(
        123,
        now=datetime(2026, 4, 21, 15, 4, 5),
    )

    assert workspace.root == tmp_path / "img" / "123-20260421150405"
    assert workspace.reference_dir.is_dir()
    assert workspace.site_dir.is_dir()
    assert workspace.compare_dir.is_dir()


def test_executable_root_uses_frozen_executable_directory(monkeypatch, tmp_path):
    from link_check_desktop import storage

    monkeypatch.setattr(storage.sys, "frozen", True, raising=False)
    monkeypatch.setattr(storage.sys, "executable", str(tmp_path / "LinkCheckDesktop.exe"))

    assert storage.executable_root() == tmp_path


def test_write_json_persists_utf8_payload(tmp_path):
    from link_check_desktop import storage

    output = tmp_path / "task.json"
    storage.write_json(output, {"message": "最小闭环", "count": 1})

    assert output.read_text(encoding="utf-8") == (
        '{\n'
        '  "message": "最小闭环",\n'
        '  "count": 1\n'
        '}'
    )
