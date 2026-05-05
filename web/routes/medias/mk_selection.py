"""明空（wedev）选品代理路由。

由 ``web.routes.medias`` package 在 PR 2.15 抽出；行为不变。
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Response, abort, jsonify, request, send_file
from flask_login import login_required

from appcore import local_media_storage, pushes

from . import bp
from ._helpers import _MAX_MK_VIDEO_BYTES, _MK_VIDEO_CACHE_PREFIX, _dianxiaomi_rankings_columns
from web.services.media_mk_selection import (
    build_mk_selection_response as _build_mk_selection_response_impl,
)


_MK_TOKEN_FILE = Path("C:/店小秘/mk_token.txt")
_MK_CREDENTIALS_MISSING_ERROR = "明空凭据未配置，请先在设置页同步 wedev 凭据"


class MkCredentialsMissingError(RuntimeError):
    pass


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

    headers = _build_mk_request_headers()
    headers.pop("Content-Type", None)
    headers["Accept"] = "image/*,*/*;q=0.8"
    if not _has_mk_credentials(headers):
        return _mk_credentials_missing_response()
    url = f"{_get_mk_api_base_url()}/medias/{quote(media_path, safe='/')}"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if resp.status_code >= 400:
        return ("", resp.status_code)
    content_type = (
        (resp.headers.get("content-type") or "").split(";")[0].strip()
        or mimetypes.guess_type(media_path)[0]
        or "application/octet-stream"
    )
    proxied = Response(resp.content, status=resp.status_code, content_type=content_type)
    proxied.headers["Cache-Control"] = "private, max-age=3600"
    return proxied


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

    try:
        object_key = _cache_mk_video(media_path)
    except MkCredentialsMissingError:
        return _mk_credentials_missing_response()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None) or 502
        return ("", status)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    mimetype = mimetypes.guess_type(object_key)[0] or guessed_type or "video/mp4"
    try:
        local_path = local_media_storage.safe_local_path_for(object_key)
    except ValueError:
        abort(404)
    return send_file(
        str(local_path),
        mimetype=mimetype,
        conditional=True,
    )


@bp.route("/api/mk-detail/<int:mk_id>")
@login_required
def api_mk_detail_proxy(mk_id: int):
    """代理请求明空 API 获取产品详情，避免浏览器 CORS 问题。"""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    headers = _build_mk_request_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return jsonify({"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"}), 500
    base_url = _get_mk_api_base_url()
    try:
        resp = requests.get(
            f"{base_url}/api/marketing/medias/{mk_id}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        if _is_mk_login_expired(data):
            return jsonify({"error": "明空登录已失效，请重新同步 wedev 凭据"}), 401
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


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
    object_key = _mk_video_cache_object_key(media_path)
    if local_media_storage.exists(object_key):
        return object_key

    headers = _build_mk_request_headers()
    if not _has_mk_credentials(headers):
        raise MkCredentialsMissingError()
    headers.pop("Content-Type", None)
    headers["Accept"] = "video/*,*/*;q=0.8"
    url = f"{_get_mk_api_base_url()}/medias/{quote(media_path, safe='/')}"
    resp = requests.get(url, headers=headers, timeout=60, stream=True)
    try:
        if resp.status_code >= 400:
            http_error = requests.HTTPError(f"mk video HTTP {resp.status_code}")
            http_error.response = resp
            raise http_error
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("video/"):
            raise ValueError(f"明空返回的不是视频文件: {content_type}")
        declared_size = int(resp.headers.get("content-length") or 0)
        if declared_size > _MAX_MK_VIDEO_BYTES:
            raise ValueError("明空视频过大，超过 2GB")

        destination = local_media_storage.safe_local_path_for(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="mk_video_", dir=str(destination.parent))
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_MK_VIDEO_BYTES:
                        raise ValueError("明空视频过大，超过 2GB")
                    handle.write(chunk)
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    return object_key


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


def _has_mk_credentials(headers: dict[str, str]) -> bool:
    return bool((headers.get("Authorization") or "").strip() or (headers.get("Cookie") or "").strip())


def _mk_credentials_missing_response():
    return jsonify({"error": _MK_CREDENTIALS_MISSING_ERROR}), 500


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
