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


def _routes():
    from web.routes import medias as routes
    return routes


def _is_admin():
    return _routes()._is_admin()


def db_query(*args, **kwargs):
    return _routes().db_query(*args, **kwargs)


@bp.route("/api/mk-selection", methods=["GET"])
@login_required
def api_mk_selection():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    """返回店小秘 Top300 + 明空消耗数据，按 90 天消耗降序。"""
    snapshot = (request.args.get("snapshot") or "2026-04-23").strip()
    keyword = (request.args.get("keyword") or "").strip()
    page_num = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(10, int(request.args.get("page_size", 50))))
    offset = (page_num - 1) * page_size
    ranking_columns = _dianxiaomi_rankings_columns()
    has_mk_product_id = "mk_product_id" in ranking_columns
    has_mk_product_name = "mk_product_name" in ranking_columns
    has_mk_total_spends = "mk_total_spends" in ranking_columns
    has_mk_video_count = "mk_video_count" in ranking_columns
    has_mk_total_ads = "mk_total_ads" in ranking_columns

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        keyword_clauses = ["dr.product_name LIKE %s"]
        params.append(f"%{keyword}%")
        if has_mk_product_name:
            keyword_clauses.append("dr.mk_product_name LIKE %s")
            params.append(f"%{keyword}%")
        where += " AND (" + " OR ".join(keyword_clauses) + ")"

    mk_product_id_select = "dr.mk_product_id" if has_mk_product_id else "NULL AS mk_product_id"
    mk_product_name_select = "dr.mk_product_name" if has_mk_product_name else "NULL AS mk_product_name"
    mk_total_spends_select = "dr.mk_total_spends" if has_mk_total_spends else "0 AS mk_total_spends"
    mk_video_count_select = "dr.mk_video_count" if has_mk_video_count else "0 AS mk_video_count"
    mk_total_ads_select = "dr.mk_total_ads" if has_mk_total_ads else "0 AS mk_total_ads"
    order_by = "dr.mk_total_spends DESC, dr.rank_position ASC" if has_mk_total_spends else "dr.rank_position ASC"

    count_row = db_query(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = db_query(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main, dr.revenue_split,
            {mk_product_id_select}, {mk_product_name_select},
            {mk_total_spends_select}, {mk_video_count_select}, {mk_total_ads_select},
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    items = []
    for r in rows:
        items.append({
            "rank": r["rank_position"],
            "shopify_id": r["shopify_id"],
            "product_name": r["product_name"],
            "product_url": r["product_url"],
            "store": r["store"],
            "sales_count": r["sales_count"],
            "order_count": r["order_count"],
            "revenue_main": r["revenue_main"],
            "revenue_split": r["revenue_split"],
            "mk_product_id": r["mk_product_id"],
            "mk_product_name": r["mk_product_name"],
            "mk_total_spends": float(r["mk_total_spends"] or 0),
            "mk_video_count": r["mk_video_count"] or 0,
            "mk_total_ads": r["mk_total_ads"] or 0,
            "media_product_id": r["media_product_id"],
            "mp_name": r["mp_name"],
            "mp_code": r["mp_code"],
        })

    return jsonify({"items": items, "total": total, "page": page_num, "page_size": page_size})


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
    # 从文件读取
    token_file = Path("C:/店小秘/mk_token.txt")
    if token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip()
    # 硬编码 fallback（应尽快迁移到配置）
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ6aGlmYSIsImV4cCI6MTc3OTUxOTA5MSwiaWF0IjoxNzc2OTI3MDkxLCJqdGkiOiIzNSJ9.Rq_jgNz-f3WHg586FGQIs4DmFhnMHoIDCggJhBWDacM"
