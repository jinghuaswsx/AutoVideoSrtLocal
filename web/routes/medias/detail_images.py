"""产品详情图（detail images）路由。

由 ``web.routes.medias`` package 在 PR 2.14 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort, jsonify, request
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
    detail_images_zip_flask_response as _detail_images_zip_flask_response,
)
from web.services.media_detail_listing import (
    build_detail_image_proxy_response as _build_detail_image_proxy_response_impl,
    build_detail_images_list_response as _build_detail_images_list_response_impl,
    detail_image_proxy_flask_response as _detail_image_proxy_flask_response_impl,
)
from web.services.media_detail_responses import (
    detail_image_json_flask_response as _detail_image_json_flask_response_impl,
)
from web.services.media_detail_from_url import (
    build_detail_images_from_url_response as _build_detail_images_from_url_response_impl,
    build_detail_images_from_url_status_response as _build_detail_images_from_url_status_response_impl,
    fetch_detail_images_page as _fetch_detail_images_page_impl,
)
from web.services.media_detail_mutations import (
    build_clear_detail_images_response as _build_detail_images_clear_response_impl,
    build_delete_detail_image_response as _build_detail_images_delete_response_impl,
    build_reorder_detail_images_response as _build_detail_images_reorder_response_impl,
)
from web.services.media_detail_uploads import (
    build_detail_image_replace_bootstrap_response as _build_detail_image_replace_bootstrap_response_impl,
    build_detail_image_replace_complete_response as _build_detail_image_replace_complete_response_impl,
    build_detail_images_bootstrap_response as _build_detail_images_bootstrap_response_impl,
    build_detail_images_complete_response as _build_detail_images_complete_response_impl,
)
from web.services.media_detail_translation import (
    build_detail_translate_apply_response as _build_detail_translate_apply_response_impl,
    build_detail_translate_from_en_response as _build_detail_translate_from_en_response_impl,
    build_detail_translate_tasks_response as _build_detail_translate_tasks_response_impl,
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
    _safe_image_translate_channel,
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
    return _fetch_detail_images_page_impl(url, lang)


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


def _build_detail_images_from_url_status_response(pid: int, task_id: str, user_id: int):
    from appcore import medias_detail_fetch_tasks as mdf

    return _build_detail_images_from_url_status_response_impl(
        pid,
        task_id,
        int(user_id),
        get_fetch_task_fn=mdf.get,
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


def _build_detail_image_replace_bootstrap_response(
    pid: int,
    image_id: int,
    body: dict,
    user_id: int,
):
    return _build_detail_image_replace_bootstrap_response_impl(
        pid,
        image_id,
        int(user_id),
        body,
        get_detail_image_fn=medias.get_detail_image,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
        build_media_object_key_fn=object_keys.build_media_object_key,
    )


def _build_detail_image_replace_complete_response(pid: int, image_id: int, body: dict):
    return _build_detail_image_replace_complete_response_impl(
        pid,
        image_id,
        body,
        get_detail_image_fn=medias.get_detail_image,
        is_media_available_fn=_is_media_available,
        replace_detail_image_asset_fn=medias.replace_detail_image_asset,
        serialize_detail_image_fn=_serialize_detail_image,
        delete_media_object_fn=_delete_media_object,
    )


def _build_detail_images_delete_response(pid: int, image_id: int):
    return _build_detail_images_delete_response_impl(
        pid,
        image_id,
        get_detail_image_fn=medias.get_detail_image,
        soft_delete_detail_image_fn=medias.soft_delete_detail_image,
        delete_media_object_fn=_delete_media_object,
    )


def _build_detail_images_clear_response(pid: int, body: dict):
    return _build_detail_images_clear_response_impl(
        pid,
        body,
        parse_lang_fn=_parse_lang,
        list_detail_images_fn=medias.list_detail_images,
        soft_delete_detail_images_by_lang_fn=medias.soft_delete_detail_images_by_lang,
        delete_media_object_fn=_delete_media_object,
    )


def _build_detail_images_reorder_response(pid: int, body: dict):
    return _build_detail_images_reorder_response_impl(
        pid,
        body,
        parse_lang_fn=_parse_lang,
        reorder_detail_images_fn=medias.reorder_detail_images,
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
        is_product_listed_fn=medias.is_product_listed,
        parse_lang_fn=_parse_lang,
        default_concurrency_mode=its.get_material_image_translate_default_concurrency_mode(),
        output_dir=OUTPUT_DIR,
        list_detail_images_fn=medias.list_detail_images,
        detail_images_is_gif_fn=_detail_images_is_gif,
        get_prompts_for_lang_fn=its.get_prompts_for_lang,
        get_language_name_fn=medias.get_language_name,
        default_model_id_fn=_default_image_translate_model_id,
        compose_project_name_fn=image_translate_routes._compose_project_name,
        create_image_translate_fn=task_state.create_image_translate,
        start_image_translate_runner_fn=_start_image_translate_runner,
        default_channel_fn=_safe_image_translate_channel,
    )


def _build_detail_translate_tasks_response(pid: int, lang: str, user_id: int):
    return _build_detail_translate_tasks_response_impl(
        pid,
        int(user_id),
        lang,
        is_valid_language_fn=medias.is_valid_language,
        query_tasks_fn=db_query,
    )


def _build_detail_translate_apply_response(pid: int, lang: str, task_id: str, user_id: int):
    return _build_detail_translate_apply_response_impl(
        product_id=pid,
        target_lang=lang,
        task_id=task_id,
        user_id=int(user_id),
        is_valid_language_fn=medias.is_valid_language,
        get_task_fn=store.get,
        is_running_fn=image_translate_runner.is_running,
        apply_translated_detail_images_fn=image_translate_runtime.apply_translated_detail_images_from_task,
    )


def _build_detail_images_list_response(pid: int, lang: str):
    return _build_detail_images_list_response_impl(
        pid,
        lang,
        is_valid_language_fn=medias.is_valid_language,
        list_detail_images_fn=medias.list_detail_images,
        serialize_detail_image_fn=_serialize_detail_image,
    )


def _build_detail_image_proxy_response(image_id: int):
    return _build_detail_image_proxy_response_impl(
        image_id,
        get_detail_image_fn=medias.get_detail_image,
        get_product_fn=medias.get_product,
        can_access_product_fn=_can_access_product,
    )


def _detail_image_proxy_flask_response(outcome):
    return _detail_image_proxy_flask_response_impl(
        outcome,
        send_media_object_fn=_send_media_object,
    )


def _detail_image_json_flask_response(outcome):
    return _detail_image_json_flask_response_impl(outcome)


@bp.route("/api/products/<int:pid>/detail-images", methods=["GET"])
@login_required
def api_detail_images_list(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    routes = _routes()
    result = routes._build_detail_images_list_response(pid, lang)
    return routes._detail_image_json_flask_response(result)


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
        return _routes()._detail_image_json_flask_response(result)

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action=result.audit_action,
        detail=result.audit_detail,
    )

    return _detail_images_zip_flask_response(result)


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
        return _routes()._detail_image_json_flask_response(result)

    _routes()._audit_detail_images_zip_download(
        p,
        pid,
        action=result.audit_action,
        detail=result.audit_detail,
    )

    return _detail_images_zip_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/from-url", methods=["POST"])
@login_required
def api_detail_images_from_url(pid: int):
    """Start a background detail-image fetch task and return its task id."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_detail_images_from_url_response(
        pid,
        p or {},
        body,
        current_user.id,
    )
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/from-url/status/<task_id>", methods=["GET"])
@login_required
def api_detail_images_from_url_status(pid: int, task_id: str):
    """Return the current status for a detail-image fetch task."""
    routes = _routes()
    result = routes._build_detail_images_from_url_status_response(
        pid,
        task_id,
        current_user.id,
    )
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/bootstrap", methods=["POST"])
@login_required
def api_detail_images_bootstrap(pid: int):
    """Reserve local upload targets for detail images."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_detail_images_bootstrap_response(
        pid,
        body,
        current_user.id,
    )
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/complete", methods=["POST"])
@login_required
def api_detail_images_complete(pid: int):
    """Persist detail-image records after browser uploads complete."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_detail_images_complete_response(pid, body)
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/replace-bootstrap", methods=["POST"])
@login_required
def api_detail_image_replace_bootstrap(pid: int, image_id: int):
    """Reserve a local upload target for replacing one existing detail image."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_detail_image_replace_bootstrap_response(
        pid,
        image_id,
        body,
        current_user.id,
    )
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/replace-complete", methods=["POST"])
@login_required
def api_detail_image_replace_complete(pid: int, image_id: int):
    """Persist a single detail-image replacement after browser upload completes."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_detail_image_replace_complete_response(pid, image_id, body)
    return routes._detail_image_json_flask_response(result)


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>", methods=["DELETE"])
@login_required
def api_detail_images_delete(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    routes = _routes()
    outcome = routes._build_detail_images_delete_response(pid, image_id)
    if outcome.not_found:
        abort(404)
    return routes._detail_image_json_flask_response(outcome)


@bp.route("/api/products/<int:pid>/detail-images/clear", methods=["POST"])
@login_required
def api_detail_images_clear_all(pid: int):
    """Clear all detail images (manual / from-url / translated) for a target language."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    outcome = routes._build_detail_images_clear_response(pid, body)
    return routes._detail_image_json_flask_response(outcome)


@bp.route("/api/products/<int:pid>/detail-images/reorder", methods=["POST"])
@login_required
def api_detail_images_reorder(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    outcome = routes._build_detail_images_reorder_response(pid, body)
    return routes._detail_image_json_flask_response(outcome)


@bp.route("/api/products/<int:pid>/detail-images/translate-from-en", methods=["POST"])
@login_required
def api_detail_images_translate_from_en(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    routes = _routes()
    outcome = routes._build_detail_translate_from_en_response(
        pid,
        p or {},
        body,
        current_user.id,
    )
    return routes._detail_image_json_flask_response(outcome)


@bp.route("/api/products/<int:pid>/detail-image-translate-tasks", methods=["GET"])
@login_required
def api_detail_image_translate_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    routes = _routes()
    outcome = routes._build_detail_translate_tasks_response(pid, lang, current_user.id)
    return routes._detail_image_json_flask_response(outcome)


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
    routes = _routes()
    outcome = routes._build_detail_translate_apply_response(
        pid,
        lang,
        task_id,
        current_user.id,
    )
    if outcome.not_found:
        abort(404)
    return routes._detail_image_json_flask_response(outcome)


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/quality-check", methods=["POST"])
@login_required
def api_detail_image_quality_check(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    target_image = medias.get_detail_image(image_id)
    if not target_image or int(target_image.get("product_id") or 0) != pid:
        return jsonify({"error": "商品详情图片未找到"}), 404

    if target_image.get("lang") == "en":
        return jsonify({"error": "无需对英语原图进行翻译质量检测"}), 400

    # 1. 查找源英文图
    source_image = None
    source_detail_image_id = target_image.get("source_detail_image_id")
    if source_detail_image_id:
        source_image = medias.get_detail_image(source_detail_image_id)

    if not source_image:
        # 回退：按同 sort_order 找 en 语种图
        sort_order = target_image.get("sort_order") or 0
        en_images = medias.list_detail_images(pid, "en")
        for img in en_images:
            if img.get("sort_order") == sort_order:
                source_image = img
                break

    if not source_image:
        return jsonify({"error": "未找到对比的英文原图，请先上传英文原图并保持相同的排序"}), 400

    # 2. 定位物理路径
    from appcore import local_media_storage, llm_bindings, llm_client
    import os
    import json
    from appcore.image_translate_runtime import _EVAL_PROMPT, _EVAL_SCHEMA

    src_key = source_image["object_key"]
    dst_key = target_image["object_key"]

    src_path = str(local_media_storage.safe_local_path_for(src_key))
    dst_path = str(local_media_storage.safe_local_path_for(dst_key))

    if not os.path.exists(src_path):
        return jsonify({"error": f"英文原图本地物理文件不存在: {src_key}"}), 400
    if not os.path.exists(dst_path):
        return jsonify({"error": f"翻译图本地物理文件不存在: {dst_key}"}), 400

    # 3. 实时评估
    binding = llm_bindings.resolve("image_translate.eval")
    eval_channel = binding.get("provider") or "openrouter"
    eval_model_id = binding.get("model") or "google/gemini-3.1-flash-lite"

    # 先更新状态为 running
    medias.update_detail_image_evaluation(
        image_id,
        eval_status="running",
        eval_channel=eval_channel,
        eval_model_id=eval_model_id,
    )

    try:
        target_lang_name = medias.get_language_name(target_image["lang"]) or "目标语言"
        prompt = _EVAL_PROMPT.format(target_lang_name=target_lang_name)

        result = llm_client.invoke_generate(
            "image_translate.eval",
            prompt=prompt,
            media=[src_path, dst_path],
            user_id=current_user.id,
            project_id=f"detail_eval_{image_id}",
            response_schema=_EVAL_SCHEMA,
            temperature=0,
            billing_extra={
                "operation": "detail_image_manual_quality_evaluation",
                "item_idx": image_id,
                "filename": os.path.basename(dst_key),
                "source_key": src_key,
                "target_key": dst_key,
            },
        )

        eval_data = result.get("json") if isinstance(result, dict) else None
        if not isinstance(eval_data, dict):
            raise ValueError("大模型质检未返回符合 Schema 的 JSON 数据")

        # 更新数据库成功状态
        medias.update_detail_image_evaluation(
            image_id,
            eval_status="done",
            eval_result_json=json.dumps(eval_data, ensure_ascii=False),
            eval_channel=eval_channel,
            eval_model_id=eval_model_id,
        )

        updated_image = medias.get_detail_image(image_id)
        return jsonify(
            {
                "success": True,
                "detail_image": _serialize_detail_image(updated_image)
            }
        ), 200

    except Exception as exc:
        medias.update_detail_image_evaluation(
            image_id,
            eval_status="failed",
            eval_error=str(exc),
            eval_channel=eval_channel,
            eval_model_id=eval_model_id,
        )
        updated_image = medias.get_detail_image(image_id)
        return jsonify(
            {
                "success": False,
                "error": str(exc),
                "detail_image": _serialize_detail_image(updated_image),
            }
        ), 500


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/retranslate/preview", methods=["POST"])
@login_required
def api_detail_image_retranslate_preview(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    target_image = medias.get_detail_image(image_id)
    if not target_image or int(target_image.get("product_id") or 0) != pid:
        return jsonify({"error": "商品详情图片未找到"}), 404

    if target_image.get("lang") == "en":
        return jsonify({"error": "无需对英语原图进行翻译"}), 400

    # 1. 查找源英文图
    source_image = None
    source_detail_image_id = target_image.get("source_detail_image_id")
    if source_detail_image_id:
        source_image = medias.get_detail_image(source_detail_image_id)

    if not source_image:
        sort_order = target_image.get("sort_order") or 0
        en_images = medias.list_detail_images(pid, "en")
        for img in en_images:
            if img.get("sort_order") == sort_order:
                source_image = img
                break

    if not source_image:
        return jsonify({"error": "未找到对比的英文原图，请先上传英文原图并保持相同的排序"}), 400

    # 2. 定位物理路径
    from appcore import local_media_storage, llm_bindings, llm_client
    import os
    import json
    import logging
    import uuid
    from datetime import datetime

    logger = logging.getLogger("app.web.detail_images")

    src_key = source_image["object_key"]
    src_path = str(local_media_storage.safe_local_path_for(src_key))

    if not os.path.exists(src_path):
        return jsonify({"error": f"英文原图本地物理文件不存在: {src_key}"}), 400

    # 3. 构造优化提示词
    target_lang = target_image["lang"]
    prompt_template = str((its.get_prompts_for_lang(target_lang).get("detail") or "")).strip()
    if not prompt_template:
        return jsonify({"error": f"当前语种 {target_lang} 未配置详情图翻译 prompt"}), 400

    target_lang_name = medias.get_language_name(target_lang) or target_lang.upper()
    baseline_prompt = prompt_template.replace("{target_language_name}", target_lang_name)

    # 从现有的质检结果中提取问题
    eval_result_json = target_image.get("eval_result_json")
    eval_error = target_image.get("eval_error")
    
    # 构造修正提示词
    corrections = []
    if eval_result_json:
        try:
            eval_data = json.loads(eval_result_json)
            if isinstance(eval_data, dict):
                if eval_data.get("has_mixed_languages") and eval_data.get("mixed_languages_details"):
                    corrections.append(f"- 中英混杂/漏译问题：{eval_data.get('mixed_languages_details')}")
                if eval_data.get("has_layout_issue") and eval_data.get("layout_issue_details"):
                    corrections.append(f"- 排版或文字溢出重叠问题：{eval_data.get('layout_issue_details')}")
                issues = eval_data.get("issues")
                if isinstance(issues, list) and issues:
                    issues_str = "、".join(str(x) for x in issues)
                    corrections.append(f"- 质检发现的具体问题清单：{issues_str}")
                if eval_data.get("summary"):
                    corrections.append(f"- 质检专家总结：{eval_data.get('summary')}")
        except Exception:
            pass

    if not corrections and eval_error:
        corrections.append(f"- 上一轮翻译/评估报错或缺陷：{eval_error}")

    optimized_prompt = baseline_prompt
    if corrections:
        correction_block = (
            "\n\n【重要！上一轮翻译缺陷修正指令 / Correction Instructions】\n"
            "在上一轮的翻译中，该图片被评估发现存在以下问题，请在本次生成时务必严格纠正，确保翻译地道、无英文残留且排版美观：\n"
            + "\n".join(corrections)
        )
        optimized_prompt = f"{baseline_prompt}\n{correction_block}"

    # 4. 调用大模型重新生图并保存为草稿/预览
    from appcore import gemini_image
    
    binding = llm_bindings.resolve("image_translate.generate")
    channel = binding.get("provider") or _safe_image_translate_channel()
    model_id = binding.get("model") or _default_image_translate_model_id()

    try:
        with open(src_path, "rb") as f:
            src_bytes = f.read()

        src_mime = image_translate_runtime.ImageTranslateRuntime._guess_mime(src_key, filename=os.path.basename(src_key), data=src_bytes)
        
        out_bytes, out_mime = gemini_image.generate_image(
            prompt=optimized_prompt,
            source_image=src_bytes,
            source_mime=src_mime,
            model=model_id,
            user_id=current_user.id,
            project_id=f"detail_retranslate_draft_{image_id}",
            service="image_translate.generate",
            channel=channel or None,
        )

        # 写入草稿文件
        draft_filename = f"retranslate_{image_id}_{uuid.uuid4().hex}.png"
        draft_key = f"drafts/{pid}/{draft_filename}"
        draft_path = local_media_storage.write_bytes(draft_key, out_bytes)
        logger.info(f"重新翻译生成草稿文件：{draft_path}")

        # 5. 重新执行质检，获取新的评分和结果
        from appcore.image_translate_runtime import _EVAL_PROMPT, _EVAL_SCHEMA
        
        eval_binding = llm_bindings.resolve("image_translate.eval")
        eval_channel = eval_binding.get("provider") or "openrouter"
        eval_model_id = eval_binding.get("model") or "google/gemini-3.1-flash-lite"
        
        eval_prompt = _EVAL_PROMPT.format(target_lang_name=target_lang_name)
        
        eval_result = llm_client.invoke_generate(
            "image_translate.eval",
            prompt=eval_prompt,
            media=[src_path, str(draft_path)],
            user_id=current_user.id,
            project_id=f"detail_eval_post_retranslate_draft_{image_id}",
            response_schema=_EVAL_SCHEMA,
            temperature=0,
            billing_extra={
                "operation": "detail_image_retranslate_quality_evaluation_draft",
                "item_idx": image_id,
                "filename": draft_filename,
                "source_key": src_key,
                "target_key": draft_key,
            },
        )
        
        eval_data = eval_result.get("json") if isinstance(eval_result, dict) else None
        if not isinstance(eval_data, dict):
            raise ValueError("重新翻译后的质检未返回符合 Schema 的 JSON 数据")
            
        return jsonify({
            "success": True,
            "source_image": _serialize_detail_image(source_image),
            "current_image": _serialize_detail_image(target_image),
            "new_image": {
                "draft_filename": draft_filename,
                "url": f"/medias/api/products/{pid}/detail-images/draft/{draft_filename}",
                "eval_result": eval_data,
                "eval_channel": eval_channel,
                "eval_model_id": eval_model_id
            }
        }), 200

    except Exception as exc:
        logger.exception(f"重新翻译详情图预览失败: {exc}")
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


@bp.route("/api/products/<int:pid>/detail-images/draft/<path:filename>", methods=["GET"])
@login_required
def api_detail_image_draft_proxy(pid: int, filename: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    from werkzeug.utils import secure_filename
    clean_filename = secure_filename(filename)
    draft_key = f"drafts/{pid}/{clean_filename}"
    
    from appcore import local_media_storage
    if not local_media_storage.exists(draft_key):
        abort(404)
        
    draft_path = local_media_storage.safe_local_path_for(draft_key)
    from flask import send_file
    return send_file(str(draft_path))


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/retranslate/confirm", methods=["POST"])
@login_required
def api_detail_image_retranslate_confirm(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    target_image = medias.get_detail_image(image_id)
    if not target_image or int(target_image.get("product_id") or 0) != pid:
        return jsonify({"error": "商品详情图片未找到"}), 404

    data = request.get_json() or {}
    draft_filename = data.get("draft_filename")
    eval_result = data.get("eval_result")
    eval_channel = data.get("eval_channel")
    eval_model_id = data.get("eval_model_id")

    if not draft_filename:
        return jsonify({"error": "缺失 draft_filename 参数"}), 400

    from appcore import local_media_storage
    from werkzeug.utils import secure_filename
    import os
    import json
    import logging
    from datetime import datetime
    from ._helpers import probe_media_info_safe

    logger = logging.getLogger("app.web.detail_images")

    clean_filename = secure_filename(draft_filename)
    draft_key = f"drafts/{pid}/{clean_filename}"
    draft_path = local_media_storage.safe_local_path_for(draft_key)

    if not os.path.exists(str(draft_path)):
        return jsonify({"error": "新生成的草稿图片已过期或不存在"}), 400

    dst_key = target_image["object_key"]
    dst_path = str(local_media_storage.safe_local_path_for(dst_key))

    # 1. 备份原图
    if os.path.exists(dst_path):
        filename = os.path.basename(dst_key)
        base, ext = os.path.splitext(filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_filename = f"{base}_bak_{timestamp}{ext}"
        
        dst_dir = os.path.dirname(dst_key)
        backup_key = f"{dst_dir}/{backup_filename}" if dst_dir else backup_filename
        backup_path = str(local_media_storage.safe_local_path_for(backup_key))
        
        os.rename(dst_path, backup_path)
        logger.info(f"备份有问题原图：{dst_path} -> {backup_path}")

    # 2. 覆盖应用新图
    with open(str(draft_path), "rb") as f:
        out_bytes = f.read()
    local_media_storage.write_bytes(dst_key, out_bytes)
    logger.info(f"采用新图覆盖成功：{dst_path}")

    # 探测新图宽高及大小
    file_size = len(out_bytes)
    width, height = None, None
    try:
        info = probe_media_info_safe(dst_path)
        width = info.get("width")
        height = info.get("height")
    except Exception:
        pass

    medias.execute(
        "UPDATE media_product_detail_images "
        "SET file_size=%s, width=%s, height=%s "
        "WHERE id=%s AND deleted_at IS NULL",
        (file_size, width, height, image_id),
    )

    # 3. 写入质检评估结果
    medias.update_detail_image_evaluation(
        image_id,
        eval_status="done",
        eval_result_json=json.dumps(eval_result, ensure_ascii=False) if eval_result else None,
        eval_channel=eval_channel,
        eval_model_id=eval_model_id,
    )

    # 4. 清理草稿文件
    try:
        os.remove(str(draft_path))
    except Exception:
        pass

    updated_image = medias.get_detail_image(image_id)
    return jsonify({
        "success": True,
        "detail_image": _serialize_detail_image(updated_image)
    }), 200


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>/retranslate/discard", methods=["POST"])
@login_required
def api_detail_image_retranslate_discard(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    data = request.get_json() or {}
    draft_filename = data.get("draft_filename")

    if draft_filename:
        from appcore import local_media_storage
        from werkzeug.utils import secure_filename
        import os
        import logging
        logger = logging.getLogger("app.web.detail_images")

        clean_filename = secure_filename(draft_filename)
        draft_key = f"drafts/{pid}/{clean_filename}"
        draft_path = local_media_storage.safe_local_path_for(draft_key)

        try:
            if os.path.exists(str(draft_path)):
                os.remove(str(draft_path))
                logger.info(f"清理放弃的草稿文件成功：{draft_path}")
        except Exception as exc:
            logger.warning(f"清理草稿文件失败: {exc}")

    return jsonify({"success": True}), 200


@bp.route("/detail-image/<int:image_id>", methods=["GET"])
@login_required
def detail_image_proxy(image_id: int):
    """Serve or redirect to the stored detail image asset."""
    outcome = _routes()._build_detail_image_proxy_response(image_id)
    if outcome.not_found:
        abort(404)
    return _detail_image_proxy_flask_response(outcome)


@bp.route("/products/<int:pid>/detail-images/batch-evaluation-report", methods=["GET"])
@login_required
def api_detail_images_batch_evaluation_report(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        abort(400, "Language parameter is required")

    # Get all detail images for this language
    all_images = medias.list_detail_images(pid, lang)
    # Filter out gifs
    from ._helpers import _detail_images_is_gif
    images = [img for img in all_images if not _detail_images_is_gif(img.get("object_key") or "")]

    # We want to deserialize and attach the source (English) image for comparison
    en_images = medias.list_detail_images(pid, "en")
    en_by_order = {img.get("sort_order", 0): img for img in en_images if not _detail_images_is_gif(img.get("object_key") or "")}

    serialized_items = []
    import json
    for img in images:
        serialized = _serialize_detail_image(img)
        # Parse eval_result if exists
        eval_result = None
        eval_result_json = img.get("eval_result_json")
        if eval_result_json:
            try:
                eval_result = json.loads(eval_result_json)
            except Exception:
                pass
        serialized["eval_result"] = eval_result

        # Attach source image
        source_img = None
        source_id = img.get("source_detail_image_id")
        if source_id:
            for en_img in en_images:
                if en_img.get("id") == source_id:
                    source_img = _serialize_detail_image(en_img)
                    break
        if not source_img:
            # Fallback
            so = img.get("sort_order") or 0
            if so in en_by_order:
                source_img = _serialize_detail_image(en_by_order[so])

        serialized["source_image"] = source_img
        serialized_items.append(serialized)

    from flask import render_template
    lang_name = medias.get_language_name(lang) or lang.upper()
    return render_template(
        "medias_batch_evaluation_report.html",
        product=p,
        lang=lang,
        lang_name=lang_name,
        items=serialized_items,
    )
