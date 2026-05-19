from __future__ import annotations

import io
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from web.auth import permission_required

from appcore import image_translate_store, local_media_storage, medias, runner_dispatch, task_state
from appcore.gemini_image import is_valid_image_model, list_image_models
from appcore import image_translate_settings as its

_BACKEND_LABELS = {
    "aistudio":   "Google AI Studio",
    "cloud":      "Google Cloud (Vertex AI)",
    "cloud_adc":  "Google Vertex AI (ADC)",
    "openrouter": "OpenRouter",
    "doubao":     "豆包 ARK（Seedream）",
}


def _channel_label(channel: str) -> str:
    key = (channel or "").strip().lower()
    return its.CHANNEL_LABELS.get(key) or _BACKEND_LABELS.get(key, key or "unknown")


def _backend_badge(channel: str | None = None) -> dict:
    """读 system_settings 里的全局通道；DB 异常时回落 aistudio，避免页面 500。"""
    key = (channel or "").strip().lower()
    if key not in its.CHANNELS:
        key = _safe_image_translate_channel()
    return {"key": key, "label": _channel_label(key)}


from web import store
from web.services import image_translate_runner
from web.services.image_translate import (
    build_image_translate_error_response,
    build_image_translate_payload_response,
    image_translate_flask_response,
)

bp = Blueprint("image_translate", __name__)

db_query = image_translate_store.query
db_query_one = image_translate_store.query_one
db_execute = image_translate_store.execute

_MAX_ITEMS = task_state.IMAGE_TRANSLATE_MAX_ITEMS
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_PRODUCT_NAME_MAX_LEN = 60
_PROJECT_NAME_ILLEGAL = set('\\/:*?"<>|\t\r\n')
_BANANA_RETRY_CHANNEL = "aistudio"
_BANANA_RETRY_MODEL = "gemini-3.1-flash-image-preview"
_BANANA_RETRY_LABEL = "banana重新生成"
_PARALLEL_CAPABLE_CHANNELS = {"openrouter", "apimart"}


def _safe_image_translate_channel() -> str:
    try:
        return its.get_channel()
    except Exception:
        return "aistudio"


def _safe_image_translate_default_model(channel: str) -> str:
    try:
        return its.get_default_model(channel)
    except Exception:
        from appcore.gemini_image import coerce_image_model

        return coerce_image_model("", channel=channel)


def _image_translate_channels_payload() -> list[dict]:
    return [{"id": code, "name": _channel_label(code)} for code in its.CHANNELS]


def _requested_image_translate_channel(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return _safe_image_translate_channel()
    return raw if raw in its.CHANNELS else ""


def _sanitize_product_name(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "") if ch not in _PROJECT_NAME_ILLEGAL)
    return cleaned.strip()


def _compose_project_name(product_name: str, preset: str, lang_name: str) -> str:
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y%m%d")
    preset_label = "封面" if preset == "cover" else ("商品详情" if preset == "detail" else "")
    parts = [product_name.strip(), preset_label, (lang_name or "").strip(), today]
    return "-".join(p for p in parts if p)


def _normalize_concurrency_mode(value: str | None) -> str:
    raw = (value or task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE).strip().lower()
    return raw if raw in {"sequential", "parallel"} else task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE


def _channel_allows_parallel(channel: str | None) -> bool:
    return (channel or "").strip().lower() in _PARALLEL_CAPABLE_CHANNELS


def _coerce_concurrency_mode_for_channel(mode: str, channel: str | None) -> str:
    if mode == "parallel" and not _channel_allows_parallel(channel):
        return "sequential"
    return mode


def _concurrency_mode_label(value: str | None) -> str:
    return "并行" if _normalize_concurrency_mode(value) == "parallel" else "串行"

_upload_guard = threading.Lock()
_upload_reservations: dict[str, dict] = {}
_local_upload_guard = threading.Lock()
_local_upload_reservations: dict[str, dict] = {}


def _get_existing_image_translate_task(task_id: str) -> dict:
    task = store.get(task_id)
    if (
        not task
        or task.get("type") != "image_translate"
        or (task.get("status") or "").strip() == "deleted"
        or task.get("deleted_at")
    ):
        abort(404)
    return task


def _task_belongs_to_current_user(task: dict) -> bool:
    return str(task.get("_user_id")) == str(getattr(current_user, "id", ""))


def _is_superadmin_user() -> bool:
    return (
        bool(getattr(current_user, "is_superadmin", False))
        or bool(getattr(current_user, "is_admin", False))
        or (getattr(current_user, "role", "") == "admin")
    )


def _get_owned_task(task_id: str) -> dict:
    task = _get_existing_image_translate_task(task_id)
    if not _task_belongs_to_current_user(task):
        abort(404)
    return task


