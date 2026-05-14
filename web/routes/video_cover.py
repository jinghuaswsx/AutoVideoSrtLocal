from __future__ import annotations

from datetime import date
import json
import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from appcore import local_media_storage, video_cover_project_store
from appcore.project_state import save_project_state
from appcore.settings import get_retention_hours
from appcore.task_recovery import recover_project_if_needed
from appcore.video_cover_generation import (
    VideoCoverGenerationError,
    generate_ad_copy_sets,
    generate_product_analysis,
    generate_video_analysis,
    generate_video_covers,
    resolve_cover_model_selection,
    resolve_text_model_selection,
    video_cover_model_options,
)
from appcore.video_cover_generation import _fetch_product_image, _product_value
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail, probe_media_info
from web.auth import admin_required
from web.services.video_cover_responses import VideoCoverResponse, video_cover_flask_response
from web.upload_util import (
    client_filename_basename,
    save_uploaded_file_to_path,
    secure_filename_component,
    validate_video_extension,
)


bp = Blueprint("video_cover", __name__)

STEP_ORDER = ("video_analysis", "product_analysis", "ad_copy", "cover_generation")
STEP_LABELS = {
    "video_analysis": "视频分析",
    "product_analysis": "产品分析",
    "ad_copy": "文案创作",
    "cover_generation": "封面生成",
}
STEP_OUTPUT_KEYS = {
    "video_analysis": ("video_analysis",),
    "product_analysis": ("product_analysis",),
    "ad_copy": ("ad_copy_sets",),
    "cover_generation": ("result",),
}


def _artifact_url(object_key: str) -> str:
    return url_for("medias.media_object_proxy", object_key=object_key)


def _attach_urls(payload: dict) -> dict:
    data = dict(payload)
    reference = dict(data.get("reference") or {})
    if reference.get("object_key") and not reference.get("url"):
        reference["url"] = _artifact_url(reference["object_key"])
    data["reference"] = reference

    covers = []
    for cover in data.get("covers") or []:
        row = dict(cover)
        if row.get("object_key") and not row.get("url"):
            row["url"] = _artifact_url(row["object_key"])
        covers.append(row)
    data["covers"] = covers
    return data


def _json_or_form(name: str) -> str:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return str(payload.get(name) or "").strip()
    return str(request.form.get(name) or "").strip()


def _parse_ad_copy_payload(raw: str | None) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _save_upload_to_temp(work_dir: str):
    upload = request.files.get("video_file")
    if not upload or not (upload.filename or "").strip():
        raise VideoCoverGenerationError("请上传视频文件")
    filename = upload.filename or "video.mp4"
    suffix = Path(filename).suffix or ".mp4"
    video_path = Path(work_dir) / f"source{suffix}"
    upload.save(video_path)
    return filename, video_path


def _extract_product(product_url: str):
    product = fetch_product_analysis(product_url)
    title = _product_value(product, "title")
    image_url = _product_value(product, "main_image_url")
    if not title:
        raise VideoCoverGenerationError("无法从商品链接提取商品标题")
    if not image_url:
        raise VideoCoverGenerationError("无法从商品链接提取商品主图")
    return product, title, image_url


def _product_payload(product_url: str, title: str, image_url: str) -> dict:
    return {
        "title": title,
        "main_image_url": image_url,
        "product_url": product_url,
    }


def _json_response(payload: dict, status: int = 200):
    return video_cover_flask_response(VideoCoverResponse(payload, status))


def _parse_state(row: dict | None) -> dict:
    if not row:
        return {}
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    return state if isinstance(state, dict) else {}


def _is_admin_user() -> bool:
    return getattr(current_user, "is_admin", False)


def _load_user_project(task_id: str) -> tuple[dict | None, dict]:
    row = video_cover_project_store.get_project(
        task_id,
        user_id=int(current_user.id),
        is_admin=_is_admin_user(),
    )
    return row, _parse_state(row)


