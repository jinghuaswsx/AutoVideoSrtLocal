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
from urllib.parse import quote, urlparse
import uuid
import requests
from flask import Blueprint, Response, render_template, request, jsonify, abort, redirect, send_file, url_for
from flask_login import login_required, current_user

from appcore import local_media_storage, material_evaluation, medias, object_keys, pushes, task_state
from appcore import image_translate_runtime
from appcore import image_translate_settings as its
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.gemini_image import coerce_image_model
from appcore.material_filename_rules import (
    validate_initial_material_filename,
    validate_material_filename,
)
from config import OUTPUT_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration
from web import store
from web.background import start_background_task
from web.routes import image_translate as image_translate_routes
from web.services import image_translate_runner, link_check_runner

import re

import pymysql.err

log = logging.getLogger(__name__)

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB
_ALLOWED_RAW_VIDEO_TYPES = ("video/mp4", "video/quicktime")
_MAX_RAW_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_MK_VIDEO_CACHE_PREFIX = "mk-selection/videos"


def _schedule_material_evaluation(pid: int, *, force: bool = False) -> None:
    start_background_task(
        material_evaluation.evaluate_product_if_ready,
        int(pid),
        force=force,
    )


def _parse_lang(body: dict, default: str = "en") -> tuple[str | None, str | None]:
    """Return (lang, error). When validation fails, return (None, error)."""
    lang = (body.get("lang") or default).strip().lower()
    if not medias.is_valid_language(lang):
        return None, f"涓嶆敮鎸佺殑璇: {lang}"
    return lang, None


def _resolve_upload_user_id(user_id: int | None = None) -> int | None:
    if user_id is not None:
        return int(user_id)
    try:
        resolved = getattr(current_user, "id", None)
    except Exception:
        resolved = None
    return int(resolved) if resolved is not None else None


def _download_image_to_local_media(
    url: str, pid: int, prefix: str, *, user_id: int | None = None
) -> tuple[str, bytes, str] | tuple[None, None, str]:
    """Download an image from URL and store it in local media storage."""
    if not url:
        return None, None, "url required"
    upload_user_id = _resolve_upload_user_id(user_id)
    if upload_user_id is None:
        return None, None, "missing upload user"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "only http/https links are supported"
    try:
        resp = requests.get(url, timeout=20, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 AutoVideoSrt-Importer"})
        resp.raise_for_status()
        ct = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            return None, None, f"闈炲浘鐗囩被鍨? {ct}"
        data = b""
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            data += chunk
            if len(data) > _MAX_IMAGE_BYTES:
                return None, None, "image too large (>15MB)"
    except requests.RequestException as e:
        return None, None, f"涓嬭浇澶辫触: {e}"

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ct, ".jpg")
    name_from_url = os.path.basename(parsed.path or "") or "from_url"
    filename = f"{prefix}_{name_from_url}"
    if not filename.endswith(ext):
        filename += ext
    object_key = object_keys.build_media_object_key(upload_user_id, pid, filename)
    local_media_storage.write_bytes(object_key, data)
    return object_key, data, ext

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")
_PRODUCT_CODE_SUFFIX = "-rjc"
_PRODUCT_CODE_SUFFIX_ERROR = "Product ID 必须以 -RJC 结尾"


def _validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "浜у搧 ID 蹇呭～"
    if not code.endswith(_PRODUCT_CODE_SUFFIX):
        return False, _PRODUCT_CODE_SUFFIX_ERROR
    if not _SLUG_RE.match(code):
        return False, "浜у搧 ID 鍙兘浣跨敤灏忓啓瀛楁瘝銆佹暟瀛楀拰杩炲瓧绗︼紝闀垮害 3-128锛屼笖棣栧熬涓嶈兘鏄繛瀛楃"
    return True, None


@lru_cache(maxsize=1)
def _dianxiaomi_rankings_columns() -> frozenset[str]:
    rows = db_query("SHOW COLUMNS FROM dianxiaomi_rankings")
    return frozenset(
        str(row.get("Field") or "").strip()
        for row in rows
        if row.get("Field")
    )


def _language_name_map() -> dict[str, str]:
    return {
        str(row.get("code") or "").strip().lower(): str(row.get("name_zh") or "").strip()
        for row in medias.list_languages()
        if str(row.get("code") or "").strip()
    }


def _validate_material_filename_for_product(
    filename: str,
    product: dict,
    lang: str,
    *,
    initial_upload: bool = False,
):
    validator = validate_initial_material_filename if initial_upload else validate_material_filename
    result = validator(
        filename,
        (product or {}).get("name") or "",
        lang,
        _language_name_map(),
    )
    if result.ok:
        return result, None
    return result, (
        jsonify({
            "error": "filename_invalid",
            "message": "文件名不符合命名规范",
            "details": list(result.errors),
            "effective_lang": result.effective_lang,
            "suggested_filename": result.suggested_filename,
        }),
        400,
    )


_DATE_PREFIX_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-")
_RAW_SOURCE_TITLE_DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")


def _check_filename_prefix(filename: str, product: dict) -> str | None:
    """轻量校验：只检查文件名以 YYYY.MM.DD-{产品名}- 开头，其余不限制。"""
    product_name = (product or {}).get("name") or ""
    if not product_name:
        return None
    if not _DATE_PREFIX_RE.match(filename):
        return '文件名必须以 "YYYY.MM.DD-" 开头'
    date_len = 10
    rest = filename[date_len + 1:]  # skip date + "-"
    if not rest.startswith(product_name + "-"):
        return f'日期之后必须紧跟 "{product_name}-"'
    return None


def _suggest_raw_source_title(product: dict) -> str | None:
    product_name = ((product or {}).get("name") or "").strip()
    if not product_name:
        return None
    today = datetime.now().strftime("%Y.%m.%d")
    return f"{today}-{product_name}-原始视频.mp4"


def _validate_raw_source_display_name(title: str, product: dict) -> list[str]:
    value = (title or "").strip()
    product_name = ((product or {}).get("name") or "").strip()
    errors: list[str] = []

    if not product_name:
        return ["当前产品尚未加载，请重试"]
    if not value:
        return ["名称不能为空，格式为 YYYY.MM.DD-产品名-xxxxxx.mp4"]

    if not value.lower().endswith(".mp4"):
        errors.append("名称必须以 .mp4 结尾")

    if len(value) < 11 or value[10] != "-":
        errors.append('名称必须以 "YYYY.MM.DD-" 开头')
        return errors

    date_str = value[:10]
    match = _RAW_SOURCE_TITLE_DATE_RE.match(date_str)
    if not match:
        errors.append(f'日期段 "{date_str}" 格式必须是 YYYY.MM.DD')
    else:
        year, month, day = (int(part) for part in match.groups())
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            parsed = None
        if parsed is None or parsed.strftime("%Y.%m.%d") != date_str:
            errors.append(f'日期 "{date_str}" 不是合法日期')

    expected_prefix = f"{date_str}-{product_name}-"
    if not value.startswith(expected_prefix):
        errors.append(f'日期之后必须紧跟 "{product_name}-"')
        return errors

    tail = value[len(expected_prefix):]
    if tail.lower().endswith(".mp4"):
        tail = tail[:-4]
    if not tail.strip():
        errors.append("产品名之后的描述不能为空")
    return errors

bp = Blueprint("medias", __name__, url_prefix="/medias")

THUMB_DIR = Path(OUTPUT_DIR) / "media_thumbs"
_local_upload_guard = threading.Lock()
_local_upload_reservations: dict[str, dict] = {}


