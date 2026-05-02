"""产品详情图（detail images）路由。

由 ``web.routes.medias`` package 在 PR 2.14 抽出；行为不变。
"""
from __future__ import annotations

import json
import os
import uuid

import requests
from flask import abort, jsonify, request, send_file
from flask_login import current_user, login_required

from appcore import (
    image_translate_runtime,
    image_translate_settings as its,
    medias,
    object_keys,
    task_state,
)
from config import OUTPUT_DIR
from web import store
from web.routes import image_translate as image_translate_routes
from web.services import image_translate_runner
from web.services.media_detail_archives import (
    DetailImagesZipGroup,
    build_detail_images_archive,
)

from . import bp
from ._helpers import (
    _ALLOWED_IMAGE_TYPES,
    _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES,
    _DETAIL_IMAGE_KIND_LABELS,
    _DETAIL_IMAGE_LIMITS,
    _MAX_IMAGE_BYTES,
    _default_image_translate_model_id,
    _detail_image_empty_counts,
    _detail_image_existing_counts,
    _detail_image_kind_from_download_ext,
    _detail_image_limit_error,
    _detail_images_archive_basename,
    _detail_images_archive_part,
    _detail_images_archive_product_code,
    _detail_images_is_gif,
    _ensure_product_listed,
    _parse_lang,
)
from ._serializers import _serialize_detail_image


def _routes():
    """Return the package facade so monkeypatch on routes._xxx transmits."""
    from web.routes import medias as routes
    return routes


def _can_access_product(product):
    return _routes()._can_access_product(product)


def _is_media_available(object_key):
    return _routes()._is_media_available(object_key)


def _send_media_object(object_key):
    return _routes()._send_media_object(object_key)


def _download_media_object(object_key, destination):
    return _routes()._download_media_object(object_key, destination)


def _delete_media_object(object_key):
    return _routes()._delete_media_object(object_key)


def _reserve_local_media_upload(object_key):
    return _routes()._reserve_local_media_upload(object_key)


def _download_image_to_local_media(url, pid, prefix, *, user_id=None):
    return _routes()._download_image_to_local_media(url, pid, prefix, user_id=user_id)


def _start_image_translate_runner(task_id, user_id):
    return _routes()._start_image_translate_runner(task_id, user_id)


def db_query(*args, **kwargs):
    return _routes().db_query(*args, **kwargs)


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

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action="detail_images_zip_download",
        detail={
            "lang": lang,
            "kind": kind,
            "file_count": len(rows),
            "object_keys": [str(row.get("object_key") or "").strip() for row in rows],
        },
    )

    base = _detail_images_archive_basename(p or {}, pid, lang)
    archive_base = f"{base}_gif" if kind == "gif" else base
    archive = build_detail_images_archive(
        archive_base=archive_base,
        groups=[DetailImagesZipGroup(folder=archive_base, rows=rows)],
        download_media_object=_download_media_object,
    )
    return send_file(
        archive.buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive.archive_base}.zip",
    )


@bp.route("/api/products/<int:pid>/detail-images/download-localized-zip", methods=["GET"])
@login_required
def api_detail_images_download_localized_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    product_code = _detail_images_archive_product_code(p or {}, pid)
    archive_base = f"小语种-{product_code}"
    groups: list[tuple[str, str, list[dict]]] = []
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
        groups.append((lang, folder, rows))

    if not groups:
        abort(404)

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action="localized_detail_images_zip_download",
        detail={
            "languages": [lang for lang, _folder, _rows in groups],
            "file_count": sum(len(rows) for _lang, _folder, rows in groups),
            "object_keys": [
                str(row.get("object_key") or "").strip()
                for _lang, _folder, rows in groups
                for row in rows
            ],
        },
    )

    archive = build_detail_images_archive(
        archive_base=archive_base,
        groups=[
            DetailImagesZipGroup(folder=folder, rows=rows)
            for _lang, folder, rows in groups
        ],
        download_media_object=_download_media_object,
        temp_prefix="localized_detail_images_zip_",
    )
    return send_file(
        archive.buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive.archive_base}.zip",
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
        if len(images) > _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES:
            images = images[:_DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES]

        if clear_existing:
            try:
                cleared = medias.soft_delete_detail_images_by_lang(pid, lang)
            except Exception as exc:
                update(status="failed", error=str(exc),
                       message=f"failed to clear existing detail images: {exc}")
                return
            limit_counts = _detail_image_empty_counts()
            update(status="downloading", total=len(images),
                   message=f"cleared {cleared} existing detail images; found {len(images)} images, starting download")
        else:
            limit_counts = _detail_image_existing_counts(pid, lang)
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
                obj_key, data, ext = _download_image_to_local_media(
                    src, pid, filename, user_id=uid,
                )
                if ext and not obj_key:
                    errors.append(f"{src}: {ext}")
                    continue
                kind = _detail_image_kind_from_download_ext(ext)
                if limit_counts[kind] >= _DETAIL_IMAGE_LIMITS[kind]:
                    errors.append(
                        f"{src}: skipped, {_DETAIL_IMAGE_KIND_LABELS[kind]} limit reached "
                        f"(max {_DETAIL_IMAGE_LIMITS[kind]})"
                    )
                    continue
                new_id = medias.add_detail_image(
                    pid, lang, obj_key,
                    content_type=None, file_size=len(data) if data else None,
                    origin_type="from_url",
                )
                limit_counts[kind] += 1
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

    validated_files: list[dict] = []
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

        validated_files.append({
            "filename": filename,
            "content_type": ct,
            "size": size,
        })

    limit_error = _detail_image_limit_error(pid, lang, validated_files)
    if limit_error:
        return jsonify({"error": limit_error}), 400

    uploads = []
    for idx, f in enumerate(validated_files):
        object_key = object_keys.build_media_object_key(
            current_user.id, pid, f"detail_{lang}_{idx:02d}_{f['filename']}",
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

    validated_images: list[dict] = []
    for idx, img in enumerate(images):
        if not isinstance(img, dict):
            return jsonify({"error": f"images[{idx}] must be an object"}), 400
        object_key = (img.get("object_key") or "").strip()
        if not object_key:
            return jsonify({"error": f"images[{idx}].object_key required"}), 400
        if not _is_media_available(object_key):
            return jsonify({"error": f"images[{idx}] object missing: {object_key}"}), 400
        normalized = dict(img)
        normalized["object_key"] = object_key
        normalized["content_type"] = (img.get("content_type") or "").strip().lower()
        validated_images.append(normalized)

    limit_error = _detail_image_limit_error(pid, lang, validated_images)
    if limit_error:
        return jsonify({"error": limit_error}), 400

    created: list[dict] = []
    for img in validated_images:

        def _opt_int(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        new_id = medias.add_detail_image(
            pid, lang, img["object_key"],
            content_type=img.get("content_type") or None,
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


@bp.route("/api/products/<int:pid>/detail-images/clear", methods=["POST"])
@login_required
def api_detail_images_clear_all(pid: int):
    """Clear all detail images (manual / from-url / translated) for a target language."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "english detail images cannot be cleared via this endpoint"}), 400

    rows = medias.list_detail_images(pid, lang)
    cleared = medias.soft_delete_detail_images_by_lang(pid, lang)
    for row in rows:
        try:
            _delete_media_object(row["object_key"])
        except Exception:
            pass
    return jsonify({"ok": True, "cleared": cleared})


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
    mode_raw = (
        body.get("concurrency_mode")
        or task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE
    ).strip().lower()
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
