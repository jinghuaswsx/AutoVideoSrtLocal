from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from link_check_desktop import settings


APP_NAME = "LinkCheckDesktop"
PLAYWRIGHT_RUNTIME_DIR = ".playwright-runtime"
PLAYWRIGHT_DIST_DIR = "ms-playwright"
PORTABLE_LAUNCHER_NAME = "run_link_check_desktop.bat"


def _resolve_build_python(repo_root: Path) -> Path:
    candidate = repo_root / ".venv_link_check_build" / "Scripts" / "python.exe"
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def _version_key(path: Path) -> tuple[int, str]:
    suffix = path.name.split("-")[-1]
    return (int(suffix) if suffix.isdigit() else -1, path.name)


def _find_chromium_runtime_root(runtime_root: Path) -> Path | None:
    candidates: list[Path] = []
    for browser_dir in sorted(runtime_root.glob("chromium-*"), key=_version_key, reverse=True):
        for relative in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
            if (browser_dir / relative).is_file():
                candidates.append(browser_dir)
                break
    return candidates[0] if candidates else None


def _playwright_runtime_root(repo_root: Path) -> Path:
    return repo_root / PLAYWRIGHT_RUNTIME_DIR


def _ensure_playwright_runtime(repo_root: Path, python_exe: Path) -> Path:
    runtime_root = _playwright_runtime_root(repo_root)
    runtime_root.mkdir(parents=True, exist_ok=True)
    if _find_chromium_runtime_root(runtime_root) is not None:
        return runtime_root

    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(runtime_root)
    subprocess.run(
        [str(python_exe), "-s", "-m", "playwright", "install", "chromium"],
        cwd=repo_root,
        env=env,
        check=True,
    )
    return runtime_root


def _copytree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _write_runtime_config(repo_root: Path, dist_root: Path) -> None:
    source_config = settings.config_path(repo_root)
    target_config = dist_root / settings.CONFIG_FILENAME
    if source_config.is_file():
        shutil.copy2(source_config, target_config)
        return

    settings.save_runtime_config(
        base_url=settings.DEFAULT_BASE_URL,
        api_key=settings.DEFAULT_API_KEY,
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
                "if not exist \"%~dp0LinkCheckDesktop.exe\" (",
                "    echo [LinkCheckDesktop] missing LinkCheckDesktop.exe",
                "    pause",
                "    exit /b 1",
                ")",
                "\"%~dp0LinkCheckDesktop.exe\"",
                "set \"EXIT_CODE=%ERRORLEVEL%\"",
                "if not \"%EXIT_CODE%\"==\"0\" (",
                "    echo [LinkCheckDesktop] desktop client exited with code %EXIT_CODE%",
                "    pause",
                ")",
                "exit /b %EXIT_CODE%",
                "",
            ],
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

    try:
        shutil.rmtree(dist_root)
    except PermissionError as exc:
        raise RuntimeError(
            f"打包失败：{dist_root} 正在被占用。请先关闭正在运行的 LinkCheckDesktop.exe，再重新执行打包。"
        ) from exc


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "link_check_desktop" / "packaging" / "link_check_desktop.spec"
    python_exe = _resolve_build_python(repo_root)
    runtime_root = _ensure_playwright_runtime(repo_root, python_exe)
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

    _copytree_clean(runtime_root, dist_root / PLAYWRIGHT_DIST_DIR)
    _write_runtime_config(repo_root, dist_root)
    _write_portable_launcher(dist_root)
    _build_portable_zip(dist_root)


if __name__ == "__main__":
    main()