def _validate_product_url(value: str) -> str:
    product_url = (value or "").strip()
    parsed = urlparse(product_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoCoverGenerationError("请输入有效的商品链接")
    return product_url


def _initial_steps() -> dict[str, str]:
    return {step: "pending" for step in STEP_ORDER}


def _initial_state(
    *,
    task_id: str,
    user_id: int,
    product_url: str,
    video_path: str,
    video_filename: str,
    task_dir: str,
    display_name: str,
    thumbnail_path: str | None,
) -> dict:
    return {
        "id": task_id,
        "type": video_cover_project_store.VIDEO_COVER_TYPE,
        "status": "uploaded",
        "user_id": user_id,
        "display_name": display_name,
        "product_url": product_url,
        "video_path": video_path,
        "video_filename": video_filename,
        "task_dir": task_dir,
        "thumbnail_path": thumbnail_path or "",
        "steps": _initial_steps(),
        "step_messages": {step: "" for step in STEP_ORDER},
        "models": {},
    }


def _step_index(step: str) -> int:
    try:
        return STEP_ORDER.index(step)
    except ValueError as exc:
        raise VideoCoverGenerationError(f"未知步骤：{step}") from exc


def _ensure_previous_steps_done(state: dict, step: str) -> None:
    steps = state.get("steps") or {}
    for previous in STEP_ORDER[:_step_index(step)]:
        if steps.get(previous) != "done":
            raise VideoCoverGenerationError(f"请先完成{STEP_LABELS[previous]}")


def _clear_step_outputs(state: dict, step: str) -> None:
    for affected in STEP_ORDER[_step_index(step):]:
        if affected != step:
            state.setdefault("steps", {})[affected] = "pending"
            state.setdefault("step_messages", {})[affected] = ""
        for key in STEP_OUTPUT_KEYS.get(affected, ()):
            state.pop(key, None)


def _save_state(task_id: str, state: dict, *, status: str) -> None:
    state["status"] = status
    save_project_state(task_id, state, status=status)


def _cover_by_platform(state: dict, platform: str) -> dict | None:
    result = state.get("result") or {}
    for cover in result.get("covers") or []:
        if str(cover.get("platform") or "") == platform:
            return cover
    return None


def _state_with_urls(task_id: str, state: dict) -> dict:
    view_state = dict(state)
    result = dict(view_state.get("result") or {})
    covers = []
    for cover in result.get("covers") or []:
        row = dict(cover)
        if row.get("object_key") and not row.get("url"):
            row["url"] = _artifact_url(row["object_key"])
        if row.get("platform"):
            row["download_url"] = url_for("video_cover.download_cover", task_id=task_id, platform=row["platform"])
        covers.append(row)
    result["covers"] = covers
    reference = dict(result.get("reference") or {})
    if reference.get("object_key") and not reference.get("url"):
        reference["url"] = _artifact_url(reference["object_key"])
    result["reference"] = reference
    view_state["result"] = result
    return view_state


def _run_video_analysis_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    product_url = _validate_product_url(state.get("product_url") or "")
    _product, title, image_url = _extract_product(product_url)
    selection = resolve_text_model_selection("video_analysis", provider, model)
    analysis = generate_video_analysis(
        video_path=str(state.get("video_path") or ""),
        product_title=title,
        product_url=product_url,
        main_image_url=image_url,
        video_info=probe_media_info(str(state.get("video_path") or "")),
        provider=selection.provider,
        model=selection.alias,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["product"] = _product_payload(product_url, title, image_url)
    state["video_analysis"] = analysis
    state.setdefault("models", {})["video_analysis"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"product": state["product"], "video_analysis": analysis}


def _run_product_analysis_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    product_url = _validate_product_url(state.get("product_url") or "")
    product, title, image_url = _extract_product(product_url)
    selection = resolve_text_model_selection("product_analysis", provider, model)
    with tempfile.TemporaryDirectory(prefix="video_cover_product_") as work_dir:
        image_bytes = _fetch_product_image(image_url)
        product_image_path = Path(work_dir) / "product_image.png"
        product_image_path.write_bytes(image_bytes)
        product_analysis = generate_product_analysis(
            product=product,
            product_title=title,
            main_image_url=image_url,
            product_image_path=product_image_path,
            provider=selection.provider,
            model=selection.alias,
            user_id=user_id,
            task_id=state.get("id"),
        )
    state["product"] = _product_payload(product_url, title, image_url)
    state["product_analysis"] = product_analysis
    state.setdefault("models", {})["product_analysis"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"product": state["product"], "product_analysis": product_analysis}


def _run_ad_copy_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    selection = resolve_text_model_selection("ad_copy", provider, model)
    ad_copy_sets = generate_ad_copy_sets(
        product_analysis=str(state.get("product_analysis") or ""),
        video_analysis=str(state.get("video_analysis") or ""),
        current_date=(request.form.get("current_date") or date.today().isoformat()),
        provider=selection.provider,
        model=selection.alias,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["ad_copy_sets"] = ad_copy_sets
    state.setdefault("models", {})["ad_copy"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"ad_copy_sets": ad_copy_sets}


def _run_cover_generation_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    selection = resolve_cover_model_selection(provider, model)
    result = generate_video_covers(
        product_url=str(state.get("product_url") or ""),
        video_path=str(state.get("video_path") or ""),
        video_filename=str(state.get("video_filename") or "video.mp4"),
        user_id=user_id,
        task_id=state.get("id"),
        cover_provider=selection.provider,
        cover_model=selection.alias,
        product_analysis_text=str(state.get("product_analysis") or ""),
        video_analysis_text=str(state.get("video_analysis") or ""),
        ad_copy_payload=state.get("ad_copy_sets") if isinstance(state.get("ad_copy_sets"), dict) else None,
    )
    state["result"] = result
    state["inputs"] = result.get("inputs") or {}
    state.setdefault("models", {}).update(result.get("models") or {})
    return _attach_urls(result)


def _run_project_step(state: dict, step: str, *, provider: str | None, model: str | None, user_id: int) -> dict:
    if step == "video_analysis":
        return _run_video_analysis_step(state, provider=provider, model=model, user_id=user_id)
    if step == "product_analysis":
        return _run_product_analysis_step(state, provider=provider, model=model, user_id=user_id)
    if step == "ad_copy":
        return _run_ad_copy_step(state, provider=provider, model=model, user_id=user_id)
    if step == "cover_generation":
        return _run_cover_generation_step(state, provider=provider, model=model, user_id=user_id)
    raise VideoCoverGenerationError(f"未知步骤：{step}")


@bp.route("/video-cover", methods=["GET"])
@login_required
@admin_required
def page():
    projects = video_cover_project_store.list_projects(
        user_id=int(current_user.id),
        is_admin=_is_admin_user(),
    )
    return render_template("video_cover_list.html", projects=projects)


@bp.route("/video-cover/<task_id>", methods=["GET"])
@login_required
@admin_required
def detail_page(task_id: str):
    recover_project_if_needed(task_id, video_cover_project_store.VIDEO_COVER_TYPE)
    row, state = _load_user_project(task_id)
    if not row:
        return "Not Found", 404
    view_state = _state_with_urls(task_id, state)
    return render_template(
        "video_cover_detail.html",
        project=row,
        state=view_state,
        task_id=task_id,
        model_options=video_cover_model_options(),
        step_order=STEP_ORDER,
        step_labels=STEP_LABELS,
    )


@bp.route("/video-cover/api/projects", methods=["POST"])
@login_required
@admin_required
def api_create_project():
    try:
        product_url = _validate_product_url(request.form.get("product_url") or "")
        upload = request.files.get("video_file")
        if not upload or not (upload.filename or "").strip():
            raise VideoCoverGenerationError("请上传视频文件")
        original_filename = client_filename_basename(upload.filename)
        if not validate_video_extension(original_filename):
            raise VideoCoverGenerationError("不支持的视频格式")

        task_id = uuid.uuid4().hex
        task_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe_name = secure_filename_component(original_filename)
        video_path = os.path.join(UPLOAD_DIR, f"{task_id}_video_{safe_name}")
        save_uploaded_file_to_path(upload, video_path)

        thumbnail_path = ""
        try:
            thumbnail_path = extract_thumbnail(video_path, task_dir, scale="360:-2") or ""
        except Exception:
            thumbnail_path = ""

        display_name = Path(original_filename).stem or "video-cover"
        state = _initial_state(
            task_id=task_id,
            user_id=int(current_user.id),
            product_url=product_url,
            video_path=video_path,
            video_filename=original_filename,
            task_dir=task_dir,
            display_name=display_name,
            thumbnail_path=thumbnail_path,
        )
        video_cover_project_store.insert_project(
            task_id=task_id,
            user_id=int(current_user.id),
            original_filename=original_filename,
            display_name=display_name,
            thumbnail_path=thumbnail_path,
            task_dir=task_dir,
            state=state,
            retention_hours=get_retention_hours(video_cover_project_store.VIDEO_COVER_TYPE),
        )
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    return _json_response({"ok": True, "id": task_id}, 201)


@bp.route("/video-cover/api/<task_id>/run/<step>", methods=["POST"])
@login_required
@admin_required
def api_run_project_step(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    state.setdefault("id", task_id)
    state.setdefault("steps", _initial_steps())
    state.setdefault("step_messages", {name: "" for name in STEP_ORDER})
    started = False
    try:
        _ensure_previous_steps_done(state, step)
        _clear_step_outputs(state, step)
        state["steps"][step] = "running"
        state["step_messages"][step] = "运行中..."
        _save_state(task_id, state, status="running")
        started = True
        data = _run_project_step(
            state,
            step,
            provider=request.form.get("provider"),
            model=request.form.get("model"),
            user_id=int(row.get("user_id") or current_user.id),
        )
        state["steps"][step] = "done"
        state["step_messages"][step] = "已完成"
        status = "done" if step == "cover_generation" else "running"
        _save_state(task_id, state, status=status)
    except VideoCoverGenerationError as exc:
        message = str(exc)
        if started and step in STEP_ORDER:
            state.setdefault("steps", {})[step] = "error"
            state.setdefault("step_messages", {})[step] = message
            state["error"] = message
            _save_state(task_id, state, status="error")
        status_code = 502 if message.startswith(("封面生成失败", "文案创作失败", "产品分析失败", "视频分析失败")) else 400
        return _json_response({"ok": False, "error": message}, status_code)
    return _json_response({"ok": True, "data": data, "state": _state_with_urls(task_id, state)})


@bp.route("/video-cover/api/<task_id>/download/<platform>", methods=["GET"])
@login_required
@admin_required
def download_cover(task_id: str, platform: str):
    row, state = _load_user_project(task_id)
    if not row:
        return "Not Found", 404
    cover = _cover_by_platform(state, platform)
    object_key = str((cover or {}).get("object_key") or "")
    if not object_key:
        return "Not Found", 404
    try:
        path = local_media_storage.safe_local_path_for(object_key)
        if not path.is_file():
            local_media_storage.download_to(object_key, path)
    except Exception:
        return "Not Found", 404
    if not path.is_file():
        return "Not Found", 404
    return send_file(path, mimetype="image/png", as_attachment=True, download_name=f"{platform}.png")


@bp.route("/video-cover/api/product-analysis", methods=["POST"])
@login_required
@admin_required
def api_product_analysis():
    product_url = (request.form.get("product_url") or "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_product_") as work_dir:
            product, title, image_url = _extract_product(product_url)
            image_bytes = _fetch_product_image(image_url)
            product_image_path = Path(work_dir) / "product_image.png"
            product_image_path.write_bytes(image_bytes)
            product_analysis = generate_product_analysis(
                product=product,
                product_title=title,
                main_image_url=image_url,
                product_image_path=product_image_path,
                provider=request.form.get("provider") or request.form.get("product_provider"),
                model=request.form.get("model") or request.form.get("product_model"),
                user_id=int(getattr(current_user, "id", 0) or 0),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("产品分析失败") else 400
        return _json_response({"ok": False, "error": message}, status_code)
    return _json_response({
        "ok": True,
        "data": {
            "product": _product_payload(product_url, title, image_url),
            "product_analysis": product_analysis,
        },
    })


@bp.route("/video-cover/api/video-analysis", methods=["POST"])
@login_required
@admin_required
def api_video_analysis():
    product_url = (request.form.get("product_url") or "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_video_") as work_dir:
            filename, video_path = _save_upload_to_temp(work_dir)
            product, title, image_url = _extract_product(product_url)
            video_analysis = generate_video_analysis(
                video_path=str(video_path),
                product_title=title,
                product_url=product_url,
                main_image_url=image_url,
                video_info=probe_media_info(str(video_path)),
                provider=request.form.get("provider") or request.form.get("video_provider"),
                model=request.form.get("model") or request.form.get("video_model"),
                user_id=int(getattr(current_user, "id", 0) or 0),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("视频分析失败") else 400
        return _json_response({"ok": False, "error": message}, status_code)
    return _json_response({
        "ok": True,
        "data": {
            "product": _product_payload(product_url, title, image_url),
            "video_filename": filename,
            "video_analysis": video_analysis,
        },
    })


@bp.route("/video-cover/api/ad-copy", methods=["POST"])
@login_required
@admin_required
def api_ad_copy():
    try:
        ad_copy_sets = generate_ad_copy_sets(
            product_analysis=_json_or_form("product_analysis"),
            video_analysis=_json_or_form("video_analysis"),
            current_date=_json_or_form("current_date") or date.today().isoformat(),
            provider=_json_or_form("provider") or _json_or_form("ad_copy_provider"),
            model=_json_or_form("model") or _json_or_form("ad_copy_model"),
            user_id=int(getattr(current_user, "id", 0) or 0),
        )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("文案创作失败") else 400
        return _json_response({"ok": False, "error": message}, status_code)
    return _json_response({"ok": True, "data": {"ad_copy_sets": ad_copy_sets}})


@bp.route("/video-cover/api/generate", methods=["POST"])
@login_required
@admin_required
def api_generate():
    product_url = (request.form.get("product_url") or "").strip()
    upload = request.files.get("video_file")
    if not upload or not (upload.filename or "").strip():
        return _json_response({"ok": False, "error": "请上传视频文件"}, 400)

    filename = upload.filename or "video.mp4"
    suffix = Path(filename).suffix or ".mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_upload_") as work_dir:
            video_path = Path(work_dir) / f"source{suffix}"
            upload.save(video_path)
            result = generate_video_covers(
                product_url=product_url,
                video_path=str(video_path),
                video_filename=filename,
                user_id=int(getattr(current_user, "id", 0) or 0),
                cover_provider=request.form.get("cover_provider") or request.form.get("provider") or "",
                cover_model=request.form.get("cover_model") or request.form.get("model") or "",
                product_analysis_provider=request.form.get("product_provider") or "",
                product_analysis_model=request.form.get("product_model") or "",
                video_analysis_provider=request.form.get("video_provider") or "",
                video_analysis_model=request.form.get("video_model") or "",
                ad_copy_provider=request.form.get("ad_copy_provider") or "",
                ad_copy_model=request.form.get("ad_copy_model") or "",
                product_analysis_text=request.form.get("product_analysis") or "",
                video_analysis_text=request.form.get("video_analysis") or "",
                ad_copy_payload=_parse_ad_copy_payload(request.form.get("ad_copy_sets")),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith(("封面生成失败", "文案创作失败", "产品分析失败", "视频分析失败")) else 400
        return _json_response({"ok": False, "error": message}, status_code)

    return _json_response({"ok": True, "data": _attach_urls(result)})