def _get_retryable_task(task_id: str) -> dict:
    task = _get_existing_image_translate_task(task_id)
    if not (_task_belongs_to_current_user(task) or _is_superadmin_user()):
        abort(404)
    return task


def _get_viewable_task(task_id: str) -> dict:
    task = _get_existing_image_translate_task(task_id)
    if not (_task_belongs_to_current_user(task) or _is_superadmin_user()):
        abort(404)
    return task


def _target_language_name(code: str) -> str:
    row = db_query_one(
        "SELECT name_zh FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    if not row:
        return code
    return row["name_zh"] or code


def _start_runner(task_id: str, uid: int) -> bool:
    return image_translate_runner.start(task_id, user_id=uid)


def _task_runner_user_id(task: dict) -> int:
    return int(task.get("_user_id") or getattr(current_user, "id", 0) or 0)


def start_image_translate_runner(task_id: str, uid: int) -> bool:
    return _start_runner(task_id, uid)


runner_dispatch.register_image_translate_runner(
    start=lambda task_id, user_id=None: start_image_translate_runner(task_id, int(user_id or 0)),
    is_running=lambda task_id: image_translate_runner.is_running(task_id),
)


def _build_source_object_key(user_id: int, task_id: str, idx: int, ext: str) -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    return f"uploads/image_translate/{user_id}/{task_id}/src_{idx}.{ext}"


def _resolve_source_bucket(task: dict, item: dict) -> str:
    """返回 'media' 或 'upload'（默认）。

    从 medias_edit_detail 入口创建的任务，源图来自 media bucket；老任务默认 upload。
    item 级优先级高于 task 级，保证同一任务里混合来源也能各自对。
    """
    bucket = (item.get("source_bucket") or "").strip().lower()
    if not bucket:
        bucket = ((task.get("medias_context") or {}).get("source_bucket") or "").strip().lower()
    return "media" if bucket == "media" else "upload"


def _signed_source_url(task: dict, item: dict) -> str:
    object_key = (item.get("src_tos_key") or "").strip()
    if not object_key:
        abort(404)
    if local_media_storage.exists(object_key):
        return url_for("medias.media_object_proxy", object_key=object_key)
    if _resolve_source_bucket(task, item) == "media":
        return url_for("medias.media_object_proxy", object_key=object_key)
    abort(404)


def _result_artifact_url(object_key: str) -> str:
    key = (object_key or "").strip()
    if not key:
        abort(404)
    if local_media_storage.exists(key):
        return url_for("medias.media_object_proxy", object_key=key)
    abort(404)


def _download_artifact_object(object_key: str, destination: str) -> str:
    key = (object_key or "").strip()
    if local_media_storage.exists(key):
        return local_media_storage.download_to(key, destination)
    raise FileNotFoundError(f"local image artifact not found: {key}")


def _delete_artifact_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass


def _result_object_key_for_item(task: dict, item: dict) -> str:
    src_key = (item.get("src_tos_key") or "").strip()
    filename = (item.get("filename") or "").strip()
    ext = os.path.splitext(src_key)[1] or os.path.splitext(filename)[1] or ".jpg"
    ext = ext.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    uid = task.get("_user_id") or getattr(current_user, "id", 0) or 0
    return f"artifacts/image_translate/{uid}/{task['id']}/out_{int(item['idx'])}{ext}"


def _copy_source_to_result(task: dict, item: dict) -> str:
    src_key = (item.get("src_tos_key") or "").strip()
    if not src_key:
        raise FileNotFoundError("source image missing")
    suffix = os.path.splitext(src_key)[1] or os.path.splitext(item.get("filename") or "")[1] or ".jpg"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="it_source_result_")
    os.close(fd)
    try:
        local_media_storage.download_to(src_key, tmp_path)
        with open(tmp_path, "rb") as f:
            raw = f.read()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    dst_key = _result_object_key_for_item(task, item)
    local_media_storage.write_bytes(dst_key, raw)
    return dst_key


def _mark_item_as_copied_source_result(task: dict, item: dict) -> str:
    old_dst = (item.get("dst_tos_key") or "").strip()
    dst_key = _copy_source_to_result(task, item)
    if old_dst and old_dst != dst_key:
        _delete_artifact_object(old_dst)
    item["status"] = "done"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = dst_key
    item["provider_task_id"] = ""
    item["provider_task_submitted_at"] = 0.0
    item["apimart_task_id"] = ""
    item["apimart_submitted_at"] = 0.0
    item["generation_channel_override"] = ""
    item["generation_model_override"] = ""
    item["generation_override_label"] = ""
    item["result_source"] = "copied_source"
    return dst_key


