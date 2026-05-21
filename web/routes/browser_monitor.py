from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from flask import Blueprint, render_template
from flask_login import login_required

from appcore import scheduled_tasks
from web.auth import permission_required

bp = Blueprint("browser_monitor", __name__, url_prefix="/browser-monitor")

SERVER_HOST = "172.16.254.106"


@dataclass(frozen=True)
class BrowserEnvironment:
    code: str
    label: str
    port: int
    purpose: str

    def _vnc_url(self, *, resize: str, view_only: bool = False) -> str:
        if self.port == 6094:  # 采集程序的特殊 URL 格式
            params: dict[str, str | int] = {
                "host": SERVER_HOST,
                "port": self.port,
                "path": "websockify",
                "scale": "true",
                "autoconnect": "true",
            }
            return f"http://{SERVER_HOST}:{self.port}/vnc_lite.html?{urlencode(params)}"

        # 其他环境的默认 URL 格式
        params: dict[str, str | int] = {
            "host": SERVER_HOST,
            "port": self.port,
            "autoconnect": "true",
            "resize": resize,
        }
        if view_only:
            params["view_only"] = "true"
        return f"http://{SERVER_HOST}:{self.port}/vnc.html?{urlencode(params)}"

    @property
    def novnc_url(self) -> str:
        return self._vnc_url(resize="remote")

    @property
    def preview_url(self) -> str:
        return self._vnc_url(resize="scale", view_only=True)


ENVIRONMENTS: tuple[BrowserEnvironment, ...] = (
    BrowserEnvironment("DXM01-Meta", "DXM01-Meta", 6092, "Meta Ads Manager 导出"),
    BrowserEnvironment("DXM02-MK", "DXM02-MK", 6093, "明空选品店小秘"),
    BrowserEnvironment("DXM03-RJC", "DXM03-RJC", 6095, "荣锦成店小秘订单 / SKU / Shopify ID"),
    BrowserEnvironment("TABCUT", "TABCUT", 6097, "Tabcut 选品采集"),
    BrowserEnvironment("采集程序", "采集程序", 6094, "采集程序 VNC 窗口"),
)


def _issue_text(issues: list[dict[str, Any]]) -> str:
    parts = []
    for issue in issues:
        kind = str(issue.get("kind") or "issue")
        message = str(issue.get("message") or "").strip()
        parts.append(f"{kind}: {message}" if message else kind)
    return "；".join(parts)


def _status_by_env(latest_run: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    statuses = {
        env.code: {"label": "未知", "class": "unknown", "detail": "暂无 watchdog 摘要"}
        for env in ENVIRONMENTS
    }
    summary = (latest_run or {}).get("summary") or {}
    for item in summary.get("environments") or []:
        final = item.get("final") or item.get("initial") or {}
        code = str(final.get("code") or "")
        if code not in statuses:
            continue
        issues = final.get("issues") or []
        if final.get("ok"):
            statuses[code] = {
                "label": "正常",
                "class": "ok",
                "detail": "systemd / CDP / noVNC 可访问",
            }
        else:
            statuses[code] = {
                "label": "异常",
                "class": "bad",
                "detail": _issue_text(issues) or "watchdog 报告异常",
            }
    return statuses


@bp.route("")
@login_required
@permission_required("lab")
def page():
    try:
        latest_run = scheduled_tasks.latest_run("cdp_environment_watchdog")
    except Exception:
        latest_run = None
    return render_template(
        "browser_monitor.html",
        environments=ENVIRONMENTS,
        status_by_env=_status_by_env(latest_run),
        latest_run=latest_run,
    )