def _reserve_local_media_upload(object_key: str) -> dict[str, str]:
    upload_id = uuid.uuid4().hex
    with _local_upload_guard:
        _local_upload_reservations[upload_id] = {
            "user_id": int(current_user.id),
            "object_key": object_key,
        }
    return {
        "object_key": object_key,
        "upload_url": url_for("medias.api_local_media_upload", upload_id=upload_id),
    }


def _is_media_available(object_key: str) -> bool:
    if not object_key:
        return False
    return local_media_storage.exists(object_key)


def _download_media_object(object_key: str, destination: str | os.PathLike[str]) -> str:
    if local_media_storage.exists(object_key):
        return local_media_storage.download_to(object_key, destination)
    raise FileNotFoundError(f"local media object not found: {object_key}")


def _delete_media_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass


def _send_media_object(object_key: str):
    if _is_media_available(object_key):
        return send_file(
            str(local_media_storage.local_path_for(object_key)),
            mimetype=mimetypes.guess_type(object_key)[0] or "application/octet-stream",
        )
    abort(404)


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

def _can_access_product(product: dict | None) -> bool:
    # 鍏变韩濯掍綋搴擄細鍙浜у搧瀛樺湪灏卞厑璁歌闂€?
    return product is not None


def _product_not_listed_response():
    return jsonify({
        "error": "product_not_listed",
        "message": "产品已下架，不能执行该操作",
    }), 409


def _ensure_product_listed(product: dict | None):
    if not medias.is_product_listed(product):
        return _product_not_listed_response()
    return None


def _json_number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _start_image_translate_runner(task_id: str, user_id: int) -> bool:
    return image_translate_routes.start_image_translate_runner(task_id, user_id)


def _default_image_translate_model_id() -> str:
    channel = "aistudio"
    try:
        channel = its.get_channel()
    except Exception:
        pass
    try:
        return its.get_default_model(channel)
    except Exception:
        return coerce_image_model("", channel=channel)


def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None,
                       covers: dict[str, str] | None = None,
                       raw_sources_count: int | None = None) -> dict:
    if covers is None:
        covers = medias.get_product_covers(p["id"])
    has_en_cover = "en" in covers
    cover_url = f"/medias/cover/{p['id']}?lang=en" if has_en_cover else (
        f"/medias/thumb/{cover_item_id}" if cover_item_id else None
    )
    # localized_links_json 鍙兘鏄?str / dict / None
    raw_links = p.get("localized_links_json")
    localized_links: dict = {}
    if isinstance(raw_links, dict):
        localized_links = raw_links
    elif isinstance(raw_links, str):
        try:
            parsed = json.loads(raw_links)
            if isinstance(parsed, dict):
                localized_links = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    link_check_tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "mk_id": p.get("mk_id"),
        "owner_name": (p.get("owner_name") or "").strip(),
        "has_en_cover": has_en_cover,
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "remark": p.get("remark") or "",
        "ai_score": _json_number_or_none(p.get("ai_score")),
        "ai_evaluation_result": p.get("ai_evaluation_result") or "",
        "ai_evaluation_detail": p.get("ai_evaluation_detail") or "",
        "listing_status": medias.normalize_listing_status(p.get("listing_status")),
        "ad_supported_langs": p.get("ad_supported_langs") or "",
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "items_filenames": items_filenames or [],
        "cover_thumbnail_url": cover_url,
        "lang_coverage": lang_coverage or {},
        "localized_links": localized_links,
        "link_check_tasks": link_check_tasks,
        "raw_sources_count": raw_sources_count or 0,
    }


def _serialize_item(it: dict, raw_sources_by_id: dict[int, dict] | None = None) -> dict:
    has_user_cover = bool(it.get("cover_object_key"))
    raw_sources_by_id = raw_sources_by_id or {}
    source_raw_id = _int_or_none(it.get("source_raw_id"))
    if source_raw_id is None and it.get("auto_translated"):
        source_raw_id = _int_or_none(it.get("source_ref_id"))
    source_raw = raw_sources_by_id.get(source_raw_id or 0)
    source_raw_payload = None
    if source_raw_id is not None:
        source_raw_payload = {
            "id": source_raw_id,
            "display_name": (
                (source_raw or {}).get("display_name")
                or f"原始去字幕素材 #{source_raw_id}"
            ),
            "video_url": f"/medias/raw-sources/{source_raw_id}/video",
            "cover_url": f"/medias/raw-sources/{source_raw_id}/cover",
        }
    return {
        "id": it["id"],
        "lang": it.get("lang") or "en",
        "filename": it["filename"],
        "display_name": it.get("display_name") or it["filename"],
        "object_key": it["object_key"],
        "cover_object_key": it.get("cover_object_key"),
        "thumbnail_url": f"/medias/thumb/{it['id']}" if it.get("thumbnail_path") else None,
        "cover_url": (
            f"/medias/item-cover/{it['id']}" if has_user_cover
            else (f"/medias/thumb/{it['id']}" if it.get("thumbnail_path") else None)
        ),
        "duration_seconds": it.get("duration_seconds"),
        "file_size": it.get("file_size"),
        "source_raw_id": source_raw_id,
        "source_ref_id": _int_or_none(it.get("source_ref_id")),
        "bulk_task_id": it.get("bulk_task_id") or "",
        "auto_translated": bool(it.get("auto_translated")),
        "source_raw": source_raw_payload,
        "created_at": it["created_at"].isoformat() if it.get("created_at") else None,
    }


def _serialize_raw_source(row: dict) -> dict:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "display_name": row.get("display_name") or "",
        "video_object_key": row["video_object_key"],
        "cover_object_key": row["cover_object_key"],
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "sort_order": row.get("sort_order") or 0,
        "translations": row.get("translations") or {},
        "video_url": f"/medias/raw-sources/{row['id']}/video",
        "cover_url": f"/medias/raw-sources/{row['id']}/cover",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _serialize_link_check_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "link_url": task["link_url"],
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
        "target_language": task["target_language"],
        "target_language_name": task["target_language_name"],
        "progress": dict(task.get("progress") or {}),
        "summary": dict(task.get("summary") or {}),
        "error": task.get("error", ""),
        "reference_images": [
            {
                "id": ref["id"],
                "filename": ref["filename"],
                "preview_url": f"/api/link-check/tasks/{task['id']}/images/reference/{ref['id']}",
            }
            for ref in task.get("reference_images", [])
        ],
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "source_url": item["source_url"],
                "site_preview_url": f"/api/link-check/tasks/{task['id']}/images/site/{item['id']}",
                "analysis": dict(item.get("analysis") or {}),
                "reference_match": dict(item.get("reference_match") or {}),
                "binary_quick_check": dict(item.get("binary_quick_check") or {}),
                "same_image_llm": dict(item.get("same_image_llm") or {}),
                "status": item.get("status") or "pending",
                "error": item.get("error") or "",
            }
            for item in task.get("items", [])
        ],
    }


def _collect_link_check_reference_images(pid: int, lang: str, task_dir: Path) -> list[dict]:
    references: list[dict] = []
    ref_dir = task_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cover_key = medias.get_product_covers(pid).get(lang)
    if cover_key:
        cover_suffix = Path(cover_key).suffix or ".jpg"
        cover_local = ref_dir / f"cover_{lang}{cover_suffix}"
        _download_media_object(cover_key, cover_local)
        references.append({
            "id": f"cover-{lang}",
            "filename": f"cover_{lang}{cover_suffix}",
            "local_path": str(cover_local),
        })

    for idx, row in enumerate(medias.list_detail_images(pid, lang), start=1):
        object_key = row.get("object_key") or ""
        detail_suffix = Path(object_key).suffix or ".jpg"
        detail_local = ref_dir / f"detail_{idx:03d}{detail_suffix}"
        _download_media_object(object_key, detail_local)
        references.append({
            "id": f"detail-{row['id']}",
            "filename": f"detail_{idx:03d}{detail_suffix}",
            "local_path": str(detail_local),
        })

    return references


