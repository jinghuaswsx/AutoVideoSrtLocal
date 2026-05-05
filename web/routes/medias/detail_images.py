"""产品详情图（detail images）路由。

由 ``web.routes.medias`` package 在 PR 2.14 抽出；行为不变。
"""
from __future__ import annotations

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
    build_detail_images_zip_response as _build_detail_images_zip_response_impl,
    build_localized_detail_images_zip_response as _build_localized_detail_images_zip_response_impl,
)
from web.services.media_detail_listing import (
    build_detail_images_list_response as _build_detail_images_list_response_impl,
)
from web.services.media_detail_from_url import (
    build_detail_images_from_url_response as _build_detail_images_from_url_response_impl,
)
from web.services.media_detail_mutations import (
    clear_detail_images,
    delete_detail_image,
    reorder_detail_images as reorder_detail_images_command,
)
from web.services.media_detail_uploads import (
    build_detail_images_bootstrap_response as _build_detail_images_bootstrap_response_impl,
    build_detail_images_complete_response as _build_detail_images_complete_response_impl,
)
from web.services.media_detail_translation import (
    apply_detail_translate_task,
    build_detail_translate_from_en_response as _build_detail_translate_from_en_response_impl,
    project_detail_translate_task_rows,
)

from . import bp
from ._helpers import (
    _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES,
    _DETAIL_IMAGE_KIND_LABELS,
    _DETAIL_IMAGE_LIMITS,
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


def _fetch_detail_images_page(url: str, lang: str):
    from appcore.link_check_fetcher import LinkCheckFetcher

    return LinkCheckFetcher().fetch_page(url, lang)


def _build_detail_images_from_url_response(
    pid: int,
    product: dict,
    body: dict,
    user_id: int,
):
    from appcore import medias_detail_fetch_tasks as mdf

    return _build_detail_images_from_url_response_impl(
        pid,
        int(user_id),
        product,
        body,
        is_valid_language_fn=medias.is_valid_language,
        create_fetch_task_fn=mdf.create,
        fetch_page_fn=_fetch_detail_images_page,
        download_image_to_local_media_fn=_download_image_to_local_media,
        soft_delete_detail_images_by_lang_fn=medias.soft_delete_detail_images_by_lang,
        detail_image_empty_counts_fn=_detail_image_empty_counts,
        detail_image_existing_counts_fn=_detail_image_existing_counts,
        detail_image_kind_from_download_ext_fn=_detail_image_kind_from_download_ext,
        detail_image_limits=_DETAIL_IMAGE_LIMITS,
        detail_image_kind_labels=_DETAIL_IMAGE_KIND_LABELS,
        add_detail_image_fn=medias.add_detail_image,
        get_detail_image_fn=medias.get_detail_image,
        serialize_detail_image_fn=_serialize_detail_image,
        max_download_candidates=_DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES,
    )


def _build_detail_images_bootstrap_response(
    pid: int,
    body: dict,
    user_id: int,
):
    return _build_detail_images_bootstrap_response_impl(
        pid,
        int(user_id),
        body,
        parse_lang_fn=_parse_lang,
        detail_image_limit_error_fn=_detail_image_limit_error,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
        build_media_object_key_fn=object_keys.build_media_object_key,
    )


def _build_detail_images_complete_response(pid: int, body: dict):
    return _build_detail_images_complete_response_impl(
        pid,
        body,
        parse_lang_fn=_parse_lang,
        is_media_available_fn=_is_media_available,
        detail_image_limit_error_fn=_detail_image_limit_error,
        add_detail_image_fn=medias.add_detail_image,
        get_detail_image_fn=medias.get_detail_image,
        serialize_detail_image_fn=_serialize_detail_image,
    )


def _build_detail_images_zip_response(
    pid: int,
    product: dict,
    lang: str,
    kind: str,
):
    return _build_detail_images_zip_response_impl(
        pid,
        product,
        lang,
        kind,
        is_valid_language_fn=medias.is_valid_language,
        list_detail_images_fn=medias.list_detail_images,
        detail_images_is_gif_fn=_detail_images_is_gif,
        archive_basename_fn=_detail_images_archive_basename,
        download_media_object_fn=_download_media_object,
    )


def _build_localized_detail_images_zip_response(pid: int, product: dict):
    return _build_localized_detail_images_zip_response_impl(
        pid,
        product,
        list_languages_fn=medias.list_languages,
        list_detail_images_fn=medias.list_detail_images,
        detail_images_is_gif_fn=_detail_images_is_gif,
        archive_product_code_fn=_detail_images_archive_product_code,
        archive_part_fn=_detail_images_archive_part,
        download_media_object_fn=_download_media_object,
    )


def _build_detail_translate_from_en_response(
    pid: int,
    product: dict,
    body: dict,
    user_id: int,
):
    return _build_detail_translate_from_en_response_impl(
        pid,
        int(user_id),
        product,
        body,
        parse_lang_fn=_parse_lang,
        default_concurrency_mode=task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE,
        output_dir=OUTPUT_DIR,
        list_detail_images_fn=medias.list_detail_images,
        detail_images_is_gif_fn=_detail_images_is_gif,
        get_prompts_for_lang_fn=its.get_prompts_for_lang,
        get_language_name_fn=medias.get_language_name,
        default_model_id_fn=_default_image_translate_model_id,
        compose_project_name_fn=image_translate_routes._compose_project_name,
        create_image_translate_fn=task_state.create_image_translate,
        start_image_translate_runner_fn=_start_image_translate_runner,
    )


def _build_detail_images_list_response(pid: int, lang: str):
    return _build_detail_images_list_response_impl(
        pid,
        lang,
        is_valid_language_fn=medias.is_valid_language,
        list_detail_images_fn=medias.list_detail_images,
        serialize_detail_image_fn=_serialize_detail_image,
    )


@bp.route("/api/products/<int:pid>/detail-images", methods=["GET"])
@login_required
def api_detail_images_list(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    result = _routes()._build_detail_images_list_response(pid, lang)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/detail-images/download-zip", methods=["GET"])
@login_required
def api_detail_images_download_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    kind = (request.args.get("kind") or "image").strip().lower()
    result = _routes()._build_detail_images_zip_response(pid, p or {}, lang, kind)
    if result.not_found:
        abort(404)
    if result.error:
        return jsonify({"error": result.error}), result.status_code

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action=result.audit_action,
        detail=result.audit_detail,
    )

    archive = result.archive
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

    result = _routes()._build_localized_detail_images_zip_response(pid, p or {})
    if result.not_found:
        abort(404)
    if result.error:
        return jsonify({"error": result.error}), result.status_code

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action=result.audit_action,
        detail=result.audit_detail,
    )

    archive = result.archive
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
    result = _routes()._build_detail_images_from_url_response(
        pid,
        p or {},
        body,
        current_user.id,
    )
    return jsonify(result.payload), result.status_code


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
    result = _routes()._build_detail_images_bootstrap_response(
        pid,
        body,
        current_user.id,
    )
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/detail-images/complete", methods=["POST"])
@login_required
def api_detail_images_complete(pid: int):
    """Persist detail-image records after browser uploads complete."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = _routes()._build_detail_images_complete_response(pid, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>", methods=["DELETE"])
@login_required
def api_detail_images_delete(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    outcome = delete_detail_image(
        image_id,
        product_id=pid,
        get_detail_image=medias.get_detail_image,
        soft_delete_detail_image=medias.soft_delete_detail_image,
        delete_media_object=_delete_media_object,
    )
    if outcome.not_found:
        abort(404)
    return jsonify(outcome.payload)


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

    outcome = clear_detail_images(
        pid,
        lang,
        list_detail_images=medias.list_detail_images,
        soft_delete_detail_images_by_lang=medias.soft_delete_detail_images_by_lang,
        delete_media_object=_delete_media_object,
    )
    if outcome.error:
        return jsonify({"error": outcome.error}), outcome.status_code
    return jsonify(outcome.payload)


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
    outcome = reorder_detail_images_command(
        pid,
        lang,
        body.get("ids") or [],
        reorder_detail_images=medias.reorder_detail_images,
    )
    if outcome.error:
        return jsonify({"error": outcome.error}), outcome.status_code
    return jsonify(outcome.payload)


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
    outcome = _routes()._build_detail_translate_from_en_response(
        pid,
        p or {},
        body,
        current_user.id,
    )
    if outcome.error:
        return jsonify({"error": outcome.error}), outcome.status_code
    return jsonify(outcome.payload), outcome.status_code


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
    items = project_detail_translate_task_rows(rows, product_id=pid, target_lang=lang)
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
    outcome = apply_detail_translate_task(
        task,
        task_id=task_id,
        product_id=pid,
        target_lang=lang,
        user_id=int(current_user.id),
        is_running=image_translate_runner.is_running,
        apply_translated_detail_images=image_translate_runtime.apply_translated_detail_images_from_task,
    )
    if outcome.not_found:
        abort(404)
    if outcome.error:
        return jsonify({"error": outcome.error}), outcome.status_code
    return jsonify(outcome.payload)


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
