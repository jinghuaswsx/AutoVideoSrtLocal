from __future__ import annotations

import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
import uuid
import requests
from flask import Blueprint, render_template, request, jsonify, abort, redirect, send_file
from flask_login import login_required, current_user

from appcore import medias, task_state, tos_clients
from appcore import image_translate_settings as its
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.gemini_image import IMAGE_MODELS
from config import OUTPUT_DIR, TOS_MEDIA_BUCKET, TOS_REGION, TOS_PUBLIC_ENDPOINT, TOS_SIGNED_URL_EXPIRES
from pipeline.ffutil import extract_thumbnail, get_media_duration
from web import store
from web.routes import image_translate as image_translate_routes
from web.services import link_check_runner

import re

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB


def _parse_lang(body: dict, default: str = "en") -> tuple[str | None, str | None]:
    """返回 (lang, error)。lang 校验不通过返回 (None, error msg)。"""
    lang = (body.get("lang") or default).strip().lower()
    if not medias.is_valid_language(lang):
        return None, f"不支持的语种: {lang}"
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
    """从 URL 抓图并上传到 TOS media bucket。返回 (object_key, content, ext) 或失败时 (None, None, error_msg)。"""
    if not url:
        return None, None, "url required"
    upload_user_id = _resolve_upload_user_id(user_id)
    if upload_user_id is None:
        return None, None, "missing upload user"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "仅支持 http/https 链接"
    try:
        resp = requests.get(url, timeout=20, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 AutoVideoSrt-Importer"})
        resp.raise_for_status()
        ct = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            return None, None, f"非图片类型: {ct}"
        data = b""
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            data += chunk
            if len(data) > _MAX_IMAGE_BYTES:
                return None, None, "图片过大（>15MB）"
    except requests.RequestException as e:
        return None, None, f"下载失败: {e}"

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ct, ".jpg")
    name_from_url = os.path.basename(parsed.path or "") or "from_url"
    filename = f"{prefix}_{name_from_url}"
    if not filename.endswith(ext):
        filename += ext
    object_key = tos_clients.build_media_object_key(upload_user_id, pid, filename)
    tos_clients.upload_media_object(object_key, data, content_type=ct)
    return object_key, data, ext

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


def _validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "产品 ID 必填"
    if not _SLUG_RE.match(code):
        return False, "产品 ID 只能使用小写字母、数字和连字符，长度 3-64，且首尾不能是连字符"
    return True, None


bp = Blueprint("medias", __name__, url_prefix="/medias")

THUMB_DIR = Path(OUTPUT_DIR) / "media_thumbs"

def _can_access_product(product: dict | None) -> bool:
    # 共享媒体库：只要产品存在就允许访问。
    return product is not None


def _start_image_translate_runner(task_id: str, user_id: int) -> bool:
    return image_translate_routes.start_image_translate_runner(task_id, user_id)


def _default_image_translate_model_id() -> str:
    try:
        from appcore.api_keys import resolve_extra

        extra = resolve_extra(current_user.id, "image_translate") or {}
        preferred = (extra.get("default_model_id") or "").strip()
        if preferred:
            return preferred
    except Exception:
        pass
    if IMAGE_MODELS:
        return IMAGE_MODELS[0][0]
    return "gemini-3-pro-image-preview"


def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None,
                       covers: dict[str, str] | None = None) -> dict:
    if covers is None:
        covers = medias.get_product_covers(p["id"])
    has_en_cover = "en" in covers
    cover_url = f"/medias/cover/{p['id']}?lang=en" if has_en_cover else (
        f"/medias/thumb/{cover_item_id}" if cover_item_id else None
    )
    # localized_links_json 可能是 str / dict / None
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
        tos_clients.download_media_file(cover_key, cover_local)
        references.append({
            "id": f"cover-{lang}",
            "filename": f"cover_{lang}{cover_suffix}",
            "local_path": str(cover_local),
        })

    for idx, row in enumerate(medias.list_detail_images(pid, lang), start=1):
        object_key = row.get("object_key") or ""
        detail_suffix = Path(object_key).suffix or ".jpg"
        detail_local = ref_dir / f"detail_{idx:03d}{detail_suffix}"
        tos_clients.download_media_file(object_key, detail_local)
        references.append({
            "id": f"detail-{row['id']}",
            "filename": f"detail_{idx:03d}{detail_suffix}",
            "local_path": str(detail_local),
        })

    return references


