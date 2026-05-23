from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from appcore import settings as system_settings


SETTING_KEY = "shopify_image_localizer_release"

_RELEASED_AT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_COMPACT_BEIJING_RE = re.compile(r"^\d{4}-\d{6}$")


def _format_released_at_display(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if _COMPACT_BEIJING_RE.fullmatch(value):
        return value

    iso_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_BEIJING_TZ)
        return dt.strftime("%m%d-%H%M%S")
    except ValueError:
        pass

    for fmt in _RELEASED_AT_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%m%d-%H%M%S")
        except ValueError:
            continue
    return value


def get_release_info() -> dict[str, str]:
    raw = system_settings.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    released_at = str(payload.get("released_at") or "").strip()
    return {
        "version": str(payload.get("version") or "").strip(),
        "released_at": released_at,
        "released_at_display": _format_released_at_display(released_at),
        "release_note": str(payload.get("release_note") or "").strip(),
        "download_url": str(payload.get("download_url") or "").strip(),
        "filename": str(payload.get("filename") or "").strip(),
    }


def set_release_info(
    *,
    version: str,
    released_at: str,
    download_url: str,
    release_note: str = "",
    filename: str = "",
) -> dict[str, str]:
    payload: dict[str, Any] = {
        "version": str(version or "").strip(),
        "released_at": str(released_at or "").strip(),
        "release_note": str(release_note or "").strip(),
        "download_url": str(download_url or "").strip(),
        "filename": str(filename or "").strip(),
    }
    if not payload["version"]:
        raise ValueError("version is required")
    if not payload["download_url"]:
        raise ValueError("download_url is required")
    system_settings.set_setting(
        SETTING_KEY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    return {key: str(value or "") for key, value in payload.items()}


def run_scheduled_auto_release() -> dict[str, Any]:
    """Scheduled task that checks master version.py and builds a release under Linux Wine prefix if a new version is detected."""
    import os
    import subprocess
    from pathlib import Path

    # 1. Read the code version from version.py in the repository
    repo_root = Path(__file__).resolve().parent.parent
    version_file = repo_root / "tools" / "shopify_image_localizer" / "version.py"
    if not version_file.is_file():
        return {"ok": False, "reason": f"version.py not found at {version_file}"}

    try:
        content = version_file.read_text(encoding="utf-8")
        match = re.search(r'RELEASE_VERSION\s*=\s*["\']([^"\']+)["\']', content)
        if not match:
            return {"ok": False, "reason": "RELEASE_VERSION not found in version.py"}
        code_version = match.group(1).strip()
    except Exception as exc:
        return {"ok": False, "reason": f"failed to read version.py: {exc}"}

    # 2. Get currently released version from system_settings
    current_release = get_release_info()
    released_version = current_release.get("version")

    if code_version == released_version:
        return {
            "ok": True,
            "checked": True,
            "version": code_version,
            "action": "none",
            "reason": f"version {code_version} is already released",
        }

    # 3. Only run automated Wine build if we are on a Linux/POSIX production server
    if os.name == "nt":
        return {
            "ok": True,
            "checked": True,
            "version": code_version,
            "action": "skipped",
            "reason": f"Windows dev machine skipped remote Wine build (code version {code_version} vs released {released_version})",
        }

    # Check if we are running in the standard server environment /opt/autovideosrt
    build_script = repo_root / "scripts" / "build_shopify_image_localizer_wine.sh"
    if not build_script.is_file():
        return {"ok": False, "reason": f"build script not found at {build_script}"}

    # 4. Trigger build_shopify_image_localizer_wine.sh as the 'cjh' user to avoid Wine prefix ownership issues
    release_note = f"自动定时发布：版本 {code_version}"
    cmd = [
        "sudo", "-i", "-u", "cjh", "bash", "-c",
        f"set -e && cd /opt/autovideosrt && bash scripts/build_shopify_image_localizer_wine.sh --release-standard-read --version {code_version} --release-note '{release_note}'"
    ]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout or completed.stderr or "").strip()
            return {
                "ok": False,
                "reason": f"Wine build script failed with exit code {completed.returncode}: {output}",
            }

        return {
            "ok": True,
            "checked": True,
            "version": code_version,
            "action": "released",
            "reason": f"Successfully auto-released version {code_version}",
            "stdout": completed.stdout,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"Failed to execute Wine build script: {exc}"}


def run_scheduled_auto_release_wrapper() -> None:
    """Wrapper that logs the task execution status to scheduled_task_runs in the database."""
    from appcore import scheduled_tasks
    run_id = scheduled_tasks.start_run("shopify_image_localizer_auto_release")
    try:
        res = run_scheduled_auto_release()
        if res.get("ok"):
            scheduled_tasks.finish_run(run_id, status="success", summary_json=res)
        else:
            scheduled_tasks.record_failure(run_id, RuntimeError(res.get("reason", "unknown error")))
    except Exception as exc:
        scheduled_tasks.record_failure(run_id, exc)


def register(scheduler) -> None:
    """向调度器注册 Shopify Image Localizer 自动定时发布任务。"""
    from appcore import scheduled_tasks
    scheduled_tasks.add_controlled_job(
        scheduler,
        "shopify_image_localizer_auto_release",
        run_scheduled_auto_release_wrapper,
        "cron",
        minute="*/30",
        id="shopify_image_localizer_auto_release",
        replace_existing=True,
        max_instances=1,
    )

