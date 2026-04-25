from __future__ import annotations

import zipfile

import pytest

from tools.shopify_image_localizer import build_exe


def test_shopify_build_release_paths_are_versioned(tmp_path):
    assert build_exe._release_dist_root(tmp_path, "1.0") == tmp_path / "ShopifyImageLocalizer-1.0"
    assert build_exe._release_archive_path(tmp_path, "1.0") == tmp_path / "ShopifyImageLocalizer-portable-1.0.zip"
    assert build_exe._release_dist_root(tmp_path, "v2.0").name == "ShopifyImageLocalizer-2.0"


def test_shopify_build_defaults_to_clean_release_root():
    assert build_exe.DEFAULT_OUTPUT_ROOT == build_exe.Path(r"G:\ShopifyRelease")


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
