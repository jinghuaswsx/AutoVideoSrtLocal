from __future__ import annotations

import io
import json
import hashlib
import logging
import mimetypes
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
import uuid
import requests
from flask import Blueprint, Response, request, jsonify, abort, send_file, url_for
from flask_login import login_required, current_user

from appcore import (
    local_media_storage,
    material_evaluation,
    medias,
    object_keys,
    product_roas,
    pushes,
    shopify_image_localizer_release,
    shopify_image_tasks,
    task_state,
)
from appcore import image_translate_runtime
from appcore import image_translate_settings as its
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.material_filename_rules import (
    validate_initial_material_filename,
    validate_material_filename,
    validate_video_filename_no_spaces,
)
from config import OUTPUT_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration
from web import store
from web.background import start_background_task
from web.routes import image_translate as image_translate_routes
from web.services import image_translate_runner, link_check_runner
from ._helpers import (
    _DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES,
    _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES,
    _DETAIL_IMAGE_KIND_LABELS,
    _DETAIL_IMAGE_LIMITS,
    _check_filename_prefix,
    _client_filename_basename,
    _default_image_translate_model_id,
    _detail_image_empty_counts,
    _detail_image_existing_counts,
    _detail_image_kind_from_download_ext,
    _detail_image_limit_error,
    _detail_images_archive_basename,
    _detail_images_archive_part,
    _detail_images_archive_product_code,
    _detail_images_is_gif,
    _dianxiaomi_rankings_columns,
    _download_image_to_local_media,
    _ensure_product_listed,
    _language_name_map,
    _list_raw_source_allowed_english_filenames,
    _material_evaluation_message,
    _parse_lang,
    _raw_source_filename_error_response,
    _resolve_upload_user_id,
    _start_image_translate_runner,
    _suggest_raw_source_title,
    _validate_material_filename_for_product,
    _validate_product_code,
    _validate_raw_source_display_name,
    probe_media_info_safe,
)
from ._serializers import (
    _int_or_none,
    _json_number_or_none,
    _serialize_detail_image,
    _serialize_item,
    _serialize_link_check_task,
    _serialize_product,
    _serialize_raw_source,
)

log = logging.getLogger(__name__)

_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_MK_VIDEO_CACHE_PREFIX = "mk-selection/videos"


bp = Blueprint("medias", __name__, url_prefix="/medias")

@bp.route("/api/local-media-upload/<upload_id>", methods=["PUT"])
@login_required
def api_local_media_upload(upload_id: str):
    with _local_upload_guard:
        reservation = _local_upload_reservations.get(upload_id)
    if not reservation or int(reservation.get("user_id") or 0) != int(current_user.id):
        abort(404)
    local_media_storage.write_stream(reservation["object_key"], request.stream)
    return ("", 204)


@bp.route("/object", methods=["GET"])
@login_required
def media_object_proxy():
    object_key = (request.args.get("object_key") or "").strip()
    if not object_key:
        abort(404)
    return _send_media_object(object_key)

# ---------- 椤甸潰 ----------

# ---------- 缈昏瘧浠诲姟 ----------



# ---------- 浜у搧 API ----------






# ======================================================================
# 鍟嗗搧璇︽儏鍥撅紙product detail images锛?
# ----------------------------------------------------------------------
# 绗竴杞彧鍦ㄨ嫳璇绉嶆毚闇插叆鍙ｏ紝鍏朵粬璇鐨勭増鏈皢鐢卞悗缁浘鐗囩炕璇戦泦鎴愯嚜鍔ㄧ敓鎴愩€?
# ======================================================================

# ----------------------------------------------------------------
# 无鉴权素材下载（仅用于推送给外部下游系统作为 video.url / image_url）。
# 安全模型：知 object_key 者即可访问。
# 下游 Dify / Shopify 工作流在内网，主项目也在内网，不暴露到公网。
# ----------------------------------------------------------------
@bp.route("/obj/<path:object_key>")
def public_media_object(object_key: str):
    key = (object_key or "").strip()
    # 最低限度的防护：禁止 path traversal 和空值
    if not key or ".." in key.split("/") or key.startswith("/"):
        abort(404)
    # 项目内合法 object_key 命名空间（local_media_storage 已做 traversal 校验）：
    #   <uid>/medias/<pid>/<filename>              -- 原始素材 / 封面 / raw_sources
    #   artifacts/<variant>/<uid>/<tid>/<file>    -- 产物（image_translate 译图/译封面等）
    #   uploads/<variant>/<uid>/<tid>/<file>      -- 上传源文件
    parts = key.split("/")
    if len(parts) < 3:
        abort(404)
    if not (parts[1] == "medias" or parts[0] in ("artifacts", "uploads")):
        abort(404)
    return _send_media_object(key)


# ---------- 明空选品 ----------

