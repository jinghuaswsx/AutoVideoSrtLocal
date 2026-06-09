#!/usr/bin/env python3
"""Bootstrap and check the shared Codex worktree Python environment."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


IMPORT_CHECK = """
import bs4
import dbutils
import flask
import pytest
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
print("imports ok")
"""

PROTECTED_VENV_PATHS = {
    Path("/opt/autovideosrt/venv"),
    Path("/opt/autovideosrt-test/venv"),
}

GENERATED_DIRS = {
    ".pytest_cache",
    "playwright-report",
    "test-results",
}

RUNTIME_DIRS = {
    "media_store",
    "output",
    "uploads",
}


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, env=env, check=True)


def capture(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def repo_root() -> Path:
    return Path(capture(["git", "rev-parse", "--show-toplevel"])).resolve()


def default_base_venv() -> Path:
    return Path(os.environ.get("AUTOVIDEOSRT_CODEX_VENV", "")).expanduser() if os.environ.get("AUTOVIDEOSRT_CODEX_VENV") else Path.home() / ".cache" / "autovideosrt" / "codex-venv-py312"


def default_browser_cache() -> Path:
    return Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")).expanduser() if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else Path.home() / ".cache" / "ms-playwright"


def bin_path(venv: Path, command: str) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / folder / f"{command}{suffix}"


def python_path(venv: Path) -> Path:
    return bin_path(venv, "python")


def pip_path(venv: Path) -> Path:
    return bin_path(venv, "pip")


def protected_path_message(path: Path) -> str | None:
    resolved = path.expanduser().resolve(strict=False)
    for protected in PROTECTED_VENV_PATHS:
        protected_resolved = protected.resolve(strict=False)
        if resolved == protected_resolved:
            return f"{resolved} is reserved for service runtime; choose a Codex dev venv path."
    return None


def ensure_dev_venv_path(path: Path) -> None:
    message = protected_path_message(path)
    if message:
        raise SystemExit(message)


def ensure_base_venv(path: Path) -> None:
    ensure_dev_venv_path(path)
    if (path / "pyvenv.cfg").exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "venv", str(path)])


def ensure_worktree_symlink(root: Path, base_venv: Path, *, force: bool) -> None:
    link = root / ".venv"
    if link.is_symlink():
        current = link.resolve(strict=False)
        target = base_venv.resolve(strict=False)
        if current == target:
            print(f".venv already points to {target}")
            return
        if not force:
            raise SystemExit(f"{link} points to {current}; pass --force to replace the symlink.")
        link.unlink()
    elif link.exists():
        raise SystemExit(f"{link} exists and is not a symlink; refusing to remove it automatically.")

    link.symlink_to(base_venv, target_is_directory=True)
    print(f"created {link} -> {base_venv}")


def playwright_env(browser_cache: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    return env


def bootstrap(args: argparse.Namespace) -> None:
    root = repo_root()
    base_venv = args.venv.expanduser().resolve(strict=False)
    browser_cache = args.playwright_browsers_path.expanduser().resolve(strict=False)
    requirements = (root / args.requirements).resolve(strict=False)
    if not requirements.exists():
        raise SystemExit(f"requirements file not found: {requirements}")

    ensure_base_venv(base_venv)
    run([str(pip_path(base_venv)), "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(pip_path(base_venv)), "install", "-r", str(requirements)])

    if not args.skip_playwright_install:
        browser_cache.mkdir(parents=True, exist_ok=True)
        run(
            [str(python_path(base_venv)), "-m", "playwright", "install", "chromium"],
            env=playwright_env(browser_cache),
        )

    ensure_worktree_symlink(root, base_venv, force=args.force)
    print(f"base venv: {base_venv}")
    print(f"browser cache: {browser_cache}")


def check_chromium_cache(browser_cache: Path) -> None:
    if not browser_cache.exists():
        raise SystemExit(f"Playwright browser cache does not exist: {browser_cache}")
    markers = list(browser_cache.glob("chromium-*")) + list(browser_cache.glob("chromium_headless_shell-*"))
    if not markers:
        raise SystemExit(f"Playwright Chromium is not installed under {browser_cache}")
    print(f"chromium cache ok: {browser_cache}")


def check(args: argparse.Namespace) -> None:
    root = repo_root()
    venv = root / ".venv"
    if not venv.exists():
        raise SystemExit(f"{venv} is missing; run scripts/worktree_env.py bootstrap first.")
    py = python_path(venv)
    if not py.exists():
        raise SystemExit(f"{py} is missing.")

    browser_cache = args.playwright_browsers_path.expanduser().resolve(strict=False)
    run([str(py), "-c", IMPORT_CHECK])
    run([str(py), "-m", "pytest", "--version"])
    run([str(py), "-m", "playwright", "--version"], env=playwright_env(browser_cache))
    check_chromium_cache(browser_cache)


def remove_path(path: Path, *, dry_run: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    action = "would remove" if dry_run else "remove"
    print(f"{action}: {path}")
    if dry_run:
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def cleanup(args: argparse.Namespace) -> None:
    root = repo_root()
    remove_path(root / ".venv", dry_run=args.dry_run)
    for name in sorted(GENERATED_DIRS):
        remove_path(root / name, dry_run=args.dry_run)
    for pycache in root.rglob("__pycache__"):
        remove_path(pycache, dry_run=args.dry_run)
    if args.include_runtime_dirs:
        for name in sorted(RUNTIME_DIRS):
            remove_path(root / name, dry_run=args.dry_run)
    print("shared base venv and Playwright browser cache were kept.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--venv", type=Path, default=default_base_venv())
    bootstrap_parser.add_argument("--requirements", default="requirements-dev.txt")
    bootstrap_parser.add_argument("--playwright-browsers-path", type=Path, default=default_browser_cache())
    bootstrap_parser.add_argument("--skip-playwright-install", action="store_true")
    bootstrap_parser.add_argument("--force", action="store_true")
    bootstrap_parser.set_defaults(func=bootstrap)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--playwright-browsers-path", type=Path, default=default_browser_cache())
    check_parser.set_defaults(func=check)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--dry-run", action="store_true")
    cleanup_parser.add_argument("--include-runtime-dirs", action="store_true")
    cleanup_parser.set_defaults(func=cleanup)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