def _update_task_progress_from_items(task: dict) -> None:
    items = task.get("items") or []
    task["progress"] = {
        "total": len(items),
        "done": sum(1 for it in items if it.get("status") == "done"),
        "failed": sum(1 for it in items if it.get("status") == "failed"),
        "running": sum(1 for it in items if it.get("status") == "running"),
    }


def _item_has_success_result(item: dict) -> bool:
    return item.get("status") == "done" and bool((item.get("dst_tos_key") or "").strip())


def _auto_apply_if_all_done(task: dict) -> None:
    items = task.get("items") or []
    if not items or any((it.get("status") or "") != "done" for it in items):
        return
    ctx = dict(task.get("medias_context") or {})
    if not ctx.get("auto_apply_detail_images"):
        return
    try:
        from appcore.image_translate_runtime import apply_translated_detail_images_from_task

        apply_translated_detail_images_from_task(
            task,
            allow_partial=False,
            user_id=_task_runner_user_id(task),
        )
    except Exception as exc:
        ctx["apply_status"] = "apply_error"
        ctx["last_apply_error"] = str(exc)
        task["medias_context"] = ctx


def _reset_item_processing_state(item: dict) -> None:
    item["status"] = "pending"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = ""
    item["provider_task_id"] = ""
    item["provider_task_submitted_at"] = 0.0
    item["apimart_task_id"] = ""
    item["apimart_submitted_at"] = 0.0
    item["generation_channel_override"] = ""
    item["generation_model_override"] = ""
    item["generation_override_label"] = ""
    item["text_detect_status"] = "pending"
    item["text_detect_has_text"] = None
    item["text_detect_reason"] = ""
    item["text_detect_error"] = ""
    item["result_source"] = ""


def _reserve_local_source_upload(*, user_id: int, task_id: str, idx: int, object_key: str, filename: str) -> dict:
    upload_id = uuid.uuid4().hex
    with _local_upload_guard:
        _local_upload_reservations[upload_id] = {
            "user_id": int(user_id),
            "task_id": task_id,
            "idx": int(idx),
            "object_key": object_key,
            "filename": filename,
        }
    return {
        "idx": idx,
        "object_key": object_key,
        "upload_url": url_for("image_translate.api_local_source_upload", upload_id=upload_id),
    }


def _state_payload(task: dict) -> dict:
    concurrency_mode = _normalize_concurrency_mode(task.get("concurrency_mode"))
    channel = (task.get("channel") or "").strip().lower()
    if channel not in its.CHANNELS:
        channel = _safe_image_translate_channel()
    return {
        "id": task.get("id"),
        "type": "image_translate",
        "status": task.get("status") or "queued",
        "preset": task.get("preset") or "",
        "target_language": task.get("target_language") or "",
        "target_language_name": task.get("target_language_name") or "",
        "channel": channel,
        "channel_label": _channel_label(channel),
        "model_id": task.get("model_id") or "",
        "prompt": task.get("prompt") or "",
        "product_name": task.get("product_name") or "",
        "project_name": task.get("project_name") or "",
        "concurrency_mode": concurrency_mode,
        "concurrency_mode_label": _concurrency_mode_label(concurrency_mode),
        "progress": dict(task.get("progress") or {}),
        "items": list(task.get("items") or []),
        "medias_context": dict(task.get("medias_context") or {}),
        "steps": dict(task.get("steps") or {}),
        "error": task.get("error") or "",
        "is_running": image_translate_runner.is_running(task.get("id") or ""),
    }


@bp.route("/api/image-translate/models", methods=["GET"])
@login_required
def api_models():
    default_channel = _safe_image_translate_channel()
    channel = _requested_image_translate_channel(request.args.get("channel"))
    if not channel:
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported channel", 400)
        )
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {
                "items": [{"id": mid, "name": label} for mid, label in list_image_models(channel)],
                "default_model_id": _safe_image_translate_default_model(channel),
                "channel": channel,
                "default_channel": default_channel,
                "channels": _image_translate_channels_payload(),
            }
        )
    )


@bp.route("/api/image-translate/system-prompts", methods=["GET"])
@login_required
def api_system_prompts():
    lang = (request.args.get("lang") or "").strip().lower()
    if not its.is_image_translate_language_supported(lang):
        return image_translate_flask_response(
            build_image_translate_error_response(
                "lang must be a supported image-translate language",
                400,
            )
        )
    return image_translate_flask_response(
        build_image_translate_payload_response(its.get_prompts_for_lang(lang))
    )


