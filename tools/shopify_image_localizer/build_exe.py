from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.shopify_image_localizer import settings, version


APP_NAME = "ShopifyImageLocalizer"
PORTABLE_LAUNCHER_NAME = "run_shopify_image_localizer.bat"
RELEASE_VERSION_FILENAME = "release_version.txt"
DEFAULT_OUTPUT_ROOT = Path(r"G:\ShopifyRelease")
BUILD_WORK_DIR_NAME = "_build"


def _resolve_build_python(repo_root: Path) -> Path:
    return Path(sys.executable)


def _write_runtime_config(repo_root: Path, dist_root: Path) -> None:
    source_config = settings.config_path(repo_root)
    target_config = dist_root / settings.CONFIG_FILENAME
    if source_config.is_file():
        shutil.copy2(source_config, target_config)
        return

    settings.save_runtime_config(
        base_url=settings.default_base_url(packaged=True),
        api_key=settings.DEFAULT_API_KEY,
        browser_user_data_dir=settings.DEFAULT_BROWSER_USER_DATA_DIR,
        root=dist_root,
    )


def _write_portable_launcher(dist_root: Path) -> Path:
    launcher_path = dist_root / PORTABLE_LAUNCHER_NAME
    launcher_path.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal",
                "cd /d %~dp0",
                "if not exist \"%~dp0ShopifyImageLocalizer.exe\" (",
                "    echo [ShopifyImageLocalizer] missing ShopifyImageLocalizer.exe",
                "    pause",
                "    exit /b 1",
                ")",
                "\"%~dp0ShopifyImageLocalizer.exe\"",
                "set \"EXIT_CODE=%ERRORLEVEL%\"",
                "if not \"%EXIT_CODE%\"==\"0\" (",
                "    echo [ShopifyImageLocalizer] desktop client exited with code %EXIT_CODE%",
                "    pause",
                ")",
                "exit /b %EXIT_CODE%",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return launcher_path


def _normalize_release_version(release_version: str) -> str:
    normalized = str(release_version or "").strip().lstrip("vV")
    if not normalized:
        raise ValueError("release version cannot be empty")
    if any(ch in normalized for ch in ('/', "\\", ":", "*", "?", '"', "<", ">", "|")):
        raise ValueError(f"release version contains invalid filename characters: {release_version!r}")
    return normalized


def _release_dist_name(release_version: str) -> str:
    return f"{APP_NAME}-{_normalize_release_version(release_version)}"


def _release_dist_root(output_root: Path, release_version: str) -> Path:
    return output_root / _release_dist_name(release_version)


def _release_archive_path(output_root: Path, release_version: str) -> Path:
    normalized = _normalize_release_version(release_version)
    return output_root / f"{APP_NAME}-portable-{normalized}.zip"


def _write_release_version(dist_root: Path, release_version: str) -> Path:
    version_path = dist_root / RELEASE_VERSION_FILENAME
    version_path.write_text(f"{_normalize_release_version(release_version)}\n", encoding="utf-8")
    return version_path


def _build_portable_zip(dist_root: Path, archive_path: Path) -> Path:
    if archive_path.exists():
        raise FileExistsError(f"release archive already exists: {archive_path}")
    built_archive = shutil.make_archive(
        str(archive_path.with_suffix("")),
        "zip",
        root_dir=dist_root.parent,
        base_dir=dist_root.name,
    )
    return Path(built_archive)


def _prepare_build_dist_root(dist_root: Path) -> None:
    if dist_root.exists():
        shutil.rmtree(dist_root)


def _ensure_release_targets_available(release_root: Path, archive_path: Path) -> None:
    existing = [path for path in (release_root, archive_path) if path.exists()]
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise FileExistsError(
            f"release artifact already exists and will not be overwritten: {joined}. "
            "Use a new --version value for the next release."
        )


def _publish_release(build_dist_root: Path, release_root: Path) -> None:
    if release_root.exists():
        raise FileExistsError(f"release folder already exists: {release_root}")
    shutil.copytree(build_dist_root, release_root)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        default=version.RELEASE_VERSION,
        help=f"release version suffix, default: {version.RELEASE_VERSION}",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"release/build output root, default: {DEFAULT_OUTPUT_ROOT}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    release_version = _normalize_release_version(args.version)
    repo_root = Path(__file__).resolve().parents[2]
    output_root = Path(args.output_root).resolve()
    spec_path = repo_root / "tools" / "shopify_image_localizer" / "packaging" / "shopify_image_localizer.spec"
    python_exe = _resolve_build_python(repo_root)
    build_dist_root = output_root / APP_NAME
    build_work_root = output_root / BUILD_WORK_DIR_NAME
    release_root = _release_dist_root(output_root, release_version)
    archive_path = _release_archive_path(output_root, release_version)
    output_root.mkdir(parents=True, exist_ok=True)
    _ensure_release_targets_available(release_root, archive_path)
    _prepare_build_dist_root(build_dist_root)
    _prepare_build_dist_root(build_work_root)

    env = dict(os.environ)
    subprocess.run(
        [
            str(python_exe),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(output_root),
            "--workpath",
            str(build_work_root),
            str(spec_path),
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )

    _publish_release(build_dist_root, release_root)
    _write_runtime_config(repo_root, release_root)
    _write_portable_launcher(release_root)
    _write_release_version(release_root, release_version)
    _build_portable_zip(release_root, archive_path)
    shutil.rmtree(build_dist_root)


if __name__ == "__main__":
    main()