# ---------- 椤甸潰 ----------

@bp.route("/")
@login_required
def index():
    return render_template("medias_list.html")


@bp.route("/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def translation_tasks_page(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    return render_template(
        "medias_translation_tasks.html",
        product=product,
        product_id=pid,
    )
# ---------- 缈昏瘧浠诲姟 ----------



# ---------- 浜у搧 API ----------

def _normalize_mk_copywriting_query(product_code: str) -> str:
    code = (product_code or "").strip().lower()
    if code.endswith("-rjc"):
        code = code[:-4]
    return code


def _mk_product_link_tail(item: dict) -> str:
    links = item.get("product_links") or []
    if not isinstance(links, list) or not links:
        return ""
    first_link = links[0]
    if not isinstance(first_link, str):
        return ""
    return first_link.rstrip("/").rsplit("/", 1)[-1].strip().lower()


def _format_mk_copywriting_text(text: dict) -> str:
    title = str(text.get("title") or "").strip()
    message = str(text.get("message") or "").strip()
    description = str(text.get("description") or "").strip()
    if not any((title, message, description)):
        return ""
    return "\n".join((
        f"标题: {title}",
        f"文案: {message}",
        f"描述: {description}",
    ))


def _extract_mk_copywriting(data: dict, product_code: str) -> tuple[int | None, str]:
    items = ((data.get("data") or {}).get("items") or [])
    if not isinstance(items, list):
        return None, ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if _mk_product_link_tail(item) != product_code:
            continue
        texts = item.get("texts") or []
        if not isinstance(texts, list):
            return item.get("id"), ""
        for text in texts:
            if not isinstance(text, dict):
                continue
            copywriting = _format_mk_copywriting_text(text)
            if copywriting:
                return item.get("id"), copywriting
        return item.get("id"), ""
    return None, ""


@bp.route("/api/mk-copywriting", methods=["GET"])
@login_required
def api_mk_copywriting():
    query = _normalize_mk_copywriting_query(
        request.args.get("product_code") or request.args.get("q") or ""
    )
    if not query:
        return jsonify({"error": "product_code_required", "message": "请先填写产品 ID"}), 400

    headers = _build_mk_request_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return jsonify({
            "error": "mk_credentials_missing",
            "message": "明空凭据未配置，请先在设置页同步 wedev 凭据",
        }), 500

    url = f"{_get_mk_api_base_url()}/api/marketing/medias"
    params = {"page": 1, "q": query, "source": "", "level": "", "show_attention": 0}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return jsonify({"error": "mk_request_failed", "message": str(exc)}), 502

    if not resp.ok:
        return jsonify({
            "error": "mk_request_failed",
            "message": f"明空接口返回 HTTP {resp.status_code}",
        }), 502

    try:
        data = resp.json() or {}
    except ValueError:
        return jsonify({"error": "mk_response_invalid", "message": "明空返回数据格式异常"}), 502

    if _is_mk_login_expired(data):
        return jsonify({"error": "mk_credentials_expired", "message": "明空登录已失效，请重新同步 wedev 凭据"}), 401

    source_item_id, copywriting = _extract_mk_copywriting(data, query)
    if source_item_id is None:
        return jsonify({
            "error": "mk_copywriting_not_found",
            "message": f"明空系统未找到产品 ID 为 {query} 的文案",
            "query": query,
        }), 404
    if not copywriting:
        return jsonify({
            "error": "mk_copywriting_empty",
            "message": f"明空产品 {query} 没有可用文案",
            "query": query,
            "source_item_id": source_item_id,
        }), 404

    return jsonify({
        "ok": True,
        "query": query,
        "source_item_id": source_item_id,
        "copywriting": copywriting,
    })


@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    rows, total = medias.list_products(None, keyword=keyword, archived=archived,
                                       offset=offset, limit=limit)
    pids = [r["id"] for r in rows]
    counts = medias.count_items_by_product(pids)
    raw_counts = medias.count_raw_sources_by_product(pids)
    thumb_covers = medias.first_thumb_item_by_product(pids)
    filenames = medias.list_item_filenames_by_product(pids, limit_per=5)
    coverage = medias.lang_coverage_by_product(pids)
    covers_map = medias.get_product_covers_batch(pids)
    data = [
        _serialize_product(
            r, counts.get(r["id"], 0), thumb_covers.get(r["id"]),
            items_filenames=filenames.get(r["id"], []),
            lang_coverage=coverage.get(r["id"], {}),
            covers=covers_map.get(r["id"], {}),
            raw_sources_count=raw_counts.get(r["id"], 0),
        )
        for r in rows
    ]
    return jsonify({"items": data, "total": total, "page": page, "page_size": limit})


@bp.route("/api/products", methods=["POST"])
@login_required
def api_create_product():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    product_code = (body.get("product_code") or "").strip().lower() or None
    if product_code is not None:
        ok, err = _validate_product_code(product_code)
        if not ok:
            return jsonify({"error": err}), 400
        if medias.get_product_by_code(product_code):
            return jsonify({"error": "product_code already exists"}), 409
    pid = medias.create_product(
        current_user.id, name,
        product_code=product_code,
    )
    return jsonify({"id": pid}), 201


@bp.route("/api/products/<int:pid>", methods=["GET"])
@login_required
def api_get_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    covers = medias.get_product_covers(pid)
    items = medias.list_items(pid)
    needs_raw_sources = any(
        _int_or_none(item.get("source_raw_id"))
        or (item.get("auto_translated") and _int_or_none(item.get("source_ref_id")))
        for item in items
    )
    raw_sources_by_id = {}
    if needs_raw_sources:
        raw_sources_by_id = {
            int(row["id"]): row
            for row in medias.list_raw_sources(pid)
            if row.get("id") is not None
        }
    return jsonify({
        "product": _serialize_product(p, None, covers=covers),
        "covers": covers,
        "copywritings": medias.list_copywritings(pid),
        "items": [_serialize_item(i, raw_sources_by_id) for i in items],
    })