@bp.route("/api/image-translate/upload/bootstrap", methods=["POST"])
@login_required
def api_upload_bootstrap():
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not files:
        return image_translate_flask_response(build_image_translate_error_response("files required", 400))
    if len(files) > _MAX_ITEMS:
        return image_translate_flask_response(
            build_image_translate_error_response(f"too many files (max {_MAX_ITEMS})", 400)
        )

    task_id = str(uuid.uuid4())
    uploads = []
    reserved = []
    for idx, f in enumerate(files):
        filename = (f.get("filename") or "").strip()
        if not filename:
            return image_translate_flask_response(
                build_image_translate_error_response(f"missing filename for file #{idx}", 400)
            )
        dot_idx = filename.rfind(".")
        ext = filename[dot_idx:].lower() if dot_idx >= 0 else ""
        if ext not in _ALLOWED_EXT:
            return image_translate_flask_response(
                build_image_translate_error_response(f"unsupported image extension: {filename}", 400)
            )
        key = _build_source_object_key(current_user.id, task_id, idx, ext)
        uploads.append(_reserve_local_source_upload(
            user_id=current_user.id,
            task_id=task_id,
            idx=idx,
            object_key=key,
            filename=filename,
        ))
        reserved.append({"idx": idx, "object_key": key, "filename": filename})
    with _upload_guard:
        _upload_reservations[task_id] = {
            "user_id": current_user.id,
            "files": reserved,
        }
    return image_translate_flask_response(
        build_image_translate_payload_response({"task_id": task_id, "uploads": uploads})
    )


@bp.route("/api/image-translate/upload/local/<upload_id>", methods=["PUT"])
@login_required
def api_local_source_upload(upload_id: str):
    with _local_upload_guard:
        reservation = _local_upload_reservations.get(upload_id)
    if not reservation or int(reservation.get("user_id") or 0) != int(current_user.id):
        abort(404)
    local_media_storage.write_stream(reservation["object_key"], request.stream)
    return ("", 204)


@bp.route("/api/image-translate/upload/complete", methods=["POST"])
@login_required
def api_upload_complete():
    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    preset = (body.get("preset") or "").strip().lower()
    lang_code = (body.get("target_language") or "").strip().lower()
    model_id = (body.get("model_id") or "").strip()
    prompt_tpl = (body.get("prompt") or "").strip()
    product_name_raw = (body.get("product_name") or "").strip()
    uploaded = body.get("uploaded") or []

    with _upload_guard:
        rv = _upload_reservations.get(task_id)
    if not rv or rv["user_id"] != current_user.id:
        return image_translate_flask_response(
            build_image_translate_error_response("invalid or expired task_id", 403)
        )
    if preset not in {"cover", "detail"}:
        return image_translate_flask_response(
            build_image_translate_error_response("preset must be cover or detail", 400)
        )
    if not medias.is_valid_language(lang_code) or lang_code == "en":
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported target language", 400)
        )
    channel = _requested_image_translate_channel(body.get("channel"))
    if not channel:
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported channel", 400)
        )
    if not is_valid_image_model(model_id, channel=channel):
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported model", 400)
        )
    if not prompt_tpl:
        return image_translate_flask_response(build_image_translate_error_response("prompt required", 400))
    product_name = _sanitize_product_name(product_name_raw)
    if not product_name:
        return image_translate_flask_response(
            build_image_translate_error_response("product_name required", 400)
        )
    if len(product_name) > _PRODUCT_NAME_MAX_LEN:
        return image_translate_flask_response(
            build_image_translate_error_response(
                f"product_name too long (max {_PRODUCT_NAME_MAX_LEN})",
                400,
            )
        )
    mode_raw = (
        body.get("concurrency_mode")
        or task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE
    ).strip().lower()
    if mode_raw not in {"sequential", "parallel"}:
        return image_translate_flask_response(
            build_image_translate_error_response("concurrency_mode must be sequential or parallel", 400)
        )
    mode_raw = _coerce_concurrency_mode_for_channel(mode_raw, channel)
    if not uploaded:
        return image_translate_flask_response(build_image_translate_error_response("uploaded required", 400))

    reserved = {f["idx"]: f for f in rv["files"]}
    items = []
    seen_idxs: set[int] = set()
    for u in uploaded:
        if not isinstance(u, dict):
            return image_translate_flask_response(
                build_image_translate_error_response("uploaded item must be an object", 400)
            )
        idx_raw = u.get("idx")
        if isinstance(idx_raw, bool):
            return image_translate_flask_response(
                build_image_translate_error_response("uploaded item idx must be an integer", 400)
            )
        if isinstance(idx_raw, int):
            idx = idx_raw
        elif isinstance(idx_raw, str) and idx_raw.strip().isdigit():
            idx = int(idx_raw.strip())
        else:
            return image_translate_flask_response(
                build_image_translate_error_response("uploaded item idx must be an integer", 400)
            )
        if idx in seen_idxs:
            return image_translate_flask_response(
                build_image_translate_error_response(f"duplicated uploaded idx={idx}", 400)
            )
        seen_idxs.add(idx)
        key = (u.get("object_key") or "").strip()
        filename = (u.get("filename") or reserved.get(idx, {}).get("filename") or "").strip()
        if idx not in reserved or reserved[idx]["object_key"] != key:
            return image_translate_flask_response(
                build_image_translate_error_response(f"uploaded item mismatch idx={idx}", 400)
            )
        if not local_media_storage.exists(key):
            return image_translate_flask_response(
                build_image_translate_error_response(f"uploaded object missing idx={idx}", 400)
            )
        items.append({
            "idx": idx,
            "filename": filename,
            "src_tos_key": key,
            "source_bucket": "upload",
        })
    if seen_idxs != set(reserved):
        return image_translate_flask_response(
            build_image_translate_error_response("uploaded items must exactly match reserved items", 400)
        )

    lang_name = _target_language_name(lang_code)
    final_prompt = prompt_tpl
    task_dir = ""
    project_name = _compose_project_name(product_name, preset, lang_name)
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=current_user.id,
        preset=preset,
        target_language=lang_code,
        target_language_name=lang_name,
        model_id=model_id,
        prompt=final_prompt,
        items=items,
        product_name=product_name,
        project_name=project_name,
        concurrency_mode=mode_raw,
        channel=channel,
    )
    with _upload_guard:
        _upload_reservations.pop(task_id, None)
    with _local_upload_guard:
        stale_upload_ids = [
            upload_id for upload_id, reservation in _local_upload_reservations.items()
            if reservation.get("task_id") == task_id and int(reservation.get("user_id") or 0) == int(current_user.id)
        ]
        for upload_id in stale_upload_ids:
            _local_upload_reservations.pop(upload_id, None)

    _start_runner(task_id, current_user.id)
    return image_translate_flask_response(
        build_image_translate_payload_response({"task_id": task_id}, status_code=201)
    )


