from __future__ import annotations

import argparse
import json
import os
import re
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
RELEASE_MANIFEST_FILENAME = "release_manifest.json"
RELEASE_STANDARD_RELATIVE_PATH = Path("docs/shopify-image-localizer-exe-release-standard.md")
DEFAULT_OUTPUT_ROOT_WINDOWS = Path(r"G:\ShopifyRelease")
DEFAULT_OUTPUT_ROOT_POSIX = Path.home() / "shopify-builds"
BUILD_WORK_DIR_NAME = "_build"
REQUIRED_RUNTIME_CONFIG_FIELDS = ("api_key", "browser_user_data_dir")
FORBIDDEN_RUNTIME_CONFIG_VALUES = {
    "api_key": {"demo-key", "changeme", "change-me", "your-api-key"},
}


def _read_runtime_config_file(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"runtime config missing: {path}") from exc
    except Exception as exc:
        raise ValueError(f"runtime config is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"runtime config must be a JSON object: {path}")
    return payload


def _write_runtime_config_file(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_output_root() -> Path:
    return DEFAULT_OUTPUT_ROOT_WINDOWS if os.name == "nt" else DEFAULT_OUTPUT_ROOT_POSIX


def _resolve_build_python(repo_root: Path) -> Path:
    return Path(sys.executable)


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except FileNotFoundError:
        fallback = _git_output_from_verified_env(*args)
        if fallback is None:
            raise
        return fallback


def _git_output_from_verified_env(*args: str) -> str | None:
    env_keys = {
        ("rev-parse", "--show-toplevel"): "SHOPIFY_LOCALIZER_GIT_TOP",
        ("rev-parse", "--abbrev-ref", "HEAD"): "SHOPIFY_LOCALIZER_GIT_BRANCH",
        ("rev-parse", "--git-dir"): "SHOPIFY_LOCALIZER_GIT_DIR",
        ("rev-parse", "--git-common-dir"): "SHOPIFY_LOCALIZER_GIT_COMMON_DIR",
        ("status", "--porcelain", "--untracked-files=no"): "SHOPIFY_LOCALIZER_GIT_STATUS",
        ("rev-parse", "HEAD"): "SHOPIFY_LOCALIZER_GIT_HEAD",
        ("rev-parse", "origin/master"): "SHOPIFY_LOCALIZER_GIT_ORIGIN_MASTER",
    }
    key = env_keys.get(tuple(args))
    if key is None:
        return None
    value = os.environ.get(key)
    if value is None:
        return None
    return value.strip()


def _resolve_git_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _validate_release_preflight(
    repo_root: Path,
    *,
    release_version: str,
    release_standard_read: bool,
) -> None:
    standard_path = repo_root / RELEASE_STANDARD_RELATIVE_PATH
    if not release_standard_read:
        raise RuntimeError(
            "Shopify Image Localizer release standard was not acknowledged. "
            f"Read {RELEASE_STANDARD_RELATIVE_PATH} and pass --release-standard-read."
        )
    if not standard_path.is_file():
        raise RuntimeError(f"release standard document is missing: {standard_path}")

    repo_root = repo_root.resolve()
    git_top = Path(_git_output(repo_root, "rev-parse", "--show-toplevel")).resolve()
    if git_top != repo_root:
        raise RuntimeError(f"build must run from repository root {git_top}, got {repo_root}")

    branch = _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "master":
        raise RuntimeError(f"Shopify Image Localizer EXE must be packaged from master, got {branch!r}.")

    git_dir = _resolve_git_path(repo_root, _git_output(repo_root, "rev-parse", "--git-dir"))
    git_common_dir = _resolve_git_path(repo_root, _git_output(repo_root, "rev-parse", "--git-common-dir"))
    if git_dir != git_common_dir:
        raise RuntimeError(
            "Shopify Image Localizer EXE must not be packaged from a git worktree. "
            f"git-dir={git_dir}; git-common-dir={git_common_dir}."
        )

    status = _git_output(repo_root, "status", "--porcelain", "--untracked-files=no")
    if status:
        raise RuntimeError(
            "Shopify Image Localizer EXE must be packaged from a clean tracked working tree. "
            "Commit or discard tracked changes first."
        )

    head = _git_output(repo_root, "rev-parse", "HEAD")
    origin_master = _git_output(repo_root, "rev-parse", "origin/master")
    if head != origin_master:
        raise RuntimeError(
            "Shopify Image Localizer EXE must be packaged from the latest origin/master. "
            f"HEAD={head}; origin/master={origin_master}."
        )

    normalized_release_version = _normalize_release_version(release_version)
    if _normalize_release_version(version.RELEASE_VERSION) != normalized_release_version:
        raise RuntimeError(
            "tools/shopify_image_localizer/version.py must match --version before packaging. "
            f"version.py={version.RELEASE_VERSION!r}; --version={normalized_release_version!r}."
        )


def _validate_runtime_config_file(path: Path) -> None:
    payload = _read_runtime_config_file(path)
    missing = [
        field
        for field in REQUIRED_RUNTIME_CONFIG_FIELDS
        if not str(payload.get(field) or "").strip()
    ]
    if missing:
        hint = (
            "Set SHOPIFY_IMAGE_LOCALIZER_API_KEY before packaging, or provide a valid "
            f"{settings.CONFIG_FILENAME} in the repository root."
        )
        raise ValueError(f"{path.name} missing required field(s): {', '.join(missing)}. {hint}")
    forbidden = [
        f"{field}={str(payload.get(field) or '').strip()!r}"
        for field, values in FORBIDDEN_RUNTIME_CONFIG_VALUES.items()
        if str(payload.get(field) or "").strip().lower() in values
    ]
    if forbidden:
        raise ValueError(
            f"{path.name} contains forbidden placeholder value(s): {', '.join(forbidden)}; "
            "fetch the live openapi_materials api_key from the server before packaging."
        )


def _write_runtime_config(repo_root: Path, dist_root: Path) -> None:
    source_config = settings.config_path(repo_root)
    target_config = settings.config_path(dist_root)
    default_config = settings.default_config_path(dist_root)
    if source_config.is_file():
        payload = _read_runtime_config_file(source_config)
        if settings.DEFAULT_API_KEY:
            payload["api_key"] = settings.DEFAULT_API_KEY
        payload.setdefault("base_url", settings.default_base_url(packaged=True))
        payload.setdefault("browser_user_data_dir", settings.DEFAULT_BROWSER_USER_DATA_DIR)
        payload.setdefault("shopify_domain", settings.DEFAULT_SHOPIFY_DOMAIN)
        if not isinstance(payload.get("shopify_domain_store_slugs"), dict):
            payload["shopify_domain_store_slugs"] = {}
        _write_runtime_config_file(target_config, payload)
    else:
        if not settings.DEFAULT_API_KEY:
            raise ValueError(
                "SHOPIFY_IMAGE_LOCALIZER_API_KEY must be set before packaging Shopify Image Localizer."
            )
        settings.save_runtime_config(
            base_url=settings.default_base_url(packaged=True),
            api_key=settings.DEFAULT_API_KEY,
            browser_user_data_dir=settings.DEFAULT_BROWSER_USER_DATA_DIR,
            root=dist_root,
        )

    _validate_runtime_config_file(target_config)
    shutil.copy2(target_config, default_config)
    _validate_runtime_config_file(default_config)


def _write_portable_launcher(dist_root: Path, release_version: str) -> Path:
    exe_basename = f"{_exe_name_for_version(release_version)}.exe"
    launcher_path = dist_root / PORTABLE_LAUNCHER_NAME
    launcher_path.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal",
                "cd /d %~dp0",
                f"if not exist \"%~dp0{exe_basename}\" (",
                f"    echo [ShopifyImageLocalizer] missing {exe_basename}",
                "    pause",
                "    exit /b 1",
                ")",
                f"\"%~dp0{exe_basename}\"",
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


def _exe_name_for_version(release_version: str) -> str:
    """exe 文件名带版本号，规则同 spec：把 . / 等替换成 _，得到 ShopifyImageLocalizer_3_3.exe。"""
    normalized = _normalize_release_version(release_version)
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_")
    return f"{APP_NAME}_{suffix}" if suffix else APP_NAME


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


def _write_release_manifest(dist_root: Path, repo_root: Path, release_version: str) -> Path:
    manifest_path = dist_root / RELEASE_MANIFEST_FILENAME
    payload = {
        "app": APP_NAME,
        "release_version": _normalize_release_version(release_version),
        "code_release_version": version.RELEASE_VERSION,
        "source_branch": _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "source_commit": _git_output(repo_root, "rev-parse", "HEAD"),
        "origin_master_commit": _git_output(repo_root, "rev-parse", "origin/master"),
        "release_standard": str(RELEASE_STANDARD_RELATIVE_PATH).replace("\\", "/"),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


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
    default_output_root = _default_output_root()
    parser.add_argument(
        "--output-root",
        default=str(default_output_root),
        help=f"release/build output root, default: {default_output_root}",
    )
    parser.add_argument(
        "--release-standard-read",
        action="store_true",
        help=(
            "confirm docs/shopify-image-localizer-exe-release-standard.md was read; "
            "required for packaging"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    release_version = _normalize_release_version(args.version)
    repo_root = Path(__file__).resolve().parents[2]
    _validate_release_preflight(
        repo_root,
        release_version=release_version,
        release_standard_read=args.release_standard_read,
    )
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
    # spec 读这个 env var 决定 EXE.name，从而生成 ShopifyImageLocalizer_<major>_<minor>.exe
    env["SHOPIFY_LOCALIZER_RELEASE_VERSION"] = _normalize_release_version(release_version)
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
    _write_portable_launcher(release_root, release_version)
    _write_release_version(release_root, release_version)
    _write_release_manifest(release_root, repo_root, release_version)
    _build_portable_zip(release_root, archive_path)
    shutil.rmtree(build_dist_root)


if __name__ == "__main__":
    main()
