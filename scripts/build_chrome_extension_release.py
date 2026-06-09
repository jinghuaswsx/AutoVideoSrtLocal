#!/usr/bin/env python3
from __future__ import annotations

"""Build and publish downloadable Chrome extension tool packages.

Docs-anchor:
docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS_DIR = Path("/opt/autovideosrt/web/static/downloads/tools")
DEFAULT_OUTPUT_ROOT = Path.home() / "chrome-extension-builds"
PROD_ENV_FILE = Path("/opt/autovideosrt/.env")
PROD_BASE_URL = "http://127.0.0.1"
RELEASE_STANDARD_PATH = Path(
    "docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md"
)


@dataclass(frozen=True)
class ChromeExtensionToolSpec:
    tool_key: str
    display_name: str
    source_dir: Path
    manifest_path: Path
    version_path: Path
    release_module: str
    zip_prefix: str


TOOL_SPECS = {
    "dianxiaomi_procurement_insights": ChromeExtensionToolSpec(
        tool_key="dianxiaomi_procurement_insights",
        display_name="店小秘采购洞察 Chrome 插件",
        source_dir=Path("tools/dianxiaomi_procurement_insights/chrome_ext"),
        manifest_path=Path("tools/dianxiaomi_procurement_insights/chrome_ext/manifest.json"),
        version_path=Path("tools/dianxiaomi_procurement_insights/version.py"),
        release_module="appcore.dianxiaomi_procurement_insights_release",
        zip_prefix="DianxiaomiProcurementInsights-chrome",
    ),
}


class ReleaseError(RuntimeError):
    pass


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _git_path(value: str) -> Path:
    raw = Path(value)
    if not raw.is_absolute():
        raw = REPO_ROOT / raw
    return raw.resolve()


def require_release_standard_read(read: bool) -> None:
    standard_path = REPO_ROOT / RELEASE_STANDARD_PATH
    if not read:
        raise ReleaseError(f"打包前必须阅读 {RELEASE_STANDARD_PATH}，并传 --release-standard-read")
    if not standard_path.is_file():
        raise ReleaseError(f"发布标准文档不存在：{standard_path}")


def require_master_checkout() -> dict[str, str]:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "master":
        raise ReleaseError(f"Chrome 插件只能从 master 打包，当前分支：{branch}")

    git_dir = _git_path(_run(["git", "rev-parse", "--git-dir"]))
    git_common_dir = _git_path(_run(["git", "rev-parse", "--git-common-dir"]))
    if git_dir != git_common_dir:
        raise ReleaseError(
            f"禁止从 git worktree 打包。git-dir={git_dir} git-common-dir={git_common_dir}"
        )

    status = _run(["git", "status", "--porcelain", "--untracked-files=no"])
    if status:
        raise ReleaseError("tracked 工作区不干净；请先 commit 或清理后再打包")

    _run(["git", "fetch", "origin", "master", "--prune"])
    head = _run(["git", "rev-parse", "HEAD"])
    origin_master = _run(["git", "rev-parse", "origin/master"])
    if head != origin_master:
        raise ReleaseError(f"当前 HEAD 不是最新 origin/master。HEAD={head} origin/master={origin_master}")
    return {
        "branch": branch,
        "head": head,
        "origin_master": origin_master,
    }


def read_version_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'RELEASE_VERSION\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        raise ReleaseError(f"缺少 RELEASE_VERSION：{path}")
    return match.group(1).strip().lstrip("vV")


def read_manifest_version(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = str(payload.get("version") or "").strip().lstrip("vV")
    if not version:
        raise ReleaseError(f"manifest 缺少 version：{path}")
    return version


def validate_tool_version(spec: ChromeExtensionToolSpec, version: str) -> None:
    expected = version.strip().lstrip("vV")
    version_file = read_version_file(REPO_ROOT / spec.version_path)
    manifest_version = read_manifest_version(REPO_ROOT / spec.manifest_path)
    if version_file != expected:
        raise ReleaseError(f"{spec.version_path} 版本 {version_file} 不等于 --version {expected}")
    if manifest_version != expected:
        raise ReleaseError(f"{spec.manifest_path} 版本 {manifest_version} 不等于 --version {expected}")


def _should_skip(path: Path) -> bool:
    if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in path.parts):
        return True
    if path.name in {".DS_Store"}:
        return True
    return path.suffix in {".pyc", ".pyo"}


def build_archive(
    *,
    source_dir: Path,
    archive_path: Path,
    root_dir_name: str,
    release_manifest: dict[str, str],
) -> Path:
    if not source_dir.is_dir():
        raise ReleaseError(f"插件源码目录不存在：{source_dir}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        raise ReleaseError(f"目标 zip 已存在：{archive_path}")

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or _should_skip(path):
                continue
            relative = path.relative_to(source_dir).as_posix()
            archive.write(path, f"{root_dir_name}/{relative}")
        archive.writestr(
            f"{root_dir_name}/release_manifest.json",
            json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n",
        )
    return archive_path


def validate_archive(archive_path: Path, root_dir_name: str, version: str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        required = {
            f"{root_dir_name}/manifest.json",
            f"{root_dir_name}/background.js",
            f"{root_dir_name}/content.js",
            f"{root_dir_name}/release_manifest.json",
        }
        missing = required - names
        if missing:
            raise ReleaseError(f"zip 缺少文件：{', '.join(sorted(missing))}")
        manifest = json.loads(archive.read(f"{root_dir_name}/manifest.json").decode("utf-8"))
        if int(manifest.get("manifest_version") or 0) != 3:
            raise ReleaseError("manifest_version 必须是 3")
        if str(manifest.get("version") or "").strip().lstrip("vV") != version:
            raise ReleaseError("zip 内 manifest version 与发布版本不一致")
        release_manifest = json.loads(
            archive.read(f"{root_dir_name}/release_manifest.json").decode("utf-8")
        )
        for field in ("tool", "version", "source_commit", "origin_master_commit", "release_standard"):
            if not str(release_manifest.get(field) or "").strip():
                raise ReleaseError(f"release_manifest.json 缺少字段：{field}")


def load_prod_env(path: Path = PROD_ENV_FILE) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def write_release_info(
    *,
    release_module: str,
    version: str,
    released_at: str,
    download_url: str,
    release_note: str,
    filename: str,
) -> dict[str, str]:
    load_prod_env()
    sys.path.insert(0, str(REPO_ROOT))
    module = __import__(release_module, fromlist=["set_release_info"])
    return module.set_release_info(
        version=version,
        released_at=released_at,
        download_url=download_url,
        release_note=release_note,
        filename=filename,
    )


def copy_to_downloads(archive_path: Path, downloads_dir: Path, filename: str) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / filename
    if target.exists():
        raise ReleaseError(f"线上 zip 已存在：{target}（不要覆盖旧版本，请升版本）")
    shutil.copy2(archive_path, target)
    target.chmod(0o644)
    return target


def curl_range_check(url: str) -> str:
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--range", "0-99", url],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    code = result.stdout.strip() or "000"
    if code not in {"200", "206"}:
        raise ReleaseError(f"静态下载链接不可达：HTTP {code} {url}")
    return code


def build_and_publish(args: argparse.Namespace) -> dict[str, str]:
    spec = TOOL_SPECS.get(args.tool)
    if not spec:
        raise ReleaseError(f"未知插件工具：{args.tool}")
    version = args.version.strip().lstrip("vV")
    if not version:
        raise ReleaseError("--version 必须传")
    if re.search(r'[\\/:\*\?"<>\|]', version):
        raise ReleaseError(f"版本号含非法文件名字符：{version}")

    require_release_standard_read(args.release_standard_read)
    git_info = require_master_checkout()
    validate_tool_version(spec, version)

    filename = f"{spec.zip_prefix}-{version}.zip"
    root_dir_name = f"{spec.zip_prefix}-{version}"
    output_root = Path(args.output_root or DEFAULT_OUTPUT_ROOT)
    downloads_dir = Path(args.downloads_dir or DEFAULT_DOWNLOADS_DIR)
    archive_path = output_root / filename
    released_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m%d-%H%M%S")
    download_url = f"/static/downloads/tools/{filename}"
    release_manifest = {
        "tool": spec.tool_key,
        "display_name": spec.display_name,
        "version": version,
        "source_commit": git_info["head"],
        "origin_master_commit": git_info["origin_master"],
        "release_standard": str(RELEASE_STANDARD_PATH),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    archive = build_archive(
        source_dir=REPO_ROOT / spec.source_dir,
        archive_path=archive_path,
        root_dir_name=root_dir_name,
        release_manifest=release_manifest,
    )
    validate_archive(archive, root_dir_name, version)
    target = copy_to_downloads(archive, downloads_dir, filename)
    db_payload = write_release_info(
        release_module=spec.release_module,
        version=version,
        released_at=released_at,
        download_url=download_url,
        release_note=args.release_note or "",
        filename=filename,
    )
    http_code = curl_range_check(f"{args.base_url.rstrip('/')}{download_url}")
    return {
        "tool": spec.tool_key,
        "version": version,
        "archive": str(archive),
        "target": str(target),
        "download_url": download_url,
        "released_at": released_at,
        "http_code": http_code,
        "db_payload": json.dumps(db_payload, ensure_ascii=False, sort_keys=True),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and publish Chrome extension tool release")
    parser.add_argument("--release-standard-read", action="store_true")
    parser.add_argument("--tool", required=True, choices=sorted(TOOL_SPECS))
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-note", default="")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--downloads-dir", default=str(DEFAULT_DOWNLOADS_DIR))
    parser.add_argument("--base-url", default=PROD_BASE_URL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        result = build_and_publish(parse_args(argv or sys.argv[1:]))
    except ReleaseError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