@bp.route("/api/image-translate/<task_id>", methods=["GET"])
@login_required
def api_state(task_id: str):
    task = _get_viewable_task(task_id)
    return image_translate_flask_response(build_image_translate_payload_response(_state_payload(task)))


def _get_item(task: dict, idx: int) -> dict | None:
    for it in task.get("items") or []:
        if int(it.get("idx")) == int(idx):
            return it
    return None


@bp.route("/api/image-translate/<task_id>/artifact/source/<int:idx>", methods=["GET"])
@login_required
def api_source_artifact(task_id: str, idx: int):
    task = _get_viewable_task(task_id)
    item = _get_item(task, idx)
    if not item or not item.get("src_tos_key"):
        abort(404)
    return redirect(_signed_source_url(task, item))


@bp.route("/api/image-translate/<task_id>/artifact/result/<int:idx>", methods=["GET"])
@login_required
def api_result_artifact(task_id: str, idx: int):
    task = _get_viewable_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(_result_artifact_url(item["dst_tos_key"]))


@bp.route("/api/image-translate/<task_id>/download/result/<int:idx>", methods=["GET"])
@login_required
def api_download_result(task_id: str, idx: int):
    task = _get_viewable_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(_result_artifact_url(item["dst_tos_key"]))


@bp.route("/api/image-translate/<task_id>/retry/<int:idx>", methods=["POST"])
@login_required
def api_retry_item(task_id: str, idx: int):
    task = _get_retryable_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再重试", 409)
        )
    old_dst = (item.get("dst_tos_key") or "").strip()
    if old_dst:
        _delete_artifact_object(old_dst)
    _reset_item_processing_state(item)
    total = len(task["items"])
    done = sum(1 for it in task["items"] if it["status"] == "done")
    failed = sum(1 for it in task["items"] if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=task["items"],
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, _task_runner_user_id(task))
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {"task_id": task_id, "idx": idx, "status": "queued"},
            status_code=202,
        )
    )


@bp.route("/api/image-translate/<task_id>/banana-retry/<int:idx>", methods=["POST"])
@login_required
def api_banana_retry_item(task_id: str, idx: int):
    task = _get_retryable_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再重试", 409)
        )
    old_dst = (item.get("dst_tos_key") or "").strip()
    if old_dst:
        _delete_artifact_object(old_dst)
    _reset_item_processing_state(item)
    item["generation_channel_override"] = _BANANA_RETRY_CHANNEL
    item["generation_model_override"] = _BANANA_RETRY_MODEL
    item["generation_override_label"] = _BANANA_RETRY_LABEL
    total = len(task["items"])
    done = sum(1 for it in task["items"] if it["status"] == "done")
    failed = sum(1 for it in task["items"] if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=task["items"],
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, _task_runner_user_id(task))
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {
                "task_id": task_id,
                "idx": idx,
                "status": "queued",
                "channel": _BANANA_RETRY_CHANNEL,
                "model_id": _BANANA_RETRY_MODEL,
            },
            status_code=202,
        )
    )


