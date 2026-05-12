from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import store


@dataclass(frozen=True)
class TabcutResponse:
    payload: dict[str, Any]
    status_code: int = 200


def build_videos_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(store.list_video_candidates(args))


def build_goods_response(args: Mapping[str, Any]) -> TabcutResponse:
    return TabcutResponse(store.list_goods(args))


def build_admin_required_response() -> TabcutResponse:
    return TabcutResponse({"error": "admin required"}, 403)


def _default_refresh_runner(*, biz_date: str | None, target_date: str | None, days: int = 7) -> dict[str, Any]:
    return {
        "ok": False,
        "message": "refresh runner is not configured in this process",
        "biz_date": biz_date,
        "target_date": target_date,
        "days": days,
    }


def build_tabcut_refresh_response(
    payload: Mapping[str, Any] | None,
    *,
    runner_fn: Callable[..., dict[str, Any]] = _default_refresh_runner,
) -> TabcutResponse:
    payload = payload or {}
    biz_date = str(payload.get("biz_date") or "").strip() or None
    target_date = str(payload.get("target_date") or "").strip() or None
    try:
        days = int(payload.get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    result = runner_fn(biz_date=biz_date, target_date=target_date, days=max(1, min(days, 30)))
    return TabcutResponse({"ok": bool(result.get("ok")), "result": result}, 202)
