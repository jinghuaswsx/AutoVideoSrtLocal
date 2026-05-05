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
    system_audit,
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

bp = Blueprint("medias", __name__, url_prefix="/medias")


def _media_row_label(row: dict | None) -> str:
    row = row or {}
    return (
        str(row.get("display_name") or "").strip()
        or str(row.get("filename") or "").strip()
        or str(row.get("name") or "").strip()
        or str(row.get("product_code") or "").strip()
        or str(row.get("object_key") or "").strip()
    )


def _audit_media_item_access(item: dict | None) -> None:
    if not item:
        return
    object_key = str(item.get("object_key") or "").strip()
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action="media_video_access",
        module="medias",
        target_type="media_item",
        target_id=item.get("id"),
        target_label=_media_row_label(item),
        detail={
            "product_id": item.get("product_id"),
            "lang": item.get("lang"),
            "filename": item.get("filename"),
            "display_name": item.get("display_name"),
            "object_key": object_key,
            "file_size": item.get("file_size"),
            "range": request.headers.get("Range"),
        },
    )


def _audit_raw_source_video_access(row: dict | None) -> None:
    if not row:
        return
    object_key = str(row.get("video_object_key") or "").strip()
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action="raw_source_video_access",
        module="medias",
        target_type="raw_source",
        target_id=row.get("id"),
        target_label=_media_row_label(row),
        detail={
            "product_id": row.get("product_id"),
            "display_name": row.get("display_name"),
            "object_key": object_key,
            "file_size": row.get("file_size"),
            "range": request.headers.get("Range"),
        },
    )


def _audit_detail_images_zip_download(
    product: dict | None,
    product_id: int,
    *,
    action: str,
    detail: dict,
) -> None:
    product = product or {}
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="medias",
        target_type="media_product",
        target_id=product_id,
        target_label=_media_row_label(product) or str(product_id),
        detail={
            "product_id": product_id,
            "product_code": product.get("product_code"),
            **detail,
        },
    )


def _audit_media_item_deleted(item: dict | None) -> None:
    if not item:
        return
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action="media_item_deleted",
        module="medias",
        target_type="media_item",
        target_id=item.get("id"),
        target_label=_media_row_label(item),
        detail={
            "product_id": item.get("product_id"),
            "lang": item.get("lang"),
            "filename": item.get("filename"),
            "display_name": item.get("display_name"),
            "object_key": item.get("object_key"),
        },
    )


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
# ---------- 明空选品 ----------

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
from . import mk_selection as _mk_selection
from . import media_upload as _media_upload

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
from ._helpers import _is_admin
_build_mk_request_headers = _mk_selection._build_mk_request_headers
_get_mk_api_base_url = _mk_selection._get_mk_api_base_url
_get_mk_token = _mk_selection._get_mk_token
_is_mk_login_expired = _mk_selection._is_mk_login_expired
_normalize_mk_media_path = _mk_selection._normalize_mk_media_path
_mk_video_cache_object_key = _mk_selection._mk_video_cache_object_key
_cache_mk_video = _mk_selection._cache_mk_video
api_local_media_upload = _media_upload.api_local_media_upload
media_object_proxy = _media_upload.media_object_proxy
public_media_object = _media_upload.public_media_object
api_mk_selection = _mk_selection.api_mk_selection
api_mk_selection_refresh = _mk_selection.api_mk_selection_refresh
api_mk_media_proxy = _mk_selection.api_mk_media_proxy
api_mk_video_proxy = _mk_selection.api_mk_video_proxy
api_mk_detail_proxy = _mk_selection.api_mk_detail_proxy
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
_build_raw_sources_list_response = _raw_sources._build_raw_sources_list_response
_build_raw_source_update_response = _raw_sources._build_raw_source_update_response
_build_raw_source_delete_response = _raw_sources._build_raw_source_delete_response
api_product_evaluate = _evaluation.api_product_evaluate
api_product_evaluate_request_preview = _evaluation.api_product_evaluate_request_preview
api_product_evaluate_request_payload = _evaluation.api_product_evaluate_request_payload
_build_product_evaluation_response = _evaluation._build_product_evaluation_response
_build_product_evaluation_preview_response = _evaluation._build_product_evaluation_preview_response
_build_product_evaluation_payload_response = _evaluation._build_product_evaluation_payload_response

_medias_page_context = _pages._medias_page_context
index = _pages.index
product_detail_page = _pages.product_detail_page
translation_tasks_page = _pages.translation_tasks_page
api_list_active_users = _pages.api_list_active_users
api_list_languages = _pages.api_list_languages
mk_selection_page = _pages.mk_selection_page

_normalize_mk_copywriting_query = _products._normalize_mk_copywriting_query
_mk_product_link_tail = _products._mk_product_link_tail
_format_mk_copywriting_text = _products._format_mk_copywriting_text
_extract_mk_copywriting = _products._extract_mk_copywriting
_build_mk_copywriting_response = _products._build_mk_copywriting_response
_build_supply_pairing_search_response = _products._build_supply_pairing_search_response
_build_xmyc_skus_list_response = _products._build_xmyc_skus_list_response
_build_product_xmyc_skus_response = _products._build_product_xmyc_skus_response
_build_product_xmyc_skus_set_response = _products._build_product_xmyc_skus_set_response
_build_xmyc_sku_update_response = _products._build_xmyc_sku_update_response
_build_parcel_cost_suggest_response = _products._build_parcel_cost_suggest_response
_build_refresh_product_shopify_sku_response = _products._build_refresh_product_shopify_sku_response
_build_products_list_response = _products._build_products_list_response
_build_product_detail_response = _products._build_product_detail_response
_build_product_owner_update_response = _products._build_product_owner_update_response
_build_product_create_response = _products._build_product_create_response
_build_product_update_response = _products._build_product_update_response
_build_product_delete_response = _products._build_product_delete_response
api_mk_copywriting = _products.api_mk_copywriting
api_list_products = _products.api_list_products
api_create_product = _products.api_create_product
api_get_product = _products.api_get_product
api_update_product = _products.api_update_product
api_update_product_owner = _products.api_update_product_owner
api_delete_product = _products.api_delete_product
api_refresh_product_shopify_sku = _products.api_refresh_product_shopify_sku

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
