from __future__ import annotations

from datetime import date
import json
import mimetypes
import os
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from appcore import local_media_storage, video_cover_project_store, video_cover_settings
from appcore.project_state import save_project_state
from appcore.settings import get_retention_hours
from appcore.task_recovery import try_register_active_task, unregister_active_task
from appcore.video_cover_generation import (
    SOCIAL_REELS_SPEC,
    VideoCoverGenerationError,
    build_ad_copy_prompt,
    build_platform_prompt,
    build_product_analysis_prompt,
    build_video_analysis_prompt,
    generate_ad_copy_sets,
    generate_product_analysis,
    generate_video_analysis,
    generate_video_covers,
    normalize_image_count,
    normalize_product_image_jpg,
    resolve_cover_model_selection,
    resolve_text_model_selection,
    video_cover_model_options,
)
from appcore.video_cover_generation import _fetch_product_image, _product_value
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail, probe_media_info
from web.auth import admin_required, superadmin_required
from web.background import start_background_task
from web.services.video_cover_responses import VideoCoverResponse, video_cover_flask_response
from web.upload_util import (
    client_filename_basename,
    save_uploaded_file_to_path,
    secure_filename_component,
    validate_video_extension,
)


bp = Blueprint("video_cover", __name__)

STEP_ORDER = ("video_analysis", "product_analysis", "ad_copy", "cover_generation")
DEFAULT_IMAGE_COUNT = 4
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


def time_time() -> float:
    return time.time()


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


def _product_payload(product_url: str, title: str, image_url: str, product_image_path: str = "") -> dict:
    payload = {
        "title": title,
        "main_image_url": image_url,
        "product_url": product_url,
    }
    if product_image_path:
        payload["product_image_path"] = product_image_path
    return payload


def _save_product_image_asset(image_url: str, task_dir: str) -> str:
    os.makedirs(task_dir, exist_ok=True)
    normalized = normalize_product_image_jpg(_fetch_product_image(image_url))
    path = Path(task_dir) / "product_main.jpg"
    path.write_bytes(normalized)
    return str(path)


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
    product_title: str,
    main_image_url: str,
    product_image_path: str,
    image_count: int = DEFAULT_IMAGE_COUNT,
    model_defaults: dict | None = None,
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
        "product": _product_payload(product_url, product_title, main_image_url, product_image_path),
        "image_count": normalize_image_count(image_count, default=DEFAULT_IMAGE_COUNT),
        "model_defaults": video_cover_settings.normalize_model_defaults(model_defaults),
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
        for container in ("step_requests", "step_results", "step_timing"):
            if isinstance(state.get(container), dict):
                state[container].pop(affected, None)


def _clear_all_outputs(state: dict) -> None:
    preserved_keys = (
        "id",
        "type",
        "status",
        "user_id",
        "display_name",
        "product_url",
        "video_path",
        "video_filename",
        "task_dir",
        "thumbnail_path",
        "product",
        "image_count",
        "model_defaults",
    )
    preserved = {key: state[key] for key in preserved_keys if key in state}
    state.clear()
    state.update(preserved)
    state["steps"] = _initial_steps()
    state["step_messages"] = {step: "" for step in STEP_ORDER}
    state["status"] = "running"


def _mark_step_running(state: dict, step: str) -> None:
    now = time_time()
    state.setdefault("steps", {})[step] = "running"
    state.setdefault("step_messages", {})[step] = "运行中..."
    state.setdefault("step_timing", {})[step] = {"started_at": now}


def _mark_step_done(state: dict, step: str) -> None:
    now = time_time()
    timing = state.setdefault("step_timing", {}).setdefault(step, {})
    started = float(timing.get("started_at") or now)
    timing["finished_at"] = now
    timing["elapsed_seconds"] = max(0, int(round(now - started)))
    state.setdefault("steps", {})[step] = "done"
    state.setdefault("step_messages", {})[step] = f"已完成，耗时 {timing['elapsed_seconds']} 秒"


def _mark_step_error(state: dict, step: str, message: str) -> None:
    now = time_time()
    timing = state.setdefault("step_timing", {}).setdefault(step, {})
    started = float(timing.get("started_at") or now)
    timing["finished_at"] = now
    timing["elapsed_seconds"] = max(0, int(round(now - started)))
    state.setdefault("steps", {})[step] = "error"
    state.setdefault("step_messages", {})[step] = message
    state["error"] = message


