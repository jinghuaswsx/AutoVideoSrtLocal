from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
import requests
from flask import Blueprint, render_template, request, jsonify, abort, send_file
from flask_login import login_required, current_user

from appcore import medias, tos_clients
from appcore.db import execute as db_execute
from config import OUTPUT_DIR, TOS_MEDIA_BUCKET, TOS_REGION, TOS_PUBLIC_ENDPOINT, TOS_SIGNED_URL_EXPIRES
from pipeline.ffutil import extract_thumbnail, get_media_duration

import re

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB


def _parse_lang(body: dict, default: str = "en") -> tuple[str | None, str | None]:
    """返回 (lang, error)。lang 校验不通过返回 (None, error msg)。"""
    lang = (body.get("lang") or default).strip().lower()
    if not medias.is_valid_language(lang):
        return None, f"不支持的语种: {lang}"
    return lang, None


def _download_image_to_tos(url: str, pid: int, prefix: str) -> tuple[str, bytes, str] | tuple[None, None, str]:
    """从 URL 抓图并上传到 TOS media bucket。返回 (object_key, content, ext) 或失败时 (None, None, error_msg)。"""
    if not url:
        return None, None, "url required"
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
    object_key = tos_clients.build_media_object_key(current_user.id, pid, filename)
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


def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


def _can_access_product(product: dict | None, write: bool = False) -> bool:
    if not product:
        return False
    if product["user_id"] == current_user.id:
        return True
    if _is_admin() and not write:
        return True
    return False


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
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "has_en_cover": has_en_cover,
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "items_filenames": items_filenames or [],
        "cover_thumbnail_url": cover_url,
        "lang_coverage": lang_coverage or {},
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


# ---------- 页面 ----------

@bp.route("/")
@login_required
def index():
    return render_template(
        "medias_list.html",
        tos_ready=tos_clients.is_media_bucket_configured(),
        is_admin=_is_admin(),
    )


# ---------- 产品 API ----------

@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    scope_all = request.args.get("scope") == "all" and _is_admin()
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    user_id = None if scope_all else current_user.id
    rows, total = medias.list_products(user_id, keyword=keyword, archived=archived,
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
    if not _can_access_product(p, write=True):
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
    medias.update_product(pid, **update_fields)

    if isinstance(body.get("copywritings"), dict):
        for lang_code, lang_items in body["copywritings"].items():
            if not medias.is_valid_language(lang_code):
                continue
            if isinstance(lang_items, list):
                medias.replace_copywritings(pid, lang_items, lang=lang_code)
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key, data, err_or_ext = _download_image_to_tos(
        (body.get("url") or "").strip(), pid, f"cover_{lang}",
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
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, _data, err_or_ext = _download_image_to_tos(
        (body.get("url") or "").strip(), pid, "item_cover",
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
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, data, err_or_ext = _download_image_to_tos(
        (body.get("url") or "").strip(), it["product_id"], "item_cover",
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
    if not _can_access_product(p, write=True):
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