@bp.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}

    # 鍩虹淇℃伅瀛楁锛氬彧鏈?body 閲屾樉寮忓甫浜嗘墠鏍￠獙+鍐欏洖锛屾敮鎸侀儴鍒嗘洿鏂?
    # 锛堝垪琛?inline edit 涔嬬被鐨勮交閲忔洿鏂板彧浼氭惡甯?mk_id / ad_supported_langs 绛夊崟瀛楁锛?
    update_fields: dict = {}

    if "name" in body:
        name = (body.get("name") or "").strip() or p["name"]
        update_fields["name"] = name

    if "product_code" in body:
        product_code = (body.get("product_code") or "").strip().lower()
        ok, err = _validate_product_code(product_code)
        if not ok:
            return jsonify({"error": err}), 400
        exist = medias.get_product_by_code(product_code)
        if exist and exist["id"] != pid:
            return jsonify({"error": "product_code already exists"}), 409
        update_fields["product_code"] = product_code

    # 鏄庣┖ ID锛坢k_id锛夛細閫夊～锛?-8 浣嶆暟瀛楋紝绌轰覆浠ｈ〃娓呴櫎
    if "mk_id" in body:
        update_fields["mk_id"] = body.get("mk_id")

    for key in (
        "remark",
        "ai_score",
        "ai_evaluation_result",
        "ai_evaluation_detail",
        "listing_status",
    ):
        if key in body:
            update_fields[key] = body.get(key)

    # 鍙€夛細localized_links 鈥?姣忚瑷€瑕嗙洊鍟嗗搧閾炬帴锛坉ict {lang: url}锛?
    if isinstance(body.get("localized_links"), dict):
        cleaned = {}
        for lang, url in body["localized_links"].items():
            url = (url or "").strip()
            if url and medias.is_valid_language(lang):
                cleaned[lang] = url
        update_fields["localized_links_json"] = cleaned

    if "ad_supported_langs" in body:
        raw = body.get("ad_supported_langs") or ""
        if isinstance(raw, list):
            parts = [str(x).strip().lower() for x in raw if str(x).strip()]
        else:
            parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
        # 杩囨护鎺夐潪娉曡绉?& 鍘婚噸 & 鎺掗櫎 en
        seen: set[str] = set()
        kept: list[str] = []
        for code in parts:
            if code == "en" or code in seen:
                continue
            if not medias.is_valid_language(code):
                continue
            seen.add(code)
            kept.append(code)
        update_fields["ad_supported_langs"] = ",".join(kept) if kept else None
    try:
        medias.update_product(pid, **update_fields)
    except ValueError as e:
        return jsonify({"error": "invalid_product_field", "message": str(e)}), 400
    except pymysql.err.IntegrityError as e:
        code = e.args[0] if e.args else None
        if code == 1062 and "uk_media_products_mk_id" in str(e):
            return jsonify({
                "error": "mk_id_conflict",
                "message": "鏄庣┖ ID 宸茶鍏朵粬浜у搧鍗犵敤",
            }), 409
        raise

    if {"name", "product_code", "localized_links_json"} & set(update_fields):
        _schedule_material_evaluation(pid, force=True)

    if isinstance(body.get("copywritings"), dict):
        for lang_code, lang_items in body["copywritings"].items():
            if not medias.is_valid_language(lang_code):
                continue
            if isinstance(lang_items, list):
                medias.replace_copywritings(pid, lang_items, lang=lang_code)
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>/link-check", methods=["POST"])
@login_required
def api_product_link_check_create(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "").strip().lower()
    if not lang or not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400

    link_url = (body.get("link_url") or "").strip()
    if not link_url.startswith(("http://", "https://")):
        return jsonify({"error": "valid product link_url required"}), 400

    language = medias.get_language(lang)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target language is invalid"}), 400

    task_id = str(uuid.uuid4())
    task_dir = Path(OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    references = _collect_link_check_reference_images(pid, lang, task_dir)
    if not references:
        return jsonify({"error": "当前语种缺少参考图"}), 400

    store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=lang,
        target_language_name=language.get("name_zh") or lang,
        reference_images=references,
    )
    medias.set_product_link_check_task(pid, lang, {
        "task_id": task_id,
        "status": "queued",
        "link_url": link_url,
        "checked_at": datetime.now(UTC).isoformat(),
        "summary": {
            "overall_decision": "running",
            "pass_count": 0,
            "replace_count": 0,
            "review_count": 0,
        },
    })
    link_check_runner.start(task_id)
    return jsonify({"task_id": task_id, "status": "queued", "reference_count": len(references)}), 202


@bp.route("/api/products/<int:pid>/link-check/<lang>", methods=["GET"])
@login_required
def api_product_link_check_get(pid: int, lang: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"task": None})

    task = store.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"task": None})

    refreshed = {
        "task_id": meta.get("task_id", ""),
        "status": task.get("status", meta.get("status", "")),
        "link_url": meta.get("link_url", ""),
        "checked_at": meta.get("checked_at", ""),
        "summary": dict(task.get("summary") or meta.get("summary") or {}),
        "progress": dict(task.get("progress") or {}),
        "has_detail": True,
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
    }
    medias.set_product_link_check_task(pid, lang, {
        "task_id": refreshed["task_id"],
        "status": refreshed["status"],
        "link_url": refreshed["link_url"],
        "checked_at": refreshed["checked_at"],
        "summary": refreshed["summary"],
    })
    return jsonify({"task": refreshed})