def _with_runtime_timing(state: dict) -> dict:
    view_state = dict(state)
    timings = {}
    now = time_time()
    steps = view_state.get("steps") or {}
    for step, timing in (view_state.get("step_timing") or {}).items():
        row = dict(timing or {})
        if steps.get(step) == "running" and row.get("started_at"):
            row["running_seconds"] = max(0, int(round(now - float(row["started_at"]))))
        timings[step] = row
    view_state["step_timing"] = timings
    return view_state


def _store_step_request(state: dict, step: str, payload: dict) -> None:
    state.setdefault("step_requests", {})[step] = payload


def _store_step_result(state: dict, step: str, raw_response, structured_result) -> None:
    state.setdefault("step_results", {})[step] = {
        "raw_response": raw_response,
        "structured_result": structured_result,
    }


def _strip_wrapping_tags(text: str, outer_tag: str) -> str:
    raw = str(text or "").strip()
    raw = raw.replace(f"<{outer_tag}>", "").replace(f"</{outer_tag}>", "")
    return raw.strip()


def _parse_label_lines(text: str, mapping: dict[str, str]) -> dict:
    result: dict[str, str] = {}
    current_key = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        matched = False
        for label, key in mapping.items():
            prefixes = (f"{label}：", f"{label}:")
            if stripped.startswith(prefixes):
                current_key = key
                result[key] = stripped.split("：", 1)[-1] if "：" in stripped else stripped.split(":", 1)[-1]
                result[key] = result[key].strip()
                matched = True
                break
        if not matched and current_key:
            result[current_key] = (result.get(current_key, "") + "\n" + stripped).strip()
    return result


def _json_or_text_structured(raw_response) -> dict:
    if isinstance(raw_response, dict):
        return raw_response
    text = str(raw_response or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"summary": text}


def _structured_video_analysis(raw_response) -> dict:
    parsed = _json_or_text_structured(raw_response)
    if parsed.get("summary") and len(parsed) == 1:
        body = _strip_wrapping_tags(parsed["summary"], "视频素材分析")
        fields = _parse_label_lines(
            body,
            {
                "video_text": "video_text",
                "voiceover": "voiceover",
                "cover_reference": "cover_reference",
                "动作与使用方式": "actions",
                "拍摄角度与构图": "composition",
                "真实感线索": "authenticity_cues",
                "需要忽略的元素": "ignore_elements",
                "对封面生成的建议": "cover_suggestions",
            },
        )
        return fields or parsed
    return parsed


def _structured_product_analysis(raw_response) -> dict:
    parsed = _json_or_text_structured(raw_response)
    if parsed.get("summary") and len(parsed) == 1:
        body = _strip_wrapping_tags(parsed["summary"], "产品分析报告")
        fields = _parse_label_lines(
            body,
            {
                "<信息完整性检查>": "information_check",
                "<产品定义>": "product_definition",
                "<核心功能>": "core_functions",
                "<使用方式解析>": "usage_analysis",
                "<物理特征>": "physical_features",
                "<欧美本地化场景建议>": "western_scene_suggestions",
                "<视觉生成分类与封面策略>": "visual_category",
                "<封面画面决策>": "cover_decision",
                "<广告文案方向>": "ad_copy_direction",
                "<综合判断>": "overall_judgment",
            },
        )
        return fields or parsed
    return parsed


def _save_state(task_id: str, state: dict, *, status: str) -> None:
    state["status"] = status
    save_project_state(task_id, state, status=status)


def _state_product(state: dict) -> dict:
    product = state.get("product")
    return product if isinstance(product, dict) else {}


def _state_product_image_path(state: dict) -> str:
    product = _state_product(state)
    return str(product.get("product_image_path") or state.get("product_image_path") or "").strip()