# ---------- 页面 ----------

@bp.route("/")
@login_required
def index():
    return render_template(
        "medias_list.html",
        tos_ready=tos_clients.is_media_bucket_configured(),
    )


# ---------- 产品 API ----------

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
            return jsonify({"error": "产品 ID 已被占用"}), 409
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

    name = (body.get("name") or "").strip() or p["name"]
    product_code = (body.get("product_code") or "").strip().lower()
    ok, err = _validate_product_code(product_code)
    if not ok:
        return jsonify({"error": err}), 400
    exist = medias.get_product_by_code(product_code)
    if exist and exist["id"] != pid:
        return jsonify({"error": "产品 ID 已被占用"}), 409

    if not medias.has_english_cover(pid):
        return jsonify({"error": "必须先上传英文（EN）产品主图才能保存"}), 400

    # 允许先创建/保存产品基础信息，视频素材可在编辑弹窗后续补充，不做硬校验

    update_fields = {"name": name, "product_code": product_code}

    # 可选：localized_links — 每语言覆盖商品链接（dict {lang: url}）
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
        # 过滤掉非法语种 & 去重 & 排除 en
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
    medias.update_product(pid, **update_fields)

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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

    link_url = (body.get("link_url") or "").strip()
    if not link_url.startswith(("http://", "https://")):
        return jsonify({"error": "请先填写有效的商品链接"}), 400

    language = medias.get_language(lang)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target_language 非法"}), 400

    task_id = str(uuid.uuid4())
    task_dir = Path(OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    references = _collect_link_check_reference_images(pid, lang, task_dir)
    if not references:
        return jsonify({"error": "当前语种缺少参考图，至少需要主图或详情图之一"}), 400

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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

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


# ---------- 素材上传 ----------

@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    filename = os.path.basename((body.get("filename") or "").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = tos_clients.build_media_object_key(current_user.id, pid, filename)
    return jsonify({
        "object_key": object_key,
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
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
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    cover_object_key = (body.get("cover_object_key") or "").strip() or None
    if cover_object_key and not tos_clients.media_object_exists(cover_object_key):
        cover_object_key = None

    item_id = medias.create_item(
        pid, current_user.id, filename, object_key,
        file_size=file_size or None,
        cover_object_key=cover_object_key,
        lang=lang,
    )

    # 下载用户封面到本地缓存供代理
    if cover_object_key:
        try:
            product_dir = THUMB_DIR / str(pid)
            product_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(cover_object_key).suffix or ".jpg"
            tos_clients.download_media_file(
                cover_object_key, str(product_dir / f"item_cover_{item_id}{ext}"),
            )
        except Exception:
            pass

    # 抽缩略图（失败不阻断入库）
    try:
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(exist_ok=True)
        tmp_video = product_dir / f"tmp_{item_id}_{Path(filename).name}"
        tos_clients.download_media_file(object_key, str(tmp_video))
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
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
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
            tos_clients.delete_media_object(old)
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
    """为尚未创建的 item 预上传一张 URL 图片，返回 object_key。"""
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
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
            tos_clients.delete_media_object(old)
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
    """为产品下新建素材或已有素材的封面图申请 TOS 签名直传。"""
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
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
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })


@bp.route("/api/items/<int:item_id>/cover/set", methods=["POST"])
@login_required
def api_item_cover_set(item_id: int):
    """把已上传到 TOS 的 object_key 绑定到某个 item 作为封面。"""
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
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    old = it.get("cover_object_key")
    if old and old != object_key:
        try:
            tos_clients.delete_media_object(old)
        except Exception:
            pass

    medias.update_item_cover(item_id, object_key)

    try:
        product_dir = THUMB_DIR / str(it["product_id"])
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"item_cover_{item_id}{ext}"
        tos_clients.download_media_file(object_key, str(local))
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
        tos_clients.download_media_file(it["cover_object_key"], str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
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
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
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
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            tos_clients.delete_media_object(old)
        except Exception:
            pass

    medias.set_product_cover(pid, lang, object_key)

    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{lang}{ext}"
        tos_clients.download_media_file(object_key, str(local))
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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "英文主图不能删除"}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old:
        try:
            tos_clients.delete_media_object(old)
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
        tos_clients.delete_media_object(it["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


# ---------- 缩略图代理 ----------

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
        tos_clients.download_media_file(object_key, str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


# ---------- 签名下载（播放） ----------

@bp.route("/api/items/<int:item_id>/play_url")
@login_required
def api_play_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    url = tos_clients.generate_signed_media_download_url(it["object_key"])
    return jsonify({"url": url})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_list_languages():
    return jsonify({"items": medias.list_languages()})


# ======================================================================
# 商品详情图（product detail images）
# ----------------------------------------------------------------------
# 第一轮只在英语语种暴露入口，其他语种的版本将由后续图片翻译集成自动生成。
# ======================================================================

_DETAIL_IMAGES_MAX_BATCH = 20


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


def _detail_images_archive_basename(product: dict, pid: int, lang: str) -> str:
    raw_code = str((product or {}).get("product_code") or "").strip()
    base_code = re.sub(r"[^A-Za-z0-9_-]+", "-", raw_code).strip("-") or f"product-{pid}"
    return f"{base_code}_{lang}_detail-images"


@bp.route("/api/products/<int:pid>/detail-images", methods=["GET"])
@login_required
def api_detail_images_list(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语种: {lang}"}), 400
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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

    rows = medias.list_detail_images(pid, lang)
    if not rows:
        abort(404)

    archive_base = _detail_images_archive_basename(p or {}, pid, lang)
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="detail_images_zip_") as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, row in enumerate(rows, start=1):
                object_key = str(row.get("object_key") or "").strip()
                if not object_key:
                    continue
                suffix = Path(object_key).suffix or ".jpg"
                local_path = Path(tmp_dir) / f"detail_{idx:02d}{suffix}"
                tos_clients.download_media_file(object_key, str(local_path))
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
    """启动后台抓取任务，立即返回 task_id。前端用 /status 轮询进度。"""
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

    # 解析商品链接
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
                return jsonify({"error": "产品未设置 product_code，无法生成默认链接"}), 400
            url = (f"https://newjoyloo.com/products/{code}" if lang == "en"
                   else f"https://newjoyloo.com/{lang}/products/{code}")

    uid = current_user.id

    def _worker(task_id: str, update):
        """在后台线程跑：fetch → 逐张下载 → update 进度。"""
        from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError
        update(status="fetching", message=f"正在抓取页面 {url}")
        try:
            fetcher = LinkCheckFetcher()
            page = fetcher.fetch_page(url, lang)
        except LocaleLockError as e:
            update(status="failed", error=str(e),
                   message=f"语种锁失败：{e}")
            return
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                update(status="failed",
                       error=f"链接 404：{url} 不存在",
                       message=(
                           f"链接 404：{url} 不存在。\n"
                           f"产品 ID（{p.get('product_code')}）与 Shopify handle 可能不一致。\n"
                           "请到「产品链接」字段填入真实 URL 后重试。"
                       ))
            else:
                update(status="failed",
                       error=f"HTTP {status}",
                       message=f"抓取失败：HTTP {status}")
            return
        except requests.RequestException as e:
            update(status="failed", error=str(e),
                   message=f"抓取失败：{e}")
            return

        images = page.images or []
        if not images:
            update(status="failed",
                   error="no images found",
                   message="页面上未识别到任何轮播/详情图")
            return
        if len(images) > _DETAIL_IMAGES_MAX_BATCH:
            images = images[:_DETAIL_IMAGES_MAX_BATCH]

        update(status="downloading", total=len(images),
               message=f"共识别到 {len(images)} 张，开始下载...")

        created: list[dict] = []
        errors: list[str] = []
        for idx, img in enumerate(images):
            src = img.get("source_url") or ""
            update(progress=idx, current_url=src,
                   message=f"下载中 {idx + 1}/{len(images)}")
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
               message=f"完成：识别 {len(images)} 张，入库 {len(created)} 张" +
                       (f"，失败 {len(errors)} 张" if errors else ""))

    from appcore import medias_detail_fetch_tasks as mdf
    task_id = mdf.create(user_id=uid, product_id=pid, url=url, lang=lang, worker=_worker)
    return jsonify({"task_id": task_id, "url": url}), 202


@bp.route("/api/products/<int:pid>/detail-images/from-url/status/<task_id>", methods=["GET"])
@login_required
def api_detail_images_from_url_status(pid: int, task_id: str):
    """轮询抓取任务进度。"""
    from appcore import medias_detail_fetch_tasks as mdf
    t = mdf.get(task_id, user_id=current_user.id)
    if not t or t.get("product_id") != pid:
        return jsonify({"error": "task not found"}), 404
    return jsonify(t)


@bp.route("/api/products/<int:pid>/detail-images/bootstrap", methods=["POST"])
@login_required
def api_detail_images_bootstrap(pid: int):
    """批量申请 TOS 签名直传 URL。"""
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
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
        return jsonify({"error": f"单次最多上传 {_DETAIL_IMAGES_MAX_BATCH} 张"}), 400

    uploads = []
    for idx, f in enumerate(files):
        if not isinstance(f, dict):
            return jsonify({"error": f"files[{idx}] 格式错误"}), 400
        raw_name = (f.get("filename") or "").strip()
        if not raw_name:
            return jsonify({"error": f"files[{idx}].filename required"}), 400
        filename = os.path.basename(raw_name)
        if not filename:
            return jsonify({"error": f"files[{idx}].filename 非法"}), 400
        ct = (f.get("content_type") or "").strip().lower()
        if ct not in _ALLOWED_IMAGE_TYPES:
            return jsonify({"error": f"files[{idx}] 不支持的图片格式: {ct}"}), 400
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > _MAX_IMAGE_BYTES:
            return jsonify({"error": f"files[{idx}] 超过 15MB"}), 400

        object_key = tos_clients.build_media_object_key(
            current_user.id, pid, f"detail_{lang}_{idx:02d}_{filename}",
        )
        uploads.append({
            "idx": idx,
            "object_key": object_key,
            "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
        })

    return jsonify({
        "uploads": uploads,
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })


@bp.route("/api/products/<int:pid>/detail-images/complete", methods=["POST"])
@login_required
def api_detail_images_complete(pid: int):
    """浏览器直传完成后通知后端落库。"""
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
        return jsonify({"error": f"单次最多 {_DETAIL_IMAGES_MAX_BATCH} 张"}), 400

    created: list[dict] = []
    for idx, img in enumerate(images):
        if not isinstance(img, dict):
            return jsonify({"error": f"images[{idx}] 格式错误"}), 400
        object_key = (img.get("object_key") or "").strip()
        if not object_key:
            return jsonify({"error": f"images[{idx}].object_key required"}), 400
        if not tos_clients.media_object_exists(object_key):
            return jsonify({"error": f"images[{idx}] 对象不存在: {object_key}"}), 400

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
        tos_clients.delete_media_object(row["object_key"])
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
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503

    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "英文详情图不需要从英语版翻译"}), 400

    source_rows = medias.list_detail_images(pid, "en")
    if not source_rows:
        return jsonify({"error": "请先准备英语版商品详情图"}), 409

    prompt_tpl = (its.get_prompts_for_lang(lang).get("detail") or "").strip()
    if not prompt_tpl:
        return jsonify({"error": "当前语种未配置详情图翻译 prompt"}), 409
    lang_name = medias.get_language_name(lang)
    task_id = uuid.uuid4().hex
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    items = []
    for idx, row in enumerate(source_rows):
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
        "source_detail_image_ids": [row["id"] for row in source_rows],
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
        return jsonify({"error": f"不支持的语种: {lang}"}), 400

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


@bp.route("/detail-image/<int:image_id>", methods=["GET"])
@login_required
def detail_image_proxy(image_id: int):
    """返回详情图的签名下载 URL（302 重定向）。"""
    row = medias.get_detail_image(image_id)
    if not row or row.get("deleted_at") is not None:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    url = tos_clients.generate_signed_media_download_url(row["object_key"])
    return redirect(url, code=302)
