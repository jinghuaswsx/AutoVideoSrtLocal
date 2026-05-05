"""明空（wedev）选品代理路由。

由 ``web.routes.medias`` package 在 PR 2.15 抽出；行为不变。
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

import requests
from flask import abort, jsonify, request
from flask_login import login_required

from appcore import local_media_storage, pushes

from . import bp
from ._helpers import _MAX_MK_VIDEO_BYTES, _MK_VIDEO_CACHE_PREFIX, _dianxiaomi_rankings_columns
from web.services.media_mk_selection import (
    build_mk_video_proxy_flask_response as _build_mk_video_proxy_flask_response,
    build_mk_video_proxy_response as _build_mk_video_proxy_response_impl,
    cache_mk_video as _cache_mk_video_impl,
    build_mk_detail_response as _build_mk_detail_response_impl,
    build_mk_media_proxy_flask_response as _build_mk_media_proxy_flask_response,
    build_mk_media_proxy_response as _build_mk_media_proxy_response_impl,
    build_mk_selection_response as _build_mk_selection_response_impl,
)


_MK_TOKEN_FILE = Path("C:/店小秘/mk_token.txt")
def _routes():
    from web.routes import medias as routes
    return routes


def _is_admin():
    return _routes()._is_admin()


def db_query(*args, **kwargs):
    return _routes().db_query(*args, **kwargs)


def _build_mk_selection_response(args):
    return _build_mk_selection_response_impl(
        args,
        ranking_columns_fn=_dianxiaomi_rankings_columns,
        db_query_fn=db_query,
    )


def _build_mk_detail_response(mk_id: int):
    return _build_mk_detail_response_impl(
        mk_id,
        build_headers_fn=_build_mk_request_headers,
        get_base_url_fn=_get_mk_api_base_url,
        is_login_expired_fn=_is_mk_login_expired,
        http_get_fn=requests.get,
    )


def _build_mk_media_proxy_response(media_path: str):
    return _build_mk_media_proxy_response_impl(
        media_path,
        build_headers_fn=_build_mk_request_headers,
        get_base_url_fn=_get_mk_api_base_url,
        http_get_fn=requests.get,
    )


def _build_mk_video_proxy_response(media_path: str, guessed_type: str):
    return _build_mk_video_proxy_response_impl(
        media_path,
        guessed_type,
        cache_video_fn=_cache_mk_video,
        safe_local_path_for_fn=local_media_storage.safe_local_path_for,
    )


@bp.route("/api/mk-selection", methods=["GET"])
@login_required
def api_mk_selection():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    """返回店小秘 Top300 + 明空消耗数据，按 90 天消耗降序。"""
    result = _routes()._build_mk_selection_response(request.args)
    return jsonify(result.payload), result.status_code


@bp.route("/api/mk-selection/refresh", methods=["POST"])
@login_required
def api_mk_selection_refresh():
    """触发重新抓取明空消耗数据（后台任务）。"""
    # TODO: 后台任务重新抓取
    return jsonify({"ok": True, "message": "刷新任务已提交（暂未实现）"})


@bp.route("/api/mk-media", methods=["GET"])
@login_required
def api_mk_media_proxy():
    """Proxy wedev media files so the selection detail modal does not hit local object routes."""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    media_path = _normalize_mk_media_path(request.args.get("path") or "")
    if not media_path:
        abort(404)

    result = _routes()._build_mk_media_proxy_response(media_path)
    return _build_mk_media_proxy_flask_response(result)


@bp.route("/api/mk-video", methods=["GET"])
@login_required
def api_mk_video_proxy():
    """Cache a wedev video source locally, then serve it for in-page preview."""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    media_path = _normalize_mk_media_path(request.args.get("path") or "")
    if not media_path:
        abort(404)
    guessed_type = (mimetypes.guess_type(media_path)[0] or "").split(";")[0].strip()
    if guessed_type and not guessed_type.startswith("video/"):
        abort(404)

    result = _routes()._build_mk_video_proxy_response(media_path, guessed_type)
    return _build_mk_video_proxy_flask_response(result)


@bp.route("/api/mk-detail/<int:mk_id>")
@login_required
def api_mk_detail_proxy(mk_id: int):
    """代理请求明空 API 获取产品详情，避免浏览器 CORS 问题。"""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    result = _routes()._build_mk_detail_response(mk_id)
    return jsonify(result.payload), result.status_code


def _get_mk_api_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _normalize_mk_media_path(raw_path: str) -> str:
    path = (raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def _mk_video_cache_object_key(media_path: str) -> str:
    digest = hashlib.sha256(media_path.encode("utf-8")).hexdigest()
    ext = Path(media_path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm"}:
        ext = ".mp4"
    return f"{_MK_VIDEO_CACHE_PREFIX}/{digest}{ext}"


def _cache_mk_video(media_path: str) -> str:
    return _cache_mk_video_impl(
        media_path,
        cache_object_key_fn=_mk_video_cache_object_key,
        storage_exists_fn=local_media_storage.exists,
        build_headers_fn=_build_mk_request_headers,
        get_base_url_fn=_get_mk_api_base_url,
        safe_local_path_for_fn=local_media_storage.safe_local_path_for,
        max_bytes=_MAX_MK_VIDEO_BYTES,
        http_get_fn=requests.get,
    )


def _build_mk_request_headers() -> dict[str, str]:
    """Build server-side wedev headers, preferring synced settings over legacy token."""
    headers = dict(pushes.build_localized_texts_headers())
    headers.pop("Content-Type", None)
    headers["Accept"] = "application/json"
    if "Authorization" not in headers:
        mk_token = _get_mk_token()
        if mk_token:
            headers["Authorization"] = (
                mk_token if mk_token.lower().startswith("bearer ") else f"Bearer {mk_token}"
            )
    return headers


def _is_mk_login_expired(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return data.get("is_guest") is True or str(data.get("message") or "").startswith("登录")


def _get_mk_token() -> str:
    """从浏览器持久化数据或配置获取明空 token。"""
    # 优先从环境变量读取
    token = os.environ.get("MK_API_TOKEN", "").strip()
    if token:
        return token
    if _MK_TOKEN_FILE.is_file():
        return _MK_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""