def _ensure_product_assets(state: dict, *, fetch_product: bool = False) -> tuple[object, str, str, str]:
    product_url = _validate_product_url(state.get("product_url") or "")
    stored = _state_product(state)
    title = str(stored.get("title") or "").strip()
    image_url = str(stored.get("main_image_url") or "").strip()
    product: object | None = None
    if fetch_product or not title or not image_url:
        product, title, image_url = _extract_product(product_url)
    else:
        product = {"title": title, "main_image_url": image_url, "product_url": product_url}
    product_image_path = _state_product_image_path(state)
    if not product_image_path or not Path(product_image_path).is_file():
        task_dir = str(state.get("task_dir") or tempfile.mkdtemp(prefix="video_cover_product_"))
        product_image_path = _save_product_image_asset(image_url, task_dir)
    state["product"] = _product_payload(product_url, title, image_url, product_image_path)
    return product, title, image_url, product_image_path


def _project_model_defaults(state: dict) -> dict[str, dict[str, str]]:
    defaults = state.get("model_defaults")
    if not isinstance(defaults, dict):
        defaults = video_cover_settings.get_model_defaults()
    normalized = video_cover_settings.normalize_model_defaults(defaults)
    state["model_defaults"] = normalized
    return normalized


def _step_model_default(state: dict, step: str) -> dict[str, str]:
    return _project_model_defaults(state).get(step) or {}


def _cover_by_platform(state: dict, platform: str) -> dict | None:
    result = state.get("result") or {}
    for cover in result.get("covers") or []:
        if str(cover.get("platform") or "") == platform:
            return cover
    return None