@bp.route("/api/image-translate/<task_id>/use-source/<int:idx>", methods=["POST"])
@login_required
def api_use_source_item(task_id: str, idx: int):
    task = _get_retryable_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再使用原图", 409)
        )
    try:
        dst_key = _mark_item_as_copied_source_result(task, item)
    except FileNotFoundError:
        return image_translate_flask_response(
            build_image_translate_error_response("source image not found", 404)
        )
    _update_task_progress_from_items(task)

    update_payload = {
        "items": task.get("items") or [],
        "progress": task["progress"],
    }
    if task["progress"]["done"] == task["progress"]["total"]:
        task["status"] = "done"
        task.setdefault("steps", {})["process"] = "done"
        task["error"] = ""
        _auto_apply_if_all_done(task)
        update_payload.update({
            "status": "done",
            "steps": task.get("steps", {}),
            "error": "",
            "medias_context": task.get("medias_context") or {},
        })
    store.update(task_id, **update_payload)
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {
                "task_id": task_id,
                "idx": idx,
                "status": "done",
                "dst_tos_key": dst_key,
                "result_source": "copied_source",
            }
        )
    )


@bp.route("/api/image-translate/<task_id>/backfill-images", methods=["POST"])
@login_required
def api_backfill_images(task_id: str):
    task = _get_retryable_task(task_id)
    ctx = dict(task.get("medias_context") or {})
    if ctx.get("entry") != "medias_edit_detail":
        return image_translate_flask_response(
            build_image_translate_error_response("not a detail image translate task", 400)
        )
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再回填", 409)
        )
    items = task.get("items") or []
    if not items:
        return image_translate_flask_response(
            build_image_translate_error_response("no image items to backfill", 409)
        )

    fallback_count = 0
    try:
        for item in items:
            if item.get("status") == "done" and (item.get("dst_tos_key") or "").strip():
                continue
            _mark_item_as_copied_source_result(task, item)
            fallback_count += 1
    except FileNotFoundError as exc:
        return image_translate_flask_response(
            build_image_translate_error_response(str(exc) or "source image not found", 404)
        )

    _update_task_progress_from_items(task)
    task["status"] = "done"
    task.setdefault("steps", {})["process"] = "done"
    task["error"] = ""

    try:
        from appcore.image_translate_runtime import apply_translated_detail_images_from_task

        applied = apply_translated_detail_images_from_task(
            task,
            allow_partial=False,
            user_id=_task_runner_user_id(task),
        )
    except Exception as exc:
        ctx["apply_status"] = "apply_error"
        ctx["last_apply_error"] = str(exc)
        task["medias_context"] = ctx
        store.update(
            task_id,
            items=items,
            progress=task["progress"],
            status="done",
            steps=task.get("steps", {}),
            error="",
            medias_context=task.get("medias_context") or {},
        )
        return image_translate_flask_response(
            build_image_translate_error_response(str(exc) or "backfill failed", 409)
        )

    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="done",
        steps=task.get("steps", {}),
        error="",
        medias_context=task.get("medias_context") or {},
    )
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {
                "task_id": task_id,
                "status": "done",
                "fallback_source_count": fallback_count,
                "applied": len(applied.get("applied_ids") or []),
                "apply_status": applied.get("apply_status") or "",
                "applied_detail_image_ids": list(applied.get("applied_ids") or []),
            }
        )
    )


@bp.route("/api/image-translate/<task_id>/retry-failed", methods=["POST"])
@login_required
def api_retry_failed(task_id: str):
    """一键把该任务下所有 failed 项重置为 pending，重新入队跑一轮。"""
    task = _get_retryable_task(task_id)
    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if item.get("status") == "failed":
            _delete_artifact_object(item.get("dst_tos_key"))
            _reset_item_processing_state(item)
            reset_count += 1
    if reset_count == 0:
        return image_translate_flask_response(
            build_image_translate_error_response("当前没有失败项可重试", 409)
        )
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    failed = sum(1 for it in items if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, _task_runner_user_id(task))
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {"task_id": task_id, "reset": reset_count, "status": "queued"},
            status_code=202,
        )
    )