def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


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
    return send_file(
        str(local_media_storage.local_path_for(object_key)),
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

        destination = local_media_storage.local_path_for(object_key)
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


from . import pages as _pages
from . import products as _products
_appcore_pushes = pushes
import importlib as _importlib
_push_routes = _importlib.import_module(f"{__name__}.pushes")
pushes = _appcore_pushes
from . import shopify_image as _shopify_image_routes
from . import link_check as _link_check_routes
from . import evaluation as _evaluation
from . import raw_sources as _raw_sources
from . import translate as _translate
from . import items as _items
from . import covers as _covers
from . import detail_images as _detail_images

from ._helpers import _can_access_product, _material_evaluation_message, _schedule_material_evaluation
from ._helpers import _delete_media_object, _MAX_IMAGE_BYTES, _MAX_RAW_VIDEO_BYTES, _ALLOWED_IMAGE_TYPES, _ALLOWED_RAW_VIDEO_TYPES
from ._helpers import (
    THUMB_DIR,
    _download_media_object,
    _is_media_available,
    _reserve_local_media_upload,
    _send_media_object,
    _local_upload_guard,
    _local_upload_reservations,
)
api_detail_images_list = _detail_images.api_detail_images_list
api_detail_images_download_zip = _detail_images.api_detail_images_download_zip
api_detail_images_download_localized_zip = _detail_images.api_detail_images_download_localized_zip
api_detail_images_from_url = _detail_images.api_detail_images_from_url
api_detail_images_from_url_status = _detail_images.api_detail_images_from_url_status
api_detail_images_bootstrap = _detail_images.api_detail_images_bootstrap
api_detail_images_complete = _detail_images.api_detail_images_complete
api_detail_images_delete = _detail_images.api_detail_images_delete
api_detail_images_clear_all = _detail_images.api_detail_images_clear_all
api_detail_images_reorder = _detail_images.api_detail_images_reorder
api_detail_images_translate_from_en = _detail_images.api_detail_images_translate_from_en
api_detail_image_translate_tasks = _detail_images.api_detail_image_translate_tasks
api_detail_images_apply_translate_task = _detail_images.api_detail_images_apply_translate_task
detail_image_proxy = _detail_images.detail_image_proxy
api_cover_from_url = _covers.api_cover_from_url
api_item_cover_from_url = _covers.api_item_cover_from_url
api_item_cover_set_from_url = _covers.api_item_cover_set_from_url
api_item_cover_update = _covers.api_item_cover_update
api_item_cover_bootstrap = _covers.api_item_cover_bootstrap
api_item_cover_set = _covers.api_item_cover_set
item_cover = _covers.item_cover
raw_source_video_url = _covers.raw_source_video_url
raw_source_cover_url = _covers.raw_source_cover_url
api_cover_bootstrap = _covers.api_cover_bootstrap
api_cover_complete = _covers.api_cover_complete
api_cover_delete = _covers.api_cover_delete
thumb = _covers.thumb
cover = _covers.cover
api_play_url = _covers.api_play_url
api_item_bootstrap = _items.api_item_bootstrap
api_item_complete = _items.api_item_complete
api_update_item = _items.api_update_item
api_delete_item = _items.api_delete_item
api_product_translate = _translate.api_product_translate
api_product_translation_tasks = _translate.api_product_translation_tasks
api_list_raw_sources = _raw_sources.api_list_raw_sources
api_create_raw_source = _raw_sources.api_create_raw_source
api_update_raw_source = _raw_sources.api_update_raw_source
api_delete_raw_source = _raw_sources.api_delete_raw_source
api_product_evaluate = _evaluation.api_product_evaluate
api_product_evaluate_request_preview = _evaluation.api_product_evaluate_request_preview
api_product_evaluate_request_payload = _evaluation.api_product_evaluate_request_payload

_medias_page_context = _pages._medias_page_context
index = _pages.index
product_detail_page = _pages.product_detail_page
translation_tasks_page = _pages.translation_tasks_page
api_list_active_users = _pages.api_list_active_users
api_list_languages = _pages.api_list_languages
mk_selection_page = _pages.mk_selection_page

_ROAS_PRODUCT_FIELDS = _products._ROAS_PRODUCT_FIELDS
_normalize_mk_copywriting_query = _products._normalize_mk_copywriting_query
_mk_product_link_tail = _products._mk_product_link_tail
_format_mk_copywriting_text = _products._format_mk_copywriting_text
_extract_mk_copywriting = _products._extract_mk_copywriting
api_mk_copywriting = _products.api_mk_copywriting
api_list_products = _products.api_list_products
api_create_product = _products.api_create_product
api_get_product = _products.api_get_product
api_update_product = _products.api_update_product
api_update_product_owner = _products.api_update_product_owner
api_delete_product = _products.api_delete_product

_product_links_push_error_response = _push_routes._product_links_push_error_response
_product_localized_texts_push_error_response = _push_routes._product_localized_texts_push_error_response
_product_unsuitable_push_error_response = _push_routes._product_unsuitable_push_error_response
api_product_links_push_payload = _push_routes.api_product_links_push_payload
api_product_links_push = _push_routes.api_product_links_push
api_product_unsuitable_push_payload = _push_routes.api_product_unsuitable_push_payload
api_product_unsuitable_push = _push_routes.api_product_unsuitable_push
api_product_localized_texts_push_payload = _push_routes.api_product_localized_texts_push_payload
api_product_localized_texts_push = _push_routes.api_product_localized_texts_push

_shopify_image_lang_or_404 = _shopify_image_routes._shopify_image_lang_or_404
api_product_shopify_image_confirm = _shopify_image_routes.api_product_shopify_image_confirm
api_product_shopify_image_unavailable = _shopify_image_routes.api_product_shopify_image_unavailable
api_product_shopify_image_clear = _shopify_image_routes.api_product_shopify_image_clear
api_product_shopify_image_requeue = _shopify_image_routes.api_product_shopify_image_requeue

_collect_link_check_reference_images = _link_check_routes._collect_link_check_reference_images
api_product_link_check_create = _link_check_routes.api_product_link_check_create
api_product_link_check_get = _link_check_routes.api_product_link_check_get
api_product_link_check_detail = _link_check_routes.api_product_link_check_detail
