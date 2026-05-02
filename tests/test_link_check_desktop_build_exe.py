from __future__ import annotations

import json
import zipfile


def test_find_chromium_runtime_root_prefers_latest_version(tmp_path):
    from link_check_desktop import build_exe

    old_root = tmp_path / "chromium-1208" / "chrome-win64"
    old_root.mkdir(parents=True)
    (old_root / "chrome.exe").write_bytes(b"old")

    new_root = tmp_path / "chromium-1217" / "chrome-win64"
    new_root.mkdir(parents=True)
    (new_root / "chrome.exe").write_bytes(b"new")

    resolved = build_exe._find_chromium_runtime_root(tmp_path)

    assert resolved == tmp_path / "chromium-1217"


def test_write_runtime_config_copies_existing_config(tmp_path):
    from link_check_desktop import build_exe

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_config = repo_root / "link_check_desktop_config.json"
    source_config.write_text(
        json.dumps({
            "base_url": "http://172.30.254.14",
            "api_key": "demo-key",
        }),
        encoding="utf-8",
    )

    dist_root = tmp_path / "dist" / "LinkCheckDesktop"
    dist_root.mkdir(parents=True)

    build_exe._write_runtime_config(repo_root, dist_root)

    payload = json.loads((dist_root / "link_check_desktop_config.json").read_text(encoding="utf-8"))
    assert payload["base_url"] == "http://172.30.254.14"
    assert payload["api_key"] == "demo-key"


def test_write_portable_launcher_points_to_bundled_exe(tmp_path):
    from link_check_desktop import build_exe

    dist_root = tmp_path / "dist" / "LinkCheckDesktop"
    dist_root.mkdir(parents=True)

    launcher = build_exe._write_portable_launcher(dist_root)

    content = launcher.read_text(encoding="utf-8")
    assert launcher.name == "run_link_check_desktop.bat"
    assert "LinkCheckDesktop.exe" in content
    assert "desktop client exited with code" in content


def test_build_portable_zip_includes_generated_artifacts(tmp_path):
    from link_check_desktop import build_exe

    dist_root = tmp_path / "dist" / "LinkCheckDesktop"
    dist_root.mkdir(parents=True)
    (dist_root / "LinkCheckDesktop.exe").write_bytes(b"exe")
    (dist_root / "link_check_desktop_config.json").write_text("{}", encoding="utf-8")

    archive_path = build_exe._build_portable_zip(dist_root)

    assert archive_path.name == "LinkCheckDesktop-portable.zip"
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
    assert "LinkCheckDesktop/LinkCheckDesktop.exe" in names
    assert "LinkCheckDesktop/link_check_desktop_config.json" in names


def test_prepare_dist_root_removes_existing_directory(tmp_path):
    from link_check_desktop import build_exe

    dist_root = tmp_path / "dist" / "LinkCheckDesktop"
    dist_root.mkdir(parents=True)
    (dist_root / "stale.txt").write_text("stale", encoding="utf-8")

    build_exe._prepare_dist_root(dist_root)

    assert not dist_root.exists()
