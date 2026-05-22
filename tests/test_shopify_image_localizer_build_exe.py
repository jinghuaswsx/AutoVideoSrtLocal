from __future__ import annotations

import json
import subprocess
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


def test_shopify_build_release_preflight_requires_standard_ack(tmp_path):
    with pytest.raises(RuntimeError, match="release standard"):
        build_exe._validate_release_preflight(
            tmp_path,
            release_version=build_exe.version.RELEASE_VERSION,
            release_standard_read=False,
        )


def test_shopify_build_release_preflight_rejects_linked_worktree(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    standard = tmp_path / build_exe.RELEASE_STANDARD_RELATIVE_PATH
    standard.parent.mkdir(parents=True)
    standard.write_text("standard", encoding="utf-8")

    def fake_git_output(repo_root, *args):
        responses = {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("rev-parse", "--abbrev-ref", "HEAD"): "master",
            ("rev-parse", "--git-dir"): str(tmp_path / ".git" / "worktrees" / "build"),
            ("rev-parse", "--git-common-dir"): str(tmp_path / ".git"),
            ("status", "--porcelain", "--untracked-files=no"): "",
            ("rev-parse", "HEAD"): "abc123",
            ("rev-parse", "origin/master"): "abc123",
        }
        return responses[args]

    monkeypatch.setattr(build_exe, "_git_output", fake_git_output)

    with pytest.raises(RuntimeError, match="worktree"):
        build_exe._validate_release_preflight(
            tmp_path,
            release_version=build_exe.version.RELEASE_VERSION,
            release_standard_read=True,
        )


def test_shopify_build_release_preflight_rejects_version_mismatch(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    standard = tmp_path / build_exe.RELEASE_STANDARD_RELATIVE_PATH
    standard.parent.mkdir(parents=True)
    standard.write_text("standard", encoding="utf-8")

    def fake_git_output(repo_root, *args):
        responses = {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("rev-parse", "--abbrev-ref", "HEAD"): "master",
            ("rev-parse", "--git-dir"): str(tmp_path / ".git"),
            ("rev-parse", "--git-common-dir"): str(tmp_path / ".git"),
            ("status", "--porcelain", "--untracked-files=no"): "",
            ("rev-parse", "HEAD"): "abc123",
            ("rev-parse", "origin/master"): "abc123",
        }
        return responses[args]

    monkeypatch.setattr(build_exe, "_git_output", fake_git_output)

    with pytest.raises(RuntimeError, match="version.py"):
        build_exe._validate_release_preflight(
            tmp_path,
            release_version="999.0",
            release_standard_read=True,
        )


def test_shopify_build_release_preflight_accepts_clean_current_master(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    standard = tmp_path / build_exe.RELEASE_STANDARD_RELATIVE_PATH
    standard.parent.mkdir(parents=True)
    standard.write_text("standard", encoding="utf-8")

    def fake_git_output(repo_root, *args):
        responses = {
            ("rev-parse", "--show-toplevel"): str(tmp_path),
            ("rev-parse", "--abbrev-ref", "HEAD"): "master",
            ("rev-parse", "--git-dir"): str(tmp_path / ".git"),
            ("rev-parse", "--git-common-dir"): str(tmp_path / ".git"),
            ("status", "--porcelain", "--untracked-files=no"): "",
            ("rev-parse", "HEAD"): "abc123",
            ("rev-parse", "origin/master"): "abc123",
        }
        return responses[args]

    monkeypatch.setattr(build_exe, "_git_output", fake_git_output)

    build_exe._validate_release_preflight(
        tmp_path,
        release_version=build_exe.version.RELEASE_VERSION,
        release_standard_read=True,
    )


def test_shopify_build_release_preflight_accepts_verified_env_when_git_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    standard = tmp_path / build_exe.RELEASE_STANDARD_RELATIVE_PATH
    standard.parent.mkdir(parents=True)
    standard.write_text("standard", encoding="utf-8")
    git_dir = tmp_path / ".git"

    def missing_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "check_output", missing_git)
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_TOP", str(tmp_path))
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_BRANCH", "master")
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_DIR", str(git_dir))
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_COMMON_DIR", str(git_dir))
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_STATUS", "")
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_HEAD", "abc123")
    monkeypatch.setenv("SHOPIFY_LOCALIZER_GIT_ORIGIN_MASTER", "abc123")

    build_exe._validate_release_preflight(
        tmp_path,
        release_version=build_exe.version.RELEASE_VERSION,
        release_standard_read=True,
    )


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
                "base_url": "https://autovideosrt.example.test",
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
                "base_url": "https://autovideosrt.example.test",
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
                "base_url": "https://autovideosrt.example.test",
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


def test_shopify_build_writes_internal_default_config_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = tmp_path / "repo"
    dist_root = tmp_path / "dist"
    repo_root.mkdir()
    (dist_root / "_internal").mkdir(parents=True)
    monkeypatch.setattr(build_exe.settings, "DEFAULT_API_KEY", "packaged-openapi-key")

    build_exe._write_runtime_config(repo_root, dist_root)

    runtime_payload = json.loads(build_exe.settings.config_path(dist_root).read_text(encoding="utf-8"))
    internal_payload = json.loads(
        (dist_root / "_internal" / build_exe.settings.DEFAULT_CONFIG_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert internal_payload == runtime_payload
