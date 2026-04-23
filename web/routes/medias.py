from __future__ import annotations

import io
import json
import mimetypes
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
import uuid
import requests
from flask import Blueprint, render_template, request, jsonify, abort, redirect, send_file, url_for
from flask_login import login_required, current_user

from appcore import local_media_storage, medias, task_state, tos_clients
from appcore import image_translate_runtime
from appcore import image_translate_settings as its
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.gemini_image import coerce_image_model
from appcore.material_filename_rules import validate_material_filename, resolve_material_filename_lang
from config import OUTPUT_DIR, TOS_MEDIA_BUCKET, TOS_REGION, TOS_PUBLIC_ENDPOINT, TOS_SIGNED_URL_EXPIRES
from pipeline.ffutil import extract_thumbnail, get_media_duration
from web import store
from web.background import start_background_task
from web.routes import image_translate as image_translate_routes
from web.services import image_translate_runner, link_check_runner

import re

import pymysql.err

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB
_ALLOWED_RAW_VIDEO_TYPES = ("video/mp4", "video/quicktime")
_MAX_RAW_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB


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


def _download_image_to_tos(
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
    object_key = tos_clients.build_media_object_key(upload_user_id, pid, filename)
    local_media_storage.write_bytes(object_key, data)
    return object_key, data, ext

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")


def _validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "浜у搧 ID 蹇呭～"
    if not _SLUG_RE.match(code):
        return False, "浜у搧 ID 鍙兘浣跨敤灏忓啓瀛楁瘝銆佹暟瀛楀拰杩炲瓧绗︼紝闀垮害 3-128锛屼笖棣栧熬涓嶈兘鏄繛瀛楃"
    return True, None


def _language_name_map() -> dict[str, str]:
    return {
        str(row.get("code") or "").strip().lower(): str(row.get("name_zh") or "").strip()
        for row in medias.list_languages()
        if str(row.get("code") or "").strip()
    }


def _validate_material_filename_for_product(filename: str, product: dict, lang: str):
    result = validate_material_filename(
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
    if local_media_storage.exists(object_key):
        return True
    try:
        local_path = local_media_storage.local_path_for(object_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tos_clients.download_media_file(object_key, str(local_path))
        return local_path.exists()
    except Exception:
        return False


def _download_media_object(object_key: str, destination: str | os.PathLike[str]) -> str:
    if local_media_storage.exists(object_key):
        return local_media_storage.download_to(object_key, destination)
    try:
        local_path = local_media_storage.local_path_for(object_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tos_clients.download_media_file(object_key, str(local_path))
        return local_media_storage.download_to(object_key, destination)
    except Exception:
        return tos_clients.download_media_file(object_key, destination)


def _delete_media_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass
    try:
        tos_clients.delete_media_object(key)
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


def _start_image_translate_runner(task_id: str, user_id: int) -> bool:
    return image_translate_routes.start_image_translate_runner(task_id, user_id)


def _default_image_translate_model_id() -> str:
    channel = "aistudio"
    try:
        channel = its.get_channel()
    except Exception:
        pass
    preferred = ""
    try:
        from appcore.api_keys import resolve_extra

        extra = resolve_extra(current_user.id, "image_translate") or {}
        preferred = (extra.get("default_model_id") or "").strip()
    except Exception:
        pass
    return coerce_image_model(preferred, channel=channel)


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


def _serialize_item(it: dict) -> dict:
    has_user_cover = bool(it.get("cover_object_key"))
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
    return jsonify({
        "product": _serialize_product(p, None, covers=covers),
        "covers": covers,
        "copywritings": medias.list_copywritings(pid),
        "items": [_serialize_item(i) for i in medias.list_items(pid)],
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
        return jsonify({"error": "mk_id_invalid", "message": str(e)}), 400
    except pymysql.err.IntegrityError as e:
        code = e.args[0] if e.args else None
        if code == 1062 and "uk_media_products_mk_id" in str(e):
            return jsonify({
                "error": "mk_id_conflict",
                "message": "鏄庣┖ ID 宸茶鍏朵粬浜у搧鍗犵敤",
            }), 409
        raise

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
        return jsonify({"error": "current language is missing reference images"}), 400

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

    display_name = (request.form.get("display_name") or "").strip() or None

    uid = _resolve_upload_user_id()
    if uid is None:
        return jsonify({"error": "missing upload user"}), 400

    video_key = tos_clients.build_media_raw_source_key(
        uid, pid, kind="video", filename=video.filename or "video.mp4",
    )
    cover_key = tos_clients.build_media_raw_source_key(
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
    from appcore.bulk_translate_projection import list_product_tasks

    return jsonify({"items": list_product_tasks(current_user.id, pid)})


@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = os.path.basename((body.get("filename") or "").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = tos_clients.build_media_object_key(current_user.id, pid, filename)
    effective_lang = resolve_material_filename_lang(filename, lang, _language_name_map())
    return jsonify({
        "object_key": object_key,
        "effective_lang": effective_lang,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "bucket": TOS_MEDIA_BUCKET,
        "region": TOS_REGION,
        "endpoint": TOS_PUBLIC_ENDPOINT,
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })


@bp.route("/api/products/<int:pid>/items/complete", methods=["POST"])
@login_required
def api_item_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    filename = (body.get("filename") or "").strip()
    file_size = int(body.get("file_size") or 0)
    if not object_key or not filename:
        return jsonify({"error": "object_key and filename required"}), 400
    prefix_err = _check_filename_prefix(filename, p)
    if prefix_err:
        return jsonify({"error": "filename_invalid", "message": prefix_err}), 400
    lang = resolve_material_filename_lang(filename, lang, _language_name_map())
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
    object_key, data, err_or_ext = _download_image_to_tos(
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
    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}", "object_key": object_key})


@bp.route("/api/products/<int:pid>/item-cover/from-url", methods=["POST"])
@login_required
def api_item_cover_from_url(pid: int):
    """Fetch an item cover from URL before the item record is created."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, _data, err_or_ext = _download_image_to_tos(
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
    object_key, data, err_or_ext = _download_image_to_tos(
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
    object_key = tos_clients.build_media_object_key(
        current_user.id, pid, f"item_cover_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "expires_in": TOS_SIGNED_URL_EXPIRES,
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
    object_key = tos_clients.build_media_object_key(
        current_user.id, pid, f"cover_{lang}_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "expires_in": TOS_SIGNED_URL_EXPIRES,
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


def _detail_images_archive_basename(product: dict, pid: int, lang: str) -> str:
    raw_code = str((product or {}).get("product_code") or "").strip()
    base_code = re.sub(r"[^A-Za-z0-9_-]+", "-", raw_code).strip("-") or f"product-{pid}"
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

    def _is_gif(row: dict) -> bool:
        return str(row.get("object_key") or "").lower().endswith(".gif")

    if kind == "gif":
        rows = [r for r in rows if _is_gif(r)]
    elif kind == "image":
        rows = [r for r in rows if not _is_gif(r)]
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
                obj_key, data, err = _download_image_to_tos(
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

        object_key = tos_clients.build_media_object_key(
            current_user.id, pid, f"detail_{lang}_{idx:02d}_{filename}",
        )
        uploads.append({
            "idx": idx,
            "object_key": object_key,
                "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        })

    return jsonify({
        "uploads": uploads,
        "expires_in": TOS_SIGNED_URL_EXPIRES,
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

    # Skip GIF files in translate-from-en. Static images can still be translated
    # even when the source set contains GIFs.
    def _is_gif_row(row: dict) -> bool:
        return (
            (row.get("object_key") or "").lower().endswith(".gif")
            or (row.get("content_type") or "").lower() == "image/gif"
        )

    translatable_rows = [row for row in source_rows if not _is_gif_row(row)]
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
# 安全模型：和 TOS 签名 URL 一致 —— 知 object_key 者即可访问。
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

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        where += " AND (dr.product_name LIKE %s OR dr.mk_product_name LIKE %s)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])

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
            dr.mk_product_id, dr.mk_product_name,
            dr.mk_total_spends, dr.mk_video_count, dr.mk_total_ads,
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY dr.mk_total_spends DESC, dr.rank_position ASC
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
