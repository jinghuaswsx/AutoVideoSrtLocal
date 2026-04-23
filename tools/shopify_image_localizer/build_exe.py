from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.shopify_image_localizer import settings


APP_NAME = "ShopifyImageLocalizer"
PORTABLE_LAUNCHER_NAME = "run_shopify_image_localizer.bat"


def _resolve_build_python(repo_root: Path) -> Path:
    return Path(sys.executable)


def _write_runtime_config(repo_root: Path, dist_root: Path) -> None:
    source_config = settings.config_path(repo_root)
    target_config = dist_root / settings.CONFIG_FILENAME
    if source_config.is_file():
        shutil.copy2(source_config, target_config)
        return

    settings.save_runtime_config(
        base_url=settings.DEFAULT_BASE_URL,
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


def _build_portable_zip(dist_root: Path) -> Path:
    archive_path = dist_root.parent / f"{APP_NAME}-portable.zip"
    if archive_path.exists():
        archive_path.unlink()
    built_archive = shutil.make_archive(
        str(archive_path.with_suffix("")),
        "zip",
        root_dir=dist_root.parent,
        base_dir=dist_root.name,
    )
    return Path(built_archive)


def _prepare_dist_root(dist_root: Path) -> None:
    if not dist_root.exists():
        return
    shutil.rmtree(dist_root)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    spec_path = repo_root / "tools" / "shopify_image_localizer" / "packaging" / "shopify_image_localizer.spec"
    python_exe = _resolve_build_python(repo_root)
    dist_root = repo_root / "dist" / APP_NAME
    _prepare_dist_root(dist_root)

    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run(
        [str(python_exe), "-s", "-m", "PyInstaller", "--noconfirm", str(spec_path)],
        cwd=repo_root,
        env=env,
        check=True,
    )

    _write_runtime_config(repo_root, dist_root)
    _write_portable_launcher(dist_root)
    _build_portable_zip(dist_root)


if __name__ == "__main__":
    main()