@bp.route("/api/image-translate/<task_id>/retry-unfinished", methods=["POST"])
@login_required
def api_retry_unfinished(task_id: str):
    """把所有非 done 的 item 重置为 pending 并重启 runner。
    与 retry-failed 的区别：范围不只是 failed，还包含 pending/running 僵尸。
    仅允许在 runner 不活跃时调用，避免与在跑的线程冲突。"""
    task = _get_retryable_task(task_id)
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再重试", 409)
        )
    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if item.get("status") == "done":
            continue
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            _delete_artifact_object(old_dst)
        _reset_item_processing_state(item)
        reset_count += 1
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    failed = sum(1 for it in items if it["status"] == "failed")
    if reset_count == 0:
        # 自愈：没有需要重试的 item，但任务状态可能卡在 interrupted/running
        # （start() 末尾的 status="done" 写入被服务重启打断）。若所有 item 都已
        # 成功完成，直接把任务级状态拉回 done，避免 UI 永远显示「中断」。
        if total > 0 and done == total and failed == 0 and task.get("status") != "done":
            task["progress"] = {"total": total, "done": done, "failed": 0, "running": 0}
            task["status"] = "done"
            steps = task.setdefault("steps", {})
            steps["process"] = "done"
            store.update(
                task_id,
                items=items,
                progress=task["progress"],
                status="done",
                steps=steps,
                error="",
            )
            return image_translate_flask_response(
                build_image_translate_payload_response(
                    {
                        "task_id": task_id,
                        "reset": 0,
                        "status": "done",
                        "healed": True,
                    }
                )
            )
        return image_translate_flask_response(
            build_image_translate_error_response("没有需要重试的图片", 409)
        )
    task["progress"] = {"total": total, "done": done, "failed": 0, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, _task_runner_user_id(task))
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {"task_id": task_id, "reset": reset_count, "status": "queued"},
            status_code=202,
        )
    )


@bp.route("/api/image-translate/<task_id>/rerun-unfinished", methods=["POST"])
@login_required
def api_rerun_unfinished_with_channel(task_id: str):
    task = _get_retryable_task(task_id)
    if image_translate_runner.is_running(task_id):
        return image_translate_flask_response(
            build_image_translate_error_response("任务正在跑，等跑完再重跑", 409)
        )

    body = request.get_json(silent=True) or {}
    channel = _requested_image_translate_channel(body.get("channel") or task.get("channel"))
    if not channel:
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported channel", 400)
        )
    model_id = (body.get("model_id") or task.get("model_id") or "").strip()
    if not is_valid_image_model(model_id, channel=channel):
        return image_translate_flask_response(
            build_image_translate_error_response("unsupported model", 400)
        )
    mode_raw = (
        body.get("concurrency_mode")
        or task.get("concurrency_mode")
        or task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE
    )
    concurrency_mode = str(mode_raw).strip().lower()
    if concurrency_mode not in {"sequential", "parallel"}:
        return image_translate_flask_response(
            build_image_translate_error_response("concurrency_mode must be sequential or parallel", 400)
        )
    concurrency_mode = _coerce_concurrency_mode_for_channel(concurrency_mode, channel)

    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if _item_has_success_result(item):
            continue
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            _delete_artifact_object(old_dst)
        _reset_item_processing_state(item)
        reset_count += 1
    if reset_count == 0:
        return image_translate_flask_response(
            build_image_translate_error_response("没有需要重跑的图片", 409)
        )

    task["channel"] = channel
    task["model_id"] = model_id
    task["concurrency_mode"] = concurrency_mode
    task["status"] = "queued"
    task["error"] = ""
    task.setdefault("steps", {})["process"] = "pending"
    task["progress"] = {
        "total": len(items),
        "done": sum(1 for it in items if _item_has_success_result(it)),
        "failed": 0,
        "running": 0,
    }
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
        channel=channel,
        model_id=model_id,
        concurrency_mode=concurrency_mode,
        steps=task.get("steps", {}),
        error="",
    )
    _start_runner(task_id, _task_runner_user_id(task))
    return image_translate_flask_response(
        build_image_translate_payload_response(
            {
                "task_id": task_id,
                "reset": reset_count,
                "status": "queued",
                "channel": channel,
                "model_id": model_id,
                "concurrency_mode": concurrency_mode,
            },
            status_code=202,
        )
    )


