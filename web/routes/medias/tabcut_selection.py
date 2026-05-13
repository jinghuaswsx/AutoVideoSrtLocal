from __future__ import annotations

from pathlib import Path

from flask import jsonify, request
from flask_login import login_required

from appcore.tabcut_selection import service
from tools.tabcut_crawler.runner import collect_recent7
from web.background import start_background_task

from . import bp


def _routes_module():
    from web.routes import medias as routes

    return routes


def _json_response(response: service.TabcutResponse):
    return jsonify(response.payload), response.status_code


@bp.route("/api/tabcut-selection/videos", methods=["GET"])
@login_required
def api_tabcut_selection_videos():
    if not _routes_module()._is_admin():
        return _json_response(service.build_admin_required_response())
    return _json_response(service.build_videos_response(request.args))


@bp.route("/api/tabcut-selection/goods", methods=["GET"])
@login_required
def api_tabcut_selection_goods():
    if not _routes_module()._is_admin():
        return _json_response(service.build_admin_required_response())
    return _json_response(service.build_goods_response(request.args))


@bp.route("/api/tabcut-selection/categories", methods=["GET"])
@login_required
def api_tabcut_selection_categories():
    if not _routes_module()._is_admin():
        return _json_response(service.build_admin_required_response())
    return _json_response(service.build_category_options_response(request.args))


@bp.route("/api/tabcut-selection/refresh", methods=["POST"])
@login_required
def api_tabcut_selection_refresh():
    if not _routes_module()._is_admin():
        return _json_response(service.build_admin_required_response())
    body = request.get_json(silent=True) or {}
    return _json_response(service.build_tabcut_refresh_response(body, runner_fn=_start_refresh))


def _start_refresh(*, biz_date: str | None, target_date: str | None, days: int = 30) -> dict:
    start_background_task(
        collect_recent7,
        output_dir=Path("data") / "tabcut" / "manual-refresh",
        days=days,
        persist=True,
    )
    return {
        "ok": True,
        "mode": "background",
        "biz_date": biz_date,
        "target_date": target_date,
        "days": days,
    }