@bp.route("/api/products/<int:pid>/link-check/<lang>/detail", methods=["GET"])
@login_required
def api_product_link_check_detail(pid: int, lang: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    tasks = medias.parse_link_check_tasks_json(p.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return jsonify({"error": "task not found"}), 404

    task = store.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        return jsonify({"error": "task not found"}), 404
    return jsonify(_serialize_link_check_task(task))


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    medias.soft_delete_product(pid)
    return jsonify({"ok": True})


# ---------- 绱犳潗涓婁紶 ----------

@bp.route("/api/products/<int:pid>/raw-sources", methods=["GET"])
@login_required
def api_list_raw_sources(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    rows = medias.list_raw_sources(pid)
    return jsonify({"items": [_serialize_raw_source(r) for r in rows]})


@bp.route("/api/products/<int:pid>/raw-sources", methods=["POST"])
@login_required
def api_create_raw_source(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    video = request.files.get("video")
    cover = request.files.get("cover")
    if not video or not cover:
        return jsonify({"error": "video and cover both required"}), 400

    video_ct = (video.mimetype or "").lower()
    cover_ct = (cover.mimetype or "").lower()
    if video_ct not in _ALLOWED_RAW_VIDEO_TYPES:
        return jsonify({"error": f"video mimetype not allowed: {video_ct}"}), 400
    if cover_ct not in _ALLOWED_IMAGE_TYPES:
        return jsonify({"error": f"cover mimetype not allowed: {cover_ct}"}), 400

    display_name = (request.form.get("display_name") or "").strip()
    title_errors = _validate_raw_source_display_name(display_name, p)
    if title_errors:
        return jsonify({
            "error": "raw_source_title_invalid",
            "message": "原始去字幕视频素材名称不符合命名规范",
            "details": title_errors,
            "suggested_title": _suggest_raw_source_title(p),
        }), 400

    uid = _resolve_upload_user_id()
    if uid is None:
        return jsonify({"error": "missing upload user"}), 400

    video_key = object_keys.build_media_raw_source_key(
        uid, pid, kind="video", filename=video.filename or "video.mp4",
    )
    cover_key = object_keys.build_media_raw_source_key(
        uid, pid, kind="cover", filename=cover.filename or "cover.jpg",
    )

    video_bytes = b""
    for chunk in iter(lambda: video.stream.read(1024 * 1024), b""):
        video_bytes += chunk
        if len(video_bytes) > _MAX_RAW_VIDEO_BYTES:
            return jsonify({"error": "video too large (>2GB)"}), 400

    cover_bytes = cover.read()
    if len(cover_bytes) > _MAX_IMAGE_BYTES:
        return jsonify({"error": "cover too large (>15MB)"}), 400

    try:
        local_media_storage.write_bytes(video_key, video_bytes)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"upload video failed: {exc}"}), 500
    try:
        local_media_storage.write_bytes(cover_key, cover_bytes)
    except Exception as exc:  # noqa: BLE001
        _delete_media_object(video_key)
        return jsonify({"error": f"upload cover failed: {exc}"}), 500

    duration_seconds = None
    width = None
    height = None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name
        duration_seconds = float(get_media_duration(tmp_path) or 0.0) or None
        info = probe_media_info_safe(tmp_path)
        width = info.get("width")
        height = info.get("height")
    except Exception:
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    try:
        rid = medias.create_raw_source(
            pid,
            uid,
            display_name=display_name,
            video_object_key=video_key,
            cover_object_key=cover_key,
            duration_seconds=duration_seconds,
            file_size=len(video_bytes),
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001
        _delete_media_object(video_key)
        _delete_media_object(cover_key)
        return jsonify({"error": f"db insert failed: {exc}"}), 500

    row = medias.get_raw_source(rid)
    return jsonify({"item": _serialize_raw_source(row)}), 201


@bp.route("/api/raw-sources/<int:rid>", methods=["PATCH"])
@login_required
def api_update_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    fields: dict = {}
    if "display_name" in body:
        fields["display_name"] = (body.get("display_name") or "").strip() or None
    if "sort_order" in body:
        try:
            fields["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            return jsonify({"error": "sort_order must be int"}), 400
    if not fields:
        return jsonify({"error": "no valid fields"}), 400
    medias.update_raw_source(rid, **fields)
    return jsonify({"item": _serialize_raw_source(medias.get_raw_source(rid))})


@bp.route("/api/raw-sources/<int:rid>", methods=["DELETE"])
@login_required
def api_delete_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    medias.soft_delete_raw_source(rid)
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or ["copywriting", "detail_images", "video_covers", "videos"]
    allowed_content_types = {"copywriting", "detail_images", "video_covers", "videos"}

    if ("videos" in content_types or "video_covers" in content_types) and not raw_ids:
        return jsonify({"error": "raw_ids 涓嶈兘涓虹┖"}), 400
    if not target_langs:
        return jsonify({"error": "target_langs 涓嶈兘涓虹┖"}), 400

    if not isinstance(content_types, list) or not content_types:
        return jsonify({"error": "content_types 娑撳秷鍏樻稉铏光敄"}), 400

    try:
        raw_ids_int = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "raw_ids must be integers"}), 400

    rows = medias.list_raw_sources(pid)
    valid_ids = {int(r["id"]) for r in rows}
    bad = [rid for rid in raw_ids_int if rid not in valid_ids]
    if bad:
        return jsonify({"error": f"raw_ids 涓嶅睘浜庤浜у搧鎴栧凡鍒犻櫎: {bad}"}), 400

    for lang in target_langs:
        if lang == "en" or not medias.is_valid_language(lang):
            return jsonify({"error": f"target_langs 闈炴硶: {lang}"}), 400

    for content_type in content_types:
        if content_type not in allowed_content_types:
            return jsonify({"error": f"content_types 闂堢偞纭? {content_type}"}), 400

    from appcore.bulk_translate_runtime import create_bulk_translate_task, start_task
    from web.routes.bulk_translate import _spawn_scheduler

    initiator = {
        "user_id": current_user.id,
        "user_name": getattr(current_user, "username", "") or "",
        "ip": request.remote_addr or "",
        "user_agent": request.headers.get("User-Agent", "") or "",
        "source": "medias_raw_translate",
    }
    task_id = create_bulk_translate_task(
        user_id=current_user.id,
        product_id=pid,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=body.get("video_params") or {},
        initiator=initiator,
        raw_source_ids=raw_ids_int,
    )
    start_task(task_id, current_user.id)
    start_background_task(_spawn_scheduler, task_id)
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    from appcore.bulk_translate_projection import list_product_task_ids, list_product_tasks
    from appcore.bulk_translate_runtime import sync_task_with_children_once

    for task_id in list_product_task_ids(current_user.id, pid):
        try:
            sync_task_with_children_once(task_id, user_id=current_user.id)
        except Exception:
            log.warning("bulk translation child sync failed task_id=%s", task_id, exc_info=True)

    return jsonify({"items": list_product_tasks(current_user.id, pid)})


@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = os.path.basename((body.get("filename") or "").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    validation, error_response = _validate_material_filename_for_product(
        filename,
        p,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if error_response:
        return error_response
    effective_lang = validation.effective_lang
    object_key = object_keys.build_media_object_key(current_user.id, pid, filename)
    return jsonify({
        "object_key": object_key,
        "effective_lang": effective_lang,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/items/complete", methods=["POST"])
@login_required
def api_item_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    filename = (body.get("filename") or "").strip()
    file_size = int(body.get("file_size") or 0)
    if not object_key or not filename:
        return jsonify({"error": "object_key and filename required"}), 400
    validation, error_response = _validate_material_filename_for_product(
        filename,
        p,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if error_response:
        return error_response
    lang = validation.effective_lang
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    cover_object_key = (body.get("cover_object_key") or "").strip() or None
    if cover_object_key and not _is_media_available(cover_object_key):
        cover_object_key = None

    item_id = medias.create_item(
        pid, current_user.id, filename, object_key,
        file_size=file_size or None,
        cover_object_key=cover_object_key,
        lang=lang,
    )

    # 涓嬭浇鐢ㄦ埛灏侀潰鍒版湰鍦扮紦瀛樹緵浠ｇ悊
    if cover_object_key:
        try:
            product_dir = THUMB_DIR / str(pid)
            product_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(cover_object_key).suffix or ".jpg"
            _download_media_object(
                cover_object_key, str(product_dir / f"item_cover_{item_id}{ext}"),
            )
        except Exception:
            pass

    # 鎶界缉鐣ュ浘锛堝け璐ヤ笉闃绘柇鍏ュ簱锛?
    try:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(exist_ok=True)
        tmp_video = product_dir / f"tmp_{item_id}_{Path(filename).name}"
        _download_media_object(object_key, str(tmp_video))
        duration = get_media_duration(str(tmp_video))
        thumb = extract_thumbnail(str(tmp_video), str(product_dir), scale="360:-1")
        if thumb:
            final = product_dir / f"{item_id}.jpg"
            os.replace(thumb, final)
            db_execute(
                "UPDATE media_items SET thumbnail_path=%s, duration_seconds=%s WHERE id=%s",
                (str(final.relative_to(OUTPUT_DIR)).replace("\\", "/"),
                 duration or None, item_id),
            )
        try:
            tmp_video.unlink()
        except Exception:
            pass
    except Exception:
        pass

    if lang == "en":
        _schedule_material_evaluation(pid)

    return jsonify({"id": item_id}), 201


@bp.route("/api/products/<int:pid>/cover/from-url", methods=["POST"])
@login_required
def api_cover_from_url(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key, data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), pid, f"cover_{lang}", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.set_product_cover(pid, lang, object_key)
    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / f"cover_{lang}{err_or_ext}").write_bytes(data)
    except Exception:
        pass
    if lang == "en":
        _schedule_material_evaluation(pid, force=True)
    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}", "object_key": object_key})


@bp.route("/api/products/<int:pid>/item-cover/from-url", methods=["POST"])
@login_required
def api_item_cover_from_url(pid: int):
    """Fetch an item cover from URL before the item record is created."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, _data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), pid, "item_cover", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    return jsonify({"ok": True, "object_key": object_key})


@bp.route("/api/items/<int:item_id>/cover/from-url", methods=["POST"])
@login_required
def api_item_cover_set_from_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), it["product_id"], "item_cover", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    old = it.get("cover_object_key")
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.update_item_cover(item_id, object_key)
    try:
        product_dir = THUMB_DIR / str(it["product_id"])
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / f"item_cover_{item_id}{err_or_ext}").write_bytes(data)
    except Exception:
        pass
    return jsonify({"ok": True, "cover_url": f"/medias/item-cover/{item_id}", "object_key": object_key})


@bp.route("/api/products/<int:pid>/item-cover/bootstrap", methods=["POST"])
@login_required
def api_item_cover_bootstrap(pid: int):
    """Reserve a local upload target for an item cover image."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    filename = os.path.basename((body.get("filename") or "item_cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = object_keys.build_media_object_key(
        current_user.id, pid, f"item_cover_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


@bp.route("/api/items/<int:item_id>/cover/set", methods=["POST"])
@login_required
def api_item_cover_set(item_id: int):
    """Bind an uploaded object key as the cover for an item."""
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    old = it.get("cover_object_key")
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass

    medias.update_item_cover(item_id, object_key)

    try:
        product_dir = THUMB_DIR / str(it["product_id"])
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"item_cover_{item_id}{ext}"
        _download_media_object(object_key, str(local))
    except Exception:
        pass

    return jsonify({"ok": True, "cover_url": f"/medias/item-cover/{item_id}"})


@bp.route("/item-cover/<int:item_id>")
@login_required
def item_cover(item_id: int):
    it = medias.get_item(item_id)
    if not it or not it.get("cover_object_key"):
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    product_dir = THUMB_DIR / str(it["product_id"])
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"item_cover_{item_id}{ext}"
        if f.exists():
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(f), mimetype=mime)
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(it["cover_object_key"]).suffix or ".jpg"
        local = product_dir / f"item_cover_{item_id}{ext}"
        _download_media_object(it["cover_object_key"], str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


@bp.route("/raw-sources/<int:rid>/video", methods=["GET"])
@login_required
def raw_source_video_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(row["video_object_key"])


@bp.route("/raw-sources/<int:rid>/cover", methods=["GET"])
@login_required
def raw_source_cover_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(row["cover_object_key"])


@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = os.path.basename((body.get("filename") or "cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = object_keys.build_media_object_key(
        current_user.id, pid, f"cover_{lang}_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass

    medias.set_product_cover(pid, lang, object_key)

    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{lang}{ext}"
        _download_media_object(object_key, str(local))
    except Exception:
        pass

    if lang == "en":
        _schedule_material_evaluation(pid, force=True)

    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}"})


@bp.route("/api/products/<int:pid>/cover", methods=["DELETE"])
@login_required
def api_cover_delete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "鑻辨枃涓诲浘涓嶈兘鍒犻櫎"}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.delete_product_cover(pid, lang)
    return jsonify({"ok": True})


@bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_item(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    medias.soft_delete_item(item_id)
    try:
        _delete_media_object(it["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


# ---------- 缂╃暐鍥句唬鐞?----------

@bp.route("/thumb/<int:item_id>")
@login_required
def thumb(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    if not it.get("thumbnail_path"):
        abort(404)
    full = Path(OUTPUT_DIR) / it["thumbnail_path"]
    if not full.exists():
        abort(404)
    return send_file(str(full), mimetype="image/jpeg")


@bp.route("/cover/<int:pid>")
@login_required
def cover(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    object_key = medias.resolve_cover(pid, lang)
    if not object_key:
        abort(404)
    covers = medias.get_product_covers(pid)
    actual_lang = lang if lang in covers else "en"
    product_dir = THUMB_DIR / str(pid)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"cover_{actual_lang}{ext}"
        if f.exists():
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(f), mimetype=mime)
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{actual_lang}{ext}"
        _download_media_object(object_key, str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


# ---------- 绛惧悕涓嬭浇锛堟挱鏀撅級 ----------

@bp.route("/api/items/<int:item_id>/play_url")
@login_required
def api_play_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    return jsonify({"url": url_for("medias.media_object_proxy", object_key=it["object_key"])})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_list_languages():
    return jsonify({"items": medias.list_languages()})


# ======================================================================
# 鍟嗗搧璇︽儏鍥撅紙product detail images锛?
# ----------------------------------------------------------------------
# 绗竴杞彧鍦ㄨ嫳璇绉嶆毚闇插叆鍙ｏ紝鍏朵粬璇鐨勭増鏈皢鐢卞悗缁浘鐗囩炕璇戦泦鎴愯嚜鍔ㄧ敓鎴愩€?
# ======================================================================

_DETAIL_IMAGES_MAX_BATCH = 20

_DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES = {
    "de": "德国",
    "fr": "法国",
    "es": "西班牙",
    "it": "意大利",
    "ja": "日本",
    "pt": "葡萄牙",
    "nl": "荷兰",
    "sv": "瑞典",
    "fi": "芬兰",
}


def _serialize_detail_image(row: dict) -> dict:
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "lang": row["lang"],
        "sort_order": int(row.get("sort_order") or 0),
        "object_key": row["object_key"],
        "content_type": row.get("content_type"),
        "file_size": row.get("file_size"),
        "width": row.get("width"),
        "height": row.get("height"),
        "origin_type": row.get("origin_type") or "manual",
        "source_detail_image_id": row.get("source_detail_image_id"),
        "image_translate_task_id": row.get("image_translate_task_id"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "thumbnail_url": f"/medias/detail-image/{row['id']}",
    }


def probe_media_info_safe(path: str) -> dict:
    try:
        from pipeline.ffutil import probe_media_info

        return probe_media_info(path) or {}
    except Exception:
        return {}


def _detail_images_archive_product_code(product: dict, pid: int) -> str:
    raw_code = str((product or {}).get("product_code") or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw_code).strip("-") or f"product-{pid}"


def _detail_images_is_gif(row: dict) -> bool:
    content_type = str(row.get("content_type") or "").split(";")[0].strip().lower()
    object_key = str(row.get("object_key") or "").lower()
    return content_type == "image/gif" or object_key.endswith(".gif")


def _detail_images_archive_part(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return re.sub(r'[\\/:*?"<>|]+', "-", text).strip("-") or fallback


def _detail_images_archive_basename(product: dict, pid: int, lang: str) -> str:
    base_code = _detail_images_archive_product_code(product, pid)
    archive_name = f"{base_code}_{lang}_detail-images"
    country_prefix = _DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES.get((lang or "").strip().lower())
    return f"{country_prefix}-{archive_name}" if country_prefix else archive_name


@bp.route("/api/products/<int:pid>/detail-images", methods=["GET"])
@login_required
def api_detail_images_list(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400
    rows = medias.list_detail_images(pid, lang)
    return jsonify({"items": [_serialize_detail_image(r) for r in rows]})


@bp.route("/api/products/<int:pid>/detail-images/download-zip", methods=["GET"])
@login_required
def api_detail_images_download_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    kind = (request.args.get("kind") or "image").strip().lower()
    if kind not in {"image", "gif", "all"}:
        return jsonify({"error": f"涓嶆敮鎸佺殑 kind: {kind}"}), 400

    rows = medias.list_detail_images(pid, lang)
    if not rows:
        abort(404)

    if kind == "gif":
        rows = [r for r in rows if _detail_images_is_gif(r)]
    elif kind == "image":
        rows = [r for r in rows if not _detail_images_is_gif(r)]
    if not rows:
        abort(404)

    base = _detail_images_archive_basename(p or {}, pid, lang)
    archive_base = f"{base}_gif" if kind == "gif" else base
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="detail_images_zip_") as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, row in enumerate(rows, start=1):
                object_key = str(row.get("object_key") or "").strip()
                if not object_key:
                    continue
                suffix = Path(object_key).suffix or ".jpg"
                local_path = Path(tmp_dir) / f"detail_{idx:02d}{suffix}"
                _download_media_object(object_key, str(local_path))
                zf.write(local_path, arcname=f"{archive_base}/{idx:02d}{suffix}")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive_base}.zip",
    )


@bp.route("/api/products/<int:pid>/detail-images/download-localized-zip", methods=["GET"])
@login_required
def api_detail_images_download_localized_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    product_code = _detail_images_archive_product_code(p or {}, pid)
    archive_base = f"小语种-{product_code}"
    groups: list[tuple[str, list[dict]]] = []
    for lang_row in medias.list_languages():
        lang = str(lang_row.get("code") or "").strip().lower()
        if not lang or lang == "en":
            continue
        rows = [
            row for row in medias.list_detail_images(pid, lang)
            if str(row.get("object_key") or "").strip() and not _detail_images_is_gif(row)
        ]
        if not rows:
            continue
        lang_name = _detail_images_archive_part(lang_row.get("name_zh"), lang)
        folder = f"{lang_name}-{product_code}"
        groups.append((folder, rows))

    if not groups:
        abort(404)

    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="localized_detail_images_zip_") as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, rows in groups:
                for idx, row in enumerate(rows, start=1):
                    object_key = str(row.get("object_key") or "").strip()
                    suffix = Path(object_key).suffix or ".jpg"
                    local_path = Path(tmp_dir) / f"{uuid.uuid4().hex}{suffix}"
                    _download_media_object(object_key, str(local_path))
                    zf.write(local_path, arcname=f"{folder}/{idx:02d}{suffix}")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive_base}.zip",
    )


@bp.route("/api/products/<int:pid>/detail-images/from-url", methods=["POST"])
@login_required
def api_detail_images_from_url(pid: int):
    """Start a background detail-image fetch task and return its task id."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400
    clear_existing = bool(body.get("clear_existing"))

    # 瑙ｆ瀽鍟嗗搧閾炬帴
    url = (body.get("url") or "").strip()
    if not url:
        raw_links = p.get("localized_links_json")
        links: dict = {}
        if isinstance(raw_links, dict):
            links = raw_links
        elif isinstance(raw_links, str):
            try:
                parsed = json.loads(raw_links)
                if isinstance(parsed, dict):
                    links = parsed
            except (json.JSONDecodeError, ValueError):
                pass
        url = (links.get(lang) or "").strip()
        if not url:
            code = (p.get("product_code") or "").strip()
            if not code:
                return jsonify({"error": "product_code required before inferring a default link"}), 400
            url = (f"https://newjoyloo.com/products/{code}" if lang == "en"
                   else f"https://newjoyloo.com/{lang}/products/{code}")

    uid = current_user.id

    def _worker(task_id: str, update):
        """Fetch the page, download images, and update task progress."""
        from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError
        update(status="fetching", message=f"fetching page {url}")
        try:
            fetcher = LinkCheckFetcher()
            page = fetcher.fetch_page(url, lang)
        except LocaleLockError as e:
            update(status="failed", error=str(e),
                   message=f"locale lock failed: {e}")
            return
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                update(status="failed",
                       error=f"link returned 404: {url}",
                       message=(
                           f"link returned 404: {url}\n"
                           f"product_code={p.get('product_code')} may not match the storefront handle.\n"
                           "Please fill a real product link and retry."
                       ))
            else:
                update(status="failed",
                       error=f"HTTP {status}",
                       message=f"fetch failed: HTTP {status}")
            return
        except requests.RequestException as e:
            update(status="failed", error=str(e),
                   message=f"fetch failed: {e}")
            return

        images = page.images or []
        if not images:
            update(status="failed",
                   error="no images found",
                   message="no carousel/detail images detected on the page")
            return
        if len(images) > _DETAIL_IMAGES_MAX_BATCH:
            images = images[:_DETAIL_IMAGES_MAX_BATCH]

        if clear_existing:
            try:
                cleared = medias.soft_delete_detail_images_by_lang(pid, lang)
            except Exception as exc:
                update(status="failed", error=str(exc),
                       message=f"failed to clear existing detail images: {exc}")
                return
            update(status="downloading", total=len(images),
                   message=f"cleared {cleared} existing detail images; found {len(images)} images, starting download")
        else:
            update(status="downloading", total=len(images),
                   message=f"found {len(images)} images, starting download")

        created: list[dict] = []
        errors: list[str] = []
        for idx, img in enumerate(images):
            src = img.get("source_url") or ""
            update(progress=idx, current_url=src,
                   message=f"downloading image {idx + 1}/{len(images)}")
            filename = f"from_url_{lang}_{idx:02d}"
            try:
                obj_key, data, err = _download_image_to_local_media(
                    src, pid, filename, user_id=uid,
                )
                if err and not obj_key:
                    errors.append(f"{src}: {err}")
                    continue
                new_id = medias.add_detail_image(
                    pid, lang, obj_key,
                    content_type=None, file_size=len(data) if data else None,
                    origin_type="from_url",
                )
                row = medias.get_detail_image(new_id)
                if row:
                    created.append(_serialize_detail_image(row))
            except Exception as exc:
                errors.append(f"{src}: {exc}")

        update(status="done",
               progress=len(images),
               inserted=created,
               errors=errors,
               current_url="",
               message=f"done: detected {len(images)} images, inserted {len(created)}"
                       + (f", failed {len(errors)}" if errors else ""))

    from appcore import medias_detail_fetch_tasks as mdf
    task_id = mdf.create(user_id=uid, product_id=pid, url=url, lang=lang, worker=_worker)
    return jsonify({"task_id": task_id, "url": url}), 202


@bp.route("/api/products/<int:pid>/detail-images/from-url/status/<task_id>", methods=["GET"])
@login_required
def api_detail_images_from_url_status(pid: int, task_id: str):
    """Return the current status for a detail-image fetch task."""
    from appcore import medias_detail_fetch_tasks as mdf
    t = mdf.get(task_id, user_id=current_user.id)
    if not t or t.get("product_id") != pid:
        return jsonify({"error": "task not found"}), 404
    return jsonify(t)


@bp.route("/api/products/<int:pid>/detail-images/bootstrap", methods=["POST"])
@login_required
def api_detail_images_bootstrap(pid: int):
    """Reserve local upload targets for detail images."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400

    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify({"error": "files required"}), 400
    if len(files) > _DETAIL_IMAGES_MAX_BATCH:
        return jsonify({"error": f"too many files (max {_DETAIL_IMAGES_MAX_BATCH})"}), 400

    uploads = []
    for idx, f in enumerate(files):
        if not isinstance(f, dict):
            return jsonify({"error": f"files[{idx}] must be an object"}), 400
        raw_name = (f.get("filename") or "").strip()
        if not raw_name:
            return jsonify({"error": f"files[{idx}].filename required"}), 400
        filename = os.path.basename(raw_name)
        if not filename:
            return jsonify({"error": f"files[{idx}].filename is invalid"}), 400
        ct = (f.get("content_type") or "").strip().lower()
        if ct not in _ALLOWED_IMAGE_TYPES:
            return jsonify({"error": f"files[{idx}] unsupported image content_type: {ct}"}), 400
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > _MAX_IMAGE_BYTES:
            return jsonify({"error": f"files[{idx}] exceeds 15MB"}), 400

        object_key = object_keys.build_media_object_key(
            current_user.id, pid, f"detail_{lang}_{idx:02d}_{filename}",
        )
        uploads.append({
            "idx": idx,
            "object_key": object_key,
                "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        })

    return jsonify({
        "uploads": uploads,
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/detail-images/complete", methods=["POST"])
@login_required
def api_detail_images_complete(pid: int):
    """Persist detail-image records after browser uploads complete."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400

    images = body.get("images") or []
    if not isinstance(images, list) or not images:
        return jsonify({"error": "images required"}), 400
    if len(images) > _DETAIL_IMAGES_MAX_BATCH:
        return jsonify({"error": f"too many images (max {_DETAIL_IMAGES_MAX_BATCH})"}), 400

    created: list[dict] = []
    for idx, img in enumerate(images):
        if not isinstance(img, dict):
            return jsonify({"error": f"images[{idx}] must be an object"}), 400
        object_key = (img.get("object_key") or "").strip()
        if not object_key:
            return jsonify({"error": f"images[{idx}].object_key required"}), 400
        if not _is_media_available(object_key):
            return jsonify({"error": f"images[{idx}] object missing: {object_key}"}), 400

        def _opt_int(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        new_id = medias.add_detail_image(
            pid, lang, object_key,
            content_type=(img.get("content_type") or "").strip().lower() or None,
            file_size=_opt_int(img.get("file_size") or img.get("size")),
            width=_opt_int(img.get("width")),
            height=_opt_int(img.get("height")),
            origin_type="manual",
        )
        row = medias.get_detail_image(new_id)
        if row:
            created.append(_serialize_detail_image(row))

    return jsonify({"items": created}), 201


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>", methods=["DELETE"])
@login_required
def api_detail_images_delete(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    row = medias.get_detail_image(image_id)
    if not row or int(row["product_id"]) != pid or row.get("deleted_at") is not None:
        abort(404)

    medias.soft_delete_detail_image(image_id)
    try:
        _delete_media_object(row["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>/detail-images/reorder", methods=["POST"])
@login_required
def api_detail_images_reorder(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"error": "ids must be list"}), 400
    try:
        ids_int = [int(x) for x in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400
    updated = medias.reorder_detail_images(pid, lang, ids_int)
    return jsonify({"ok": True, "updated": updated})


@bp.route("/api/products/<int:pid>/detail-images/translate-from-en", methods=["POST"])
@login_required
def api_detail_images_translate_from_en(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "english detail images do not need translate-from-en"}), 400
    mode_raw = (body.get("concurrency_mode") or "sequential").strip().lower()
    if mode_raw not in {"sequential", "parallel"}:
        return jsonify({"error": "concurrency_mode must be sequential or parallel"}), 400

    source_rows = medias.list_detail_images(pid, "en")
    if not source_rows:
        return jsonify({"error": "english detail images are required first"}), 409

    translatable_rows = [row for row in source_rows if not _detail_images_is_gif(row)]
    if not translatable_rows:
        return jsonify({"error": "鑻辫鐗堣鎯呭浘鍏ㄩ儴涓?GIF 鍔ㄥ浘锛屾棤鍙炕璇戠殑闈欐€佸浘"}), 409

    prompt_tpl = (its.get_prompts_for_lang(lang).get("detail") or "").strip()
    if not prompt_tpl:
        return jsonify({"error": "褰撳墠璇鏈厤缃鎯呭浘缈昏瘧 prompt"}), 409
    lang_name = medias.get_language_name(lang)
    task_id = uuid.uuid4().hex
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    items = []
    for idx, row in enumerate(translatable_rows):
        items.append({
            "idx": idx,
            "filename": os.path.basename(row.get("object_key") or "") or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        })
    medias_context = {
        "entry": "medias_edit_detail",
        "product_id": pid,
        "source_lang": "en",
        "target_lang": lang,
        "source_bucket": "media",
        "source_detail_image_ids": [row["id"] for row in translatable_rows],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
        "applied_at": "",
        "applied_detail_image_ids": [],
        "last_apply_error": "",
    }
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=current_user.id,
        preset="detail",
        target_language=lang,
        target_language_name=lang_name,
        model_id=(body.get("model_id") or "").strip() or _default_image_translate_model_id(),
        prompt=prompt_tpl.replace("{target_language_name}", lang_name),
        items=items,
        product_name=(p.get("name") or "").strip(),
        project_name=image_translate_routes._compose_project_name((p.get("name") or "").strip(), "detail", lang_name),
        medias_context=medias_context,
        concurrency_mode=mode_raw,
    )
    _start_image_translate_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "detail_url": f"/image-translate/{task_id}"}), 201


@bp.route("/api/products/<int:pid>/detail-image-translate-tasks", methods=["GET"])
@login_required
def api_detail_image_translate_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    rows = db_query(
        "SELECT id, created_at, state_json "
        "FROM projects "
        "WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 50",
        (current_user.id,),
    )
    items = []
    for row in rows:
        try:
            state = json.loads(row.get("state_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        ctx = state.get("medias_context") or {}
        if state.get("preset") != "detail":
            continue
        if ctx.get("entry") != "medias_edit_detail":
            continue
        if int(ctx.get("product_id") or 0) != pid:
            continue
        if (ctx.get("target_lang") or "") != lang:
            continue
        progress = dict(state.get("progress") or {})
        items.append({
            "task_id": row["id"],
            "status": state.get("status") or "queued",
            "apply_status": ctx.get("apply_status") or "",
            "applied_detail_image_ids": list(ctx.get("applied_detail_image_ids") or []),
            "last_apply_error": ctx.get("last_apply_error") or "",
            "progress": progress,
            "detail_url": f"/image-translate/{row['id']}",
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return jsonify({"items": items})


@bp.route(
    "/api/products/<int:pid>/detail-images/<lang>/apply-translate-task/<task_id>",
    methods=["POST"],
)
@login_required
def api_detail_images_apply_translate_task(pid: int, lang: str, task_id: str):
    """Manually apply successful outputs from a finished image-translate task.

    Auto-apply skips the whole batch when any row fails. This endpoint lets the
    operator keep successful rows and ignore failed ones.
    """
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (lang or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "english detail images do not need manual apply"}), 400

    task = store.get(task_id)
    if not task or task.get("type") != "image_translate":
        abort(404)
    task_user_id = task.get("_user_id")
    if task_user_id is not None and int(task_user_id) != int(current_user.id):
        abort(404)

    ctx = task.get("medias_context") or {}
    if int(ctx.get("product_id") or 0) != pid:
        return jsonify({"error": "task does not belong to this product"}), 400
    if (ctx.get("target_lang") or "").strip().lower() != lang:
        return jsonify({"error": "task target language does not match current language"}), 400

    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "task is still running"}), 409
    if (task.get("status") or "") not in {"done", "error"}:
        return jsonify({"error": "task has not finished yet"}), 409

    try:
        result = image_translate_runtime.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=int(current_user.id),
        )
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 409

    return jsonify({
        "ok": True,
        "applied": len(result["applied_ids"]),
        "skipped_failed": len(result["skipped_failed_indices"]),
        "apply_status": result["apply_status"],
        "applied_detail_image_ids": result["applied_ids"],
    })


@bp.route("/detail-image/<int:image_id>", methods=["GET"])
@login_required
def detail_image_proxy(image_id: int):
    """Serve or redirect to the stored detail image asset."""
    row = medias.get_detail_image(image_id)
    if not row or row.get("deleted_at") is not None:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(row["object_key"])


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
    # 实际 object_key 形如 "<uid>/medias/<pid>/<filename>" —— 要求至少 3 段且
    # 第二段是 "medias"，拦住误读无关本地文件
    parts = key.split("/")
    if len(parts) < 3 or parts[1] != "medias":
        abort(404)
    return _send_media_object(key)


# ---------- 明空选品 ----------

def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


@bp.route("/mk-selection")
@login_required
def mk_selection_page():
    if not _is_admin():
        abort(403)
    return render_template("mk_selection.html")


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
