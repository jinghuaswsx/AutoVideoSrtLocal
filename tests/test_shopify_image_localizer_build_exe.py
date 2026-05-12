from __future__ import annotations

import json
import zipfile

import pytest

from tools.shopify_image_localizer import build_exe


def test_shopify_build_release_paths_are_versioned(tmp_path):
    assert build_exe._release_dist_root(tmp_path, "1.0") == tmp_path / "ShopifyImageLocalizer-1.0"
    assert build_exe._release_archive_path(tmp_path, "1.0") == tmp_path / "ShopifyImageLocalizer-portable-1.0.zip"
    assert build_exe._release_dist_root(tmp_path, "v2.0").name == "ShopifyImageLocalizer-2.0"


def test_shopify_build_default_output_root_matches_platform():
    expected = (
        build_exe.DEFAULT_OUTPUT_ROOT_WINDOWS
        if build_exe.os.name == "nt"
        else build_exe.DEFAULT_OUTPUT_ROOT_POSIX
    )
    assert build_exe._default_output_root() == expected


def test_shopify_build_portable_zip_keeps_versioned_folder(tmp_path):
    dist_root = tmp_path / "dist" / "ShopifyImageLocalizer-1.0"
    dist_root.mkdir(parents=True)
    (dist_root / "ShopifyImageLocalizer.exe").write_text("exe", encoding="utf-8")
    (dist_root / "release_version.txt").write_text("1.0\n", encoding="utf-8")

    archive_path = build_exe._build_portable_zip(
        dist_root,
        tmp_path / "dist" / "ShopifyImageLocalizer-portable-1.0.zip",
    )

    assert archive_path.name == "ShopifyImageLocalizer-portable-1.0.zip"
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "ShopifyImageLocalizer-1.0/ShopifyImageLocalizer.exe" in names
    assert "ShopifyImageLocalizer-1.0/release_version.txt" in names


def test_shopify_build_rejects_existing_release_artifacts(tmp_path):
    release_root = tmp_path / "dist" / "ShopifyImageLocalizer-1.0"
    archive_path = tmp_path / "dist" / "ShopifyImageLocalizer-portable-1.0.zip"
    release_root.mkdir(parents=True)

    with pytest.raises(FileExistsError, match="will not be overwritten"):
        build_exe._ensure_release_targets_available(release_root, archive_path)


def test_shopify_build_rejects_generated_runtime_config_without_api_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    dist_root.mkdir()
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "")

    with pytest.raises(ValueError, match="SHOPIFY_IMAGE_LOCALIZER_API_KEY"):
        build_exe._write_runtime_config(repo_root, dist_root)


def test_shopify_build_rejects_empty_source_runtime_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    dist_root.mkdir()
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "")
    build_exe.settings.config_path(repo_root).write_text(
        json.dumps(
            {
                "base_url": "http://172.30.254.14",
                "api_key": "",
                "browser_user_data_dir": r"C:\chrome-shopify-image",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="api_key"):
        build_exe._write_runtime_config(repo_root, dist_root)


def test_shopify_build_rejects_demo_source_runtime_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    dist_root.mkdir()
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "")
    build_exe.settings.config_path(repo_root).write_text(
        json.dumps(
            {
                "base_url": "http://172.30.254.14",
                "api_key": "demo-key",
                "browser_user_data_dir": r"C:\chrome-shopify-image",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="demo-key"):
        build_exe._write_runtime_config(repo_root, dist_root)


def test_shopify_build_prefers_env_api_key_over_source_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    dist_root.mkdir()
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "fresh-env-openapi-key")
    build_exe.settings.config_path(repo_root).write_text(
        json.dumps(
            {
                "base_url": "http://172.30.254.14",
                "api_key": "stale-source-openapi-key",
                "browser_user_data_dir": r"C:\chrome-shopify-image",
            }
        ),
        encoding="utf-8",
    )

    build_exe._write_runtime_config(repo_root, dist_root)

    runtime_payload = json.loads(build_exe.settings.config_path(dist_root).read_text(encoding="utf-8"))
    default_payload = json.loads(build_exe.settings.default_config_path(dist_root).read_text(encoding="utf-8"))
    assert runtime_payload["api_key"] == "fresh-env-openapi-key"
    assert default_payload == runtime_payload


def test_shopify_build_writes_runtime_and_default_configs(tmp_path, monkeypatch: pytest.MonkeyPatch):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    dist_root.mkdir()
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "packaged-openapi-key")

    build_exe._write_runtime_config(repo_root, dist_root)

    runtime_payload = json.loads(build_exe.settings.config_path(dist_root).read_text(encoding="utf-8"))
    default_payload = json.loads(build_exe.settings.default_config_path(dist_root).read_text(encoding="utf-8"))
    assert runtime_payload["api_key"] == "packaged-openapi-key"
    assert runtime_payload["browser_user_data_dir"] == r"C:\chrome-shopify-image"
    assert default_payload == runtime_payload