def _state_with_urls(task_id: str, state: dict) -> dict:
    view_state = _with_runtime_timing(state)
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
    _product, title, image_url, product_image_path = _ensure_product_assets(state)
    selection = resolve_text_model_selection("video_analysis", provider, model)
    video_info = probe_media_info(str(state.get("video_path") or ""))
    _store_step_request(state, "video_analysis", {
        "provider": selection.provider,
        "model": selection.model,
        "alias": selection.alias,
        "request_data": {
            "product_title": title,
            "product_url": product_url,
            "main_image_url": image_url,
            "video_info": video_info,
            "media": {
                "video_path": str(state.get("video_path") or ""),
                "product_image_path": product_image_path,
            },
        },
        "prompt": build_video_analysis_prompt(
            product_title=title,
            product_url=product_url,
            main_image_url=image_url,
            video_info=video_info,
        ),
    })
    analysis = generate_video_analysis(
        video_path=str(state.get("video_path") or ""),
        product_title=title,
        product_url=product_url,
        main_image_url=image_url,
        product_image_path=product_image_path,
        video_info=video_info,
        provider=selection.provider,
        model=selection.alias,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["video_analysis"] = analysis
    structured = _structured_video_analysis(analysis)
    state["video_analysis_structured"] = structured
    _store_step_result(state, "video_analysis", analysis, structured)
    state.setdefault("models", {})["video_analysis"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"product": state["product"], "video_analysis": analysis, "structured_result": structured}


def _run_product_analysis_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    product_url = _validate_product_url(state.get("product_url") or "")
    product, title, image_url, product_image_path = _ensure_product_assets(state, fetch_product=True)
    selection = resolve_text_model_selection("product_analysis", provider, model)
    _store_step_request(state, "product_analysis", {
        "provider": selection.provider,
        "model": selection.model,
        "alias": selection.alias,
        "request_data": {
            "product_title": title,
            "product_url": product_url,
            "main_image_url": image_url,
            "product_image_path": product_image_path,
        },
        "prompt": build_product_analysis_prompt(
            product,
            product_title=title,
            main_image_url=image_url,
        ),
    })
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
    state["product_analysis"] = product_analysis
    structured = _structured_product_analysis(product_analysis)
    state["product_analysis_structured"] = structured
    _store_step_result(state, "product_analysis", product_analysis, structured)
    state.setdefault("models", {})["product_analysis"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"product": state["product"], "product_analysis": product_analysis, "structured_result": structured}


def _run_ad_copy_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    selection = resolve_text_model_selection("ad_copy", provider, model)
    product = _state_product(state)
    current_date = date.today().isoformat()
    _store_step_request(state, "ad_copy", {
        "provider": selection.provider,
        "model": selection.model,
        "alias": selection.alias,
        "request_data": {
            "product_title": product.get("title") or "",
            "main_image_url": product.get("main_image_url") or "",
            "product_analysis": state.get("product_analysis") or "",
            "video_analysis": state.get("video_analysis") or "",
            "current_date": current_date,
        },
        "messages": [
            {"role": "system", "content": "你只输出一个合法 JSON 对象，不输出解释、Markdown 或代码块。"},
            {
                "role": "user",
                "content": build_ad_copy_prompt(
                    product_title=str(product.get("title") or ""),
                    main_image_url=str(product.get("main_image_url") or ""),
                    product_analysis=str(state.get("product_analysis") or ""),
                    video_analysis=str(state.get("video_analysis") or ""),
                    current_date=current_date,
                ),
            },
        ],
    })
    ad_copy_sets = generate_ad_copy_sets(
        product_title=str(product.get("title") or ""),
        main_image_url=str(product.get("main_image_url") or ""),
        product_analysis=str(state.get("product_analysis") or ""),
        video_analysis=str(state.get("video_analysis") or ""),
        current_date=current_date,
        provider=selection.provider,
        model=selection.alias,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["ad_copy_sets"] = ad_copy_sets
    _store_step_result(state, "ad_copy", ad_copy_sets, ad_copy_sets)
    state.setdefault("models", {})["ad_copy"] = {
        "provider": selection.provider,
        "model_id": selection.model,
        "alias": selection.alias,
    }
    return {"ad_copy_sets": ad_copy_sets}


def _run_cover_generation_step(state: dict, *, provider: str | None, model: str | None, user_id: int) -> dict:
    selection = resolve_cover_model_selection(provider, model)
    _product, title, image_url, product_image_path = _ensure_product_assets(state)
    product = state.get("product") or {}
    selected_prompt = build_platform_prompt(
        SOCIAL_REELS_SPEC,
        product_title=title,
        product_url=str(state.get("product_url") or ""),
        main_image_url=image_url,
        product_analysis=str(state.get("product_analysis") or ""),
        video_analysis=str(state.get("video_analysis") or ""),
        ad_copy_sets=json.dumps(state.get("ad_copy_sets") or {}, ensure_ascii=False, indent=2),
    )
    image_count = normalize_image_count(state.get("image_count"), default=DEFAULT_IMAGE_COUNT)
    _store_step_request(state, "cover_generation", {
        "provider": selection.provider,
        "model": selection.model,
        "alias": selection.alias,
        "request_data": {
            "product_url": state.get("product_url") or "",
            "video_filename": state.get("video_filename") or "",
            "image_count": image_count,
            "ad_copy_sets": state.get("ad_copy_sets") or {},
        },
        "prompt": selected_prompt,
    })

    task_id = str(state.get("id") or "")

    def apply_cover_result(next_result: dict) -> None:
        image_prompts = next_result.get("image_prompts") if isinstance(next_result.get("image_prompts"), list) else []
        request_payload = state.setdefault("step_requests", {}).setdefault("cover_generation", {})
        request_payload["image_prompts"] = image_prompts
        if image_prompts and isinstance(image_prompts[0], dict):
            request_payload["prompt"] = str(image_prompts[0].get("prompt") or request_payload.get("prompt") or "")
        state["result"] = next_result
        state["inputs"] = next_result.get("inputs") or {}
        _store_step_result(state, "cover_generation", next_result, {"covers": next_result.get("covers") or []})
        state.setdefault("models", {}).update(next_result.get("models") or {})

    def persist_partial_cover_result(partial_result: dict) -> None:
        apply_cover_result(partial_result)
        done_count = len(partial_result.get("covers") or [])
        if done_count < image_count:
            message = f"已生成 {done_count}/{image_count} 张封面，继续排队生成下一张..."
        else:
            message = f"已生成 {done_count}/{image_count} 张封面，正在整理结果..."
        state.setdefault("step_messages", {})["cover_generation"] = message
        if task_id:
            _save_state(task_id, state, status="running")

    result = generate_video_covers(
        product_url=str(state.get("product_url") or ""),
        video_path=str(state.get("video_path") or ""),
        video_filename=str(state.get("video_filename") or "video.mp4"),
        product_title=title,
        main_image_url=image_url,
        product_image_path=product_image_path,
        user_id=user_id,
        task_id=state.get("id"),
        cover_provider=selection.provider,
        cover_model=selection.alias,
        product_analysis_text=str(state.get("product_analysis") or ""),
        video_analysis_text=str(state.get("video_analysis") or ""),
        ad_copy_payload=state.get("ad_copy_sets") if isinstance(state.get("ad_copy_sets"), dict) else None,
        image_count=image_count,
        on_cover_done=persist_partial_cover_result,
    )
    apply_cover_result(result)
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


def _start_video_cover_background(
    task_id: str,
    start_step: str = "video_analysis",
    image_count: int | None = None,
) -> bool:
    if not try_register_active_task(
        video_cover_project_store.VIDEO_COVER_TYPE,
        task_id,
        runner="web.routes.video_cover._run_video_cover_chain_with_tracking",
        entrypoint="video_cover.run",
        stage=start_step,
        details={"image_count": image_count},
    ):
        return False
    try:
        start_background_task(_run_video_cover_chain_with_tracking, task_id, start_step, image_count)
    except BaseException:
        unregister_active_task(video_cover_project_store.VIDEO_COVER_TYPE, task_id)
        raise
    return True


def _run_video_cover_chain_with_tracking(task_id: str, start_step: str, image_count: int | None = None):
    try:
        return _run_video_cover_chain(task_id, start_step=start_step, image_count=image_count)
    finally:
        unregister_active_task(video_cover_project_store.VIDEO_COVER_TYPE, task_id)


def _load_project_for_background(task_id: str) -> tuple[dict | None, dict]:
    row = video_cover_project_store.get_project(task_id, user_id=0, is_admin=True)
    return row, _parse_state(row)


def _run_video_cover_chain(task_id: str, *, start_step: str = "video_analysis", image_count: int | None = None) -> None:
    row, state = _load_project_for_background(task_id)
    if not row:
        return
    state.setdefault("id", task_id)
    state.setdefault("steps", _initial_steps())
    state.setdefault("step_messages", {name: "" for name in STEP_ORDER})
    state.setdefault("models", {})
    if image_count is not None:
        state["image_count"] = normalize_image_count(image_count)
    _clear_step_outputs(state, start_step)
    _project_model_defaults(state)
    user_id = int(row.get("user_id") or state.get("user_id") or 0)

    for step in STEP_ORDER[_step_index(start_step):]:
        try:
            _ensure_previous_steps_done(state, step)
            _mark_step_running(state, step)
            _save_state(task_id, state, status="running")
            model_default = _step_model_default(state, step)
            _run_project_step(
                state,
                step,
                provider=model_default.get("provider"),
                model=model_default.get("model_id"),
                user_id=user_id,
            )
            _mark_step_done(state, step)
            next_status = "done" if step == STEP_ORDER[-1] else "running"
            _save_state(task_id, state, status=next_status)
        except VideoCoverGenerationError as exc:
            _mark_step_error(state, step, str(exc))
            _save_state(task_id, state, status="error")
            return
        except Exception as exc:
            _mark_step_error(state, step, f"{STEP_LABELS[step]}失败：{exc}")
            _save_state(task_id, state, status="error")
            return


@bp.route("/video-cover", methods=["GET"])
@login_required
@admin_required
def page():
    projects = video_cover_project_store.list_projects(
        user_id=int(current_user.id),
        is_admin=_is_admin_user(),
    )
    model_defaults = video_cover_settings.get_model_defaults() if current_user.is_superadmin else {}
    return render_template(
        "video_cover_list.html",
        projects=projects,
        model_defaults=model_defaults,
        model_options=video_cover_model_options(),
        step_order=STEP_ORDER,
        step_labels=STEP_LABELS,
    )


@bp.route("/video-cover/<task_id>", methods=["GET"])
@login_required
@admin_required
def detail_page(task_id: str):
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
        image_count = normalize_image_count(request.form.get("image_count"), default=DEFAULT_IMAGE_COUNT)
        _product, title, image_url = _extract_product(product_url)

        task_id = uuid.uuid4().hex
        task_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        product_image_path = _save_product_image_asset(image_url, task_dir)
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
            product_title=title,
            main_image_url=image_url,
            product_image_path=product_image_path,
            image_count=image_count,
            model_defaults=video_cover_settings.get_model_defaults(),
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
        _start_video_cover_background(task_id, "video_analysis", image_count)
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    return _json_response({"ok": True, "id": task_id}, 201)


def _config_payload_from_request() -> dict:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return payload.get("steps") if isinstance(payload.get("steps"), dict) else payload
    payload = {}
    for step in STEP_ORDER:
        payload[step] = {
            "provider": request.form.get(f"{step}_provider") or "",
            "model_id": request.form.get(f"{step}_model_id") or "",
        }
    return payload


@bp.route("/video-cover/api/default-config", methods=["GET"])
@login_required
@superadmin_required
def api_default_config():
    return _json_response({
        "ok": True,
        "data": {
            "steps": video_cover_settings.get_model_defaults(),
            "options": video_cover_model_options(),
        },
    })


@bp.route("/video-cover/api/default-config", methods=["POST"])
@login_required
@superadmin_required
def api_save_default_config():
    defaults = video_cover_settings.save_model_defaults(_config_payload_from_request())
    return _json_response({"ok": True, "data": {"steps": defaults}})


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
    try:
        _ensure_previous_steps_done(state, step)
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    if not _start_video_cover_background(task_id, step):
        return _json_response({"ok": False, "error": "任务正在运行中"}, 409)
    return _json_response({"ok": True, "state": _state_with_urls(task_id, state)}, 202)


@bp.route("/video-cover/api/<task_id>/state", methods=["GET"])
@login_required
@admin_required
def api_project_state(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    state.setdefault("id", task_id)
    state.setdefault("steps", _initial_steps())
    state.setdefault("step_messages", {name: "" for name in STEP_ORDER})
    state.setdefault("image_count", DEFAULT_IMAGE_COUNT)
    return _json_response({"ok": True, "state": _state_with_urls(task_id, state)})


@bp.route("/video-cover/api/<task_id>/restart", methods=["POST"])
@login_required
@admin_required
def api_restart_project(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    payload = request.get_json(silent=True) or {}
    image_count = normalize_image_count(
        payload.get("image_count") or request.form.get("image_count"),
        default=DEFAULT_IMAGE_COUNT,
    )
    state.setdefault("id", task_id)
    state.setdefault("type", video_cover_project_store.VIDEO_COVER_TYPE)
    _clear_all_outputs(state)
    state["image_count"] = image_count
    _save_state(task_id, state, status="running")
    if not _start_video_cover_background(task_id, "video_analysis", image_count):
        return _json_response({"ok": False, "error": "任务正在运行中"}, 409)
    return _json_response({"ok": True, "state": _state_with_urls(task_id, state)}, 202)


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


def _send_project_file(path_value: str, *, mimetype: str | None = None):
    path = Path(path_value or "")
    if not path.is_file():
        return "Not Found", 404
    guessed = mimetype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return send_file(path, mimetype=guessed, conditional=True)


@bp.route("/video-cover/api/<task_id>/source-video", methods=["GET"])
@login_required
@admin_required
def source_video(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return "Not Found", 404
    return _send_project_file(str(state.get("video_path") or ""))


@bp.route("/video-cover/api/<task_id>/product-image", methods=["GET"])
@login_required
@admin_required
def product_image(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return "Not Found", 404
    return _send_project_file(_state_product_image_path(state), mimetype="image/jpeg")


@bp.route("/video-cover/api/product-analysis", methods=["POST"])
@login_required
@admin_required
def api_product_analysis():
    product_url = (request.form.get("product_url") or "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_product_") as work_dir:
            product, title, image_url = _extract_product(product_url)
            product_image_path = Path(work_dir) / "product_image.jpg"
            product_image_path.write_bytes(normalize_product_image_jpg(_fetch_product_image(image_url)))
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
            product_image_path = Path(work_dir) / "product_image.jpg"
            product_image_path.write_bytes(normalize_product_image_jpg(_fetch_product_image(image_url)))
            video_analysis = generate_video_analysis(
                video_path=str(video_path),
                product_title=title,
                product_url=product_url,
                main_image_url=image_url,
                product_image_path=product_image_path,
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
            product_title=_json_or_form("product_title"),
            main_image_url=_json_or_form("main_image_url"),
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
                image_count=normalize_image_count(request.form.get("image_count"), default=1),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith(("封面生成失败", "文案创作失败", "产品分析失败", "视频分析失败")) else 400
        return _json_response({"ok": False, "error": message}, status_code)

    return _json_response({"ok": True, "data": _attach_urls(result)})