@bp.route("/api/image-translate/<task_id>/download/zip", methods=["GET"])
@login_required
def api_download_zip(task_id: str):
    task = _get_viewable_task(task_id)
    done_items = [it for it in (task.get("items") or [])
                  if it.get("status") == "done" and it.get("dst_tos_key")]
    if not done_items:
        abort(404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in done_items:
            key = it["dst_tos_key"]
            suffix = os.path.splitext(key)[1] or ".png"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="it_zip_")
            os.close(fd)
            try:
                _download_artifact_object(key, tmp_path)
                with open(tmp_path, "rb") as f:
                    raw = f.read()
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            base = os.path.splitext(
                os.path.basename(it.get("filename") or f"out_{it['idx']}")
            )[0] or "image"
            zf.writestr(f"{int(it['idx']):02d}_{base}{suffix}", raw)
    buf.seek(0)

    filename = f"{task_id}-{task.get('target_language') or 'result'}.zip"
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/image-translate", methods=["GET"])
@login_required
@permission_required("image_translate")
def page_list():
    import json as _json

    tab = (request.args.get("tab") or "all").strip().lower()

    # 列出所有任务（管理员视角）或仅当前用户的任务
    if _is_superadmin_user():
        rows = image_translate_store.list_all_projects(query_func=db_query)
    else:
        rows = image_translate_store.list_user_projects(
            current_user.id,
            query_func=db_query,
        )

    _STATUS_LABELS = {
        "queued": "排队中",
        "running": "运行中",
        "done": "完成",
        "error": "失败",
        "interrupted": "中断",
    }

    def _classify_task(row, state):
        """判断任务类型：
        - 'normal': 普通图片翻译（不从素材管理创建）
        - 'product_detail': 商品详情图翻译（从素材管理编辑页创建）
        - 'video_cover': 视频封面图翻译（从素材管理翻译按钮创建）
        """
        ctx = state.get("medias_context") or {}
        entry = ctx.get("entry") or ""
        preset = state.get("preset") or ""
        if entry == "medias_edit_detail" and preset == "detail":
            return "product_detail"
        if entry in ("medias_edit_detail", "medias_cover_translate") and preset == "cover":
            return "video_cover"
        return "normal"

    history_all = []
    history_product_detail = []
    history_video_cover = []

    for row in rows or []:
        state = {}
        try:
            state = _json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        items = state.get("items") or []
        done = sum(1 for it in items if it.get("status") == "done")
        preset = state.get("preset") or ""
        preset_label = "封面图翻译" if preset == "cover" else ("产品详情图翻译" if preset == "detail" else "")
        raw_status = row.get("status") or state.get("status") or ""
        concurrency_mode = _normalize_concurrency_mode(state.get("concurrency_mode"))
        channel = (state.get("channel") or "").strip().lower()
        if channel not in its.CHANNELS:
            channel = _safe_image_translate_channel()
        task_type = _classify_task(row, state)
        task_item = {
            "id": row["id"],
            "created_at": row.get("created_at"),
            "status": raw_status,
            "status_label": _STATUS_LABELS.get(raw_status, raw_status),
            "is_interrupted": raw_status == "interrupted",
            "preset": preset,
            "preset_label": preset_label,
            "target_language_name": state.get("target_language_name") or "",
            "project_name": state.get("project_name") or "",
            "product_name": state.get("product_name") or "",
            "channel": channel,
            "channel_label": _channel_label(channel),
            "model_id": state.get("model_id") or "",
            "concurrency_mode": concurrency_mode,
            "concurrency_mode_label": _concurrency_mode_label(concurrency_mode),
            "total": len(items),
            "done": done,
            "user_id": row.get("user_id") if _is_superadmin_user() else None,
            "task_type": task_type,
        }
        history_all.append(task_item)
        if task_type == "product_detail":
            history_product_detail.append(task_item)
        elif task_type == "video_cover":
            history_video_cover.append(task_item)

    if tab == "product_detail":
        history = history_product_detail
    elif tab == "video_cover":
        history = history_video_cover
    else:
        history = history_all

    default_channel = _safe_image_translate_channel()
    return render_template(
        "image_translate_list.html",
        history=history,
        tab=tab,
        gemini_backend=_backend_badge(default_channel),
        image_translate_channels=_image_translate_channels_payload(),
        image_translate_default_channel=default_channel,
        is_admin=_is_superadmin_user(),
    )


@bp.route("/image-translate/<task_id>", methods=["GET"])
@login_required
def page_detail(task_id: str):
    task = _get_viewable_task(task_id)
    return render_template(
        "image_translate_detail.html",
        task_id=task_id,
        state=_state_payload(task),
        gemini_backend=_backend_badge(task.get("channel")),
        image_translate_channels=_image_translate_channels_payload(),
    )


@bp.route("/api/image-translate/<task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: str):
    task = _get_owned_task(task_id)
    for it in task.get("items") or []:
        # 源图：来自 media bucket 的是素材库永久资源，不得清理；只清 upload bucket 里任务自己上传的 src。
        src_key = (it.get("src_tos_key") or "").strip()
        if src_key and _resolve_source_bucket(task, it) != "media":
            _delete_artifact_object(src_key)
        # 输出始终是 upload bucket 的 artifacts/ 路径，删除任务时可以一起清。
        dst_key = (it.get("dst_tos_key") or "").strip()
        if dst_key:
            _delete_artifact_object(dst_key)
    try:
        image_translate_store.soft_delete_project(
            task_id,
            current_user.id,
            execute_func=db_execute,
        )
    except Exception:
        pass
    store.update(task_id, status="deleted",
                 deleted_at=datetime.now().isoformat(timespec="seconds"))
    return ("", 204)
