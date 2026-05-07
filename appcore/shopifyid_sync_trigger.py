"""Manual trigger helper for the Shopify ID sync systemd service."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from appcore import scheduled_tasks

TASK_CODE = "shopifyid"
DEFAULT_SERVICE_UNIT = "autovideosrt-shopifyid-sync.service"


def latest_run() -> dict[str, Any] | None:
    return scheduled_tasks.latest_run(TASK_CODE)


def _is_running(latest: dict[str, Any] | None) -> bool:
    return bool(latest and latest.get("status") == "running")


def trigger() -> dict[str, Any]:
    latest = latest_run()
    if _is_running(latest):
        return {
            "already_running": True,
            "message": "Shopify ID 同步任务正在运行中，请稍后刷新状态。",
            "latest": latest,
        }

    if shutil.which("systemctl") is None:
        raise RuntimeError("当前环境没有 systemctl，只能在服务器上手动触发 Shopify ID 同步。")

    service_unit = os.environ.get("SHOPIFYID_SYNC_SERVICE", DEFAULT_SERVICE_UNIT).strip() or DEFAULT_SERVICE_UNIT
    completed = subprocess.run(
        ["systemctl", "start", "--no-block", service_unit],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or "未知错误"
        raise RuntimeError(f"systemctl start {service_unit} 失败：{detail}")

    return {
        "already_running": False,
        "message": "已触发 Shopify ID 同步，请稍后刷新查看结果。",
        "latest": latest_run(),
    }
