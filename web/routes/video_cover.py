from __future__ import annotations

import base64
from datetime import date
import json
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Blueprint, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from appcore import local_media_storage, medias, video_cover_project_store, video_cover_settings
from appcore.gemini_image import _resolve_apimart_output_params, parse_openrouter_openai_image2_model
from appcore.llm_provider_configs import get_provider_config
from appcore.project_state import resolve_project_display_name_conflict, save_project_state
from appcore.settings import get_retention_hours
from appcore.task_recovery import try_register_active_task, unregister_active_task
from appcore.video_cover_generation import (
    LOCAL_IMAGE_2_QUALITY,
    LOCAL_TIKTOK_COVER_2K_SIZE,
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
    normalize_cover_execution_mode,
    normalize_image_count,
    normalize_product_image_jpg,
    resolve_cover_model_selection,
    resolve_text_model_selection,
    video_cover_model_options,
    _decode_image_response_payload,
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
CARD_THUMBNAIL_FILTER = "180:270:force_original_aspect_ratio=increase,crop=180:270"
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


def _extract_card_thumbnail(video_path: str, task_dir: str) -> str:
    try:
        return extract_thumbnail(video_path, task_dir, scale=CARD_THUMBNAIL_FILTER) or ""
    except Exception:
        return ""


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


def _is_superadmin_user() -> bool:
    return getattr(current_user, "is_superadmin", False)


def _video_cover_creator_name_expr() -> str:
    try:
        return medias._media_product_owner_name_expr()
    except Exception:
        return "u.username"


def _load_user_project(task_id: str) -> tuple[dict | None, dict]:
    row = video_cover_project_store.get_project(
        task_id,
        user_id=int(current_user.id),
        is_admin=_is_superadmin_user(),
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
    nested = parsed.get("video_analysis")
    if isinstance(nested, dict):
        merged = dict(parsed)
        for key in ("video_text", "voiceover"):
            if key in nested and key not in merged:
                merged[key] = nested[key]
        if "usage_logic" in nested and "actions" not in merged:
            merged["actions"] = nested["usage_logic"]
        if parsed.get("keyframes") and "cover_suggestions" not in merged:
            merged["cover_suggestions"] = {"keyframes": parsed.get("keyframes")}
        return merged
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


def _duplicate_display_name(state: dict, row: dict) -> str:
    original_filename = str(state.get("video_filename") or row.get("original_filename") or "").strip()
    base = str(state.get("display_name") or row.get("display_name") or Path(original_filename).stem or "video-cover").strip()
    return f"{base} 复制"


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
    defaults = view_state.get("model_defaults")
    if not isinstance(defaults, dict):
        defaults = video_cover_settings.get_model_defaults()
    view_state["model_defaults"] = video_cover_settings.normalize_model_defaults(defaults)
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


def _run_cover_generation_step(
    state: dict,
    *,
    provider: str | None,
    model: str | None,
    execution_mode: str | None = None,
    user_id: int,
) -> dict:
    selection = resolve_cover_model_selection(provider, model)
    cover_execution_mode = normalize_cover_execution_mode(selection.provider, execution_mode)
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
            "execution_mode": cover_execution_mode,
            "ad_copy_sets": state.get("ad_copy_sets") or {},
        },
        "prompt": selected_prompt,
        "execution_mode": cover_execution_mode,
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
        cover_execution_mode=cover_execution_mode,
        product_analysis_text=str(state.get("product_analysis") or ""),
        video_analysis_text=str(state.get("video_analysis") or ""),
        ad_copy_payload=state.get("ad_copy_sets") if isinstance(state.get("ad_copy_sets"), dict) else None,
        image_count=image_count,
        on_cover_done=persist_partial_cover_result,
    )
    apply_cover_result(result)
    return result


def _run_project_step(
    state: dict,
    step: str,
    *,
    provider: str | None,
    model: str | None,
    execution_mode: str | None = None,
    user_id: int,
) -> dict:
    if step == "video_analysis":
        return _run_video_analysis_step(state, provider=provider, model=model, user_id=user_id)
    if step == "product_analysis":
        return _run_product_analysis_step(state, provider=provider, model=model, user_id=user_id)
    if step == "ad_copy":
        return _run_ad_copy_step(state, provider=provider, model=model, user_id=user_id)
    if step == "cover_generation":
        return _run_cover_generation_step(
            state,
            provider=provider,
            model=model,
            execution_mode=execution_mode,
            user_id=user_id,
        )
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
                execution_mode=model_default.get("execution_mode"),
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
        is_admin=_is_superadmin_user(),
        owner_name_expr=_video_cover_creator_name_expr(),
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

        thumbnail_path = _extract_card_thumbnail(video_path, task_dir)

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


@bp.route("/video-cover/api/<task_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_project(task_id: str):
    row, _state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    from appcore import cleanup

    try:
        cleanup.delete_task_storage({
            "task_dir": row.get("task_dir") or "",
            "state_json": row.get("state_json") or "",
        })
    except Exception:
        pass

    video_cover_project_store.soft_delete_project(
        task_id,
        user_id=int(current_user.id),
        is_admin=_is_superadmin_user(),
    )
    return _json_response({"ok": True})


@bp.route("/video-cover/api/<task_id>/duplicate", methods=["POST"])
@login_required
@admin_required
def api_duplicate_project(task_id: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)

    source_video_path = str(state.get("video_path") or "").strip()
    if not source_video_path:
        return _json_response({"ok": False, "error": "源视频缺失，无法复制项目。"}, 409)
    if not Path(source_video_path).is_file():
        try:
            from web.services.task_source_video import ensure_local_source_video

            ensure_local_source_video(task_id, state)
        except Exception:
            return _json_response({"ok": False, "error": f"源视频缺失: {source_video_path}"}, 409)
    if not Path(source_video_path).is_file():
        return _json_response({"ok": False, "error": f"源视频缺失: {source_video_path}"}, 409)

    try:
        product_url = _validate_product_url(state.get("product_url") or "")
        product = _state_product(state)
        title = str(product.get("title") or "").strip()
        image_url = str(product.get("main_image_url") or "").strip()
        source_product_image_path = _state_product_image_path(state)
        if not title or not image_url:
            _product, title, image_url, source_product_image_path = _ensure_product_assets(state, fetch_product=True)
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 409)

    original_filename = (
        str(state.get("video_filename") or "").strip()
        or str(row.get("original_filename") or "").strip()
        or (Path(source_video_path).name if source_video_path else "video.mp4")
    )
    new_task_id = uuid.uuid4().hex
    new_task_dir = os.path.join(OUTPUT_DIR, new_task_id)
    os.makedirs(new_task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    new_video_path = ""
    thumbnail_path = str(state.get("thumbnail_path") or "").strip()

    safe_name = secure_filename_component(original_filename)
    new_video_path = os.path.join(UPLOAD_DIR, f"{new_task_id}_video_{safe_name}")
    try:
        shutil.copy2(source_video_path, new_video_path)
    except OSError as exc:
        return _json_response({"ok": False, "error": f"复制源视频失败: {exc}"}, 500)
    try:
        thumbnail_path = _extract_card_thumbnail(new_video_path, new_task_dir)
    except Exception:
        pass

    new_product_image_path = os.path.join(new_task_dir, "product_main.jpg")
    try:
        if source_product_image_path and Path(source_product_image_path).is_file():
            shutil.copy2(source_product_image_path, new_product_image_path)
        else:
            new_product_image_path = _save_product_image_asset(image_url, new_task_dir)
    except Exception as exc:
        return _json_response({"ok": False, "error": f"复制商品主图失败: {exc}"}, 500)

    display_name = resolve_project_display_name_conflict(
        int(current_user.id),
        _duplicate_display_name(state, row),
    )
    image_count = normalize_image_count(state.get("image_count"), default=DEFAULT_IMAGE_COUNT)
    model_defaults = _project_model_defaults(state)

    next_state = _initial_state(
        task_id=new_task_id,
        user_id=int(current_user.id),
        product_url=product_url,
        video_path=new_video_path,
        video_filename=original_filename,
        task_dir=new_task_dir,
        display_name=display_name,
        thumbnail_path=thumbnail_path,
        product_title=title,
        main_image_url=image_url,
        product_image_path=new_product_image_path,
        image_count=image_count,
        model_defaults=model_defaults,
    )
    video_cover_project_store.insert_project(
        task_id=new_task_id,
        user_id=int(current_user.id),
        original_filename=original_filename,
        display_name=display_name,
        thumbnail_path=thumbnail_path,
        task_dir=new_task_dir,
        state=next_state,
        retention_hours=get_retention_hours(video_cover_project_store.VIDEO_COVER_TYPE),
    )
    _start_video_cover_background(new_task_id, "video_analysis", image_count)

    return _json_response({
        "ok": True,
        "id": new_task_id,
        "redirect_url": f"/video-cover/{new_task_id}",
    }, 201)


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
        if step == "cover_generation":
            payload[step]["execution_mode"] = request.form.get(f"{step}_execution_mode") or ""
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


def _json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        pass

    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if isinstance(key, (str, int, float, bool)) or key is None:
                safe_key = key
            else:
                safe_key = str(key)
            safe[safe_key] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _cover_provider_config_code(provider: str) -> str:
    normalized = (provider or "").strip()
    if normalized in {"", "local", "local_image_2"}:
        return "video_cover_local_image"
    if normalized == "openrouter":
        return "openrouter_image"
    if normalized == "apimart":
        return "apimart_image"
    if normalized == "gemini_aistudio":
        return "gemini_aistudio_image"
    if normalized == "gemini_vertex_adc":
        return "gemini_vertex_adc_image"
    raise VideoCoverGenerationError(f"未知封面供应商：{provider}")


def _step_debug_request(state: dict, step: str) -> dict:
    request_payload = ((state.get("step_requests") or {}).get(step)) or {}
    return request_payload if isinstance(request_payload, dict) else {}


def _step_debug_result(state: dict, step: str) -> dict:
    result_payload = ((state.get("step_results") or {}).get(step)) or {}
    return result_payload if isinstance(result_payload, dict) else {}


def _prompt_index_from_request(default: int = 1) -> int:
    raw = request.args.get("prompt_index")
    if raw is None:
        return default
    return _parse_prompt_index(raw)


def _parse_prompt_index(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VideoCoverGenerationError("prompt_index 非法") from exc
    if parsed < 1:
        raise VideoCoverGenerationError("prompt_index 非法")
    return parsed


def _cover_prompt_row(request_payload: dict, prompt_index: int) -> dict:
    prompts = request_payload.get("image_prompts")
    if not isinstance(prompts, list):
        prompts = []
    if not prompts:
        prompt = str(request_payload.get("prompt") or "")
        if prompt_index == 1 and prompt:
            return {"index": prompt_index, "prompt": prompt}
        raise VideoCoverGenerationError(f"prompt_index {prompt_index} 不存在")
    for item in prompts:
        if not isinstance(item, dict):
            continue
        item_index = _parse_prompt_index(item.get("index"))
        if item_index == prompt_index:
            return item
    raise VideoCoverGenerationError(f"prompt_index {prompt_index} 不存在")


def _cover_reference_object_key(state: dict) -> str:
    result = state.get("result") if isinstance(state.get("result"), dict) else {}
    reference = result.get("reference") if isinstance(result.get("reference"), dict) else {}
    return str(reference.get("object_key") or "").strip()


def _provider_config_values(provider: str) -> tuple[str, str]:
    cfg = get_provider_config(_cover_provider_config_code(provider))
    api_key = str(getattr(cfg, "api_key", "") or "").strip() if cfg else ""
    base_url = str(getattr(cfg, "base_url", "") or "").strip() if cfg else ""
    return api_key, base_url.rstrip("/")


def _cover_full_request_endpoint(provider: str, base_url: str) -> tuple[str, str]:
    normalized = (provider or "").strip()
    if normalized in {"", "local", "local_image_2"}:
        api_base = base_url or "http://172.16.254.106:82/v1"
        return f"{api_base.rstrip('/')}/images/edits", "multipart/form-data"
    if normalized == "openrouter":
        api_base = base_url or "https://openrouter.ai/api/v1"
        return f"{api_base.rstrip('/')}/chat/completions", "application/json"
    if normalized == "apimart":
        api_base = base_url or "https://api.apimart.ai"
        return f"{api_base.rstrip('/')}/v1/images/generations", "application/json"
    raise VideoCoverGenerationError(f"未知封面供应商：{provider}")


def _reference_image_summary(object_key: str) -> dict:
    return {
        "object_key": object_key,
        "content_type": "image/png",
        "data_url": "data:image/png;base64,<reference image bytes>",
    }


def _apimart_output_params_for_reference(object_key: str) -> tuple[str, str, str]:
    try:
        path = local_media_storage.safe_local_path_for(object_key)
        if path and Path(path).is_file():
            size, resolution = _resolve_apimart_output_params(Path(path).read_bytes())
            return size, resolution, "local_reference_image"
    except Exception:
        pass
    return "auto", "1k", "fallback_no_local_reference"


def _base_cover_request_parts(request_payload: dict, prompt_index: int) -> tuple[str, str, str, str, list]:
    provider = str(request_payload.get("provider") or "local").strip() or "local"
    model = str(request_payload.get("model") or request_payload.get("model_id") or "gpt-image-2").strip() or "gpt-image-2"
    prompt_row = _cover_prompt_row(request_payload, prompt_index)
    prompt = str(prompt_row.get("prompt") or request_payload.get("prompt") or "")
    image_prompts = request_payload.get("image_prompts")
    if not isinstance(image_prompts, list):
        image_prompts = []
    return provider, model, prompt, str(prompt_row.get("index") or prompt_index), image_prompts


def _build_cover_full_request(state: dict, request_payload: dict, prompt_index: int) -> tuple[dict, dict]:
    provider, model, prompt, resolved_prompt_index_raw, image_prompts = _base_cover_request_parts(request_payload, prompt_index)
    api_key, base_url = _provider_config_values(provider)
    if provider in {"local", "local_image_2", "openrouter", "apimart", "gemini_aistudio"} and not api_key:
        raise VideoCoverGenerationError(f"缺少供应商配置 {_cover_provider_config_code(provider)}.api_key")
    object_key = _cover_reference_object_key(state)
    if not object_key:
        raise VideoCoverGenerationError("缺少 reference image")

    prompt_indexes = [
        _parse_prompt_index(item.get("index") or idx + 1)
        for idx, item in enumerate(image_prompts)
        if isinstance(item, dict)
    ]
    resolved_prompt_index = _parse_prompt_index(resolved_prompt_index_raw)

    replay = {
        "supported": provider in {"local", "local_image_2"},
        "prompt_index": resolved_prompt_index,
        "prompt_indexes": prompt_indexes or [prompt_index],
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    if provider in {"local", "local_image_2"}:
        url, content_type = _cover_full_request_endpoint(provider, base_url)
        full_request = {
            "method": "POST",
            "url": url,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
            },
            "api_key": api_key,
            "body": {
                "model": model,
                "prompt": prompt,
                "n": "1",
                "size": LOCAL_TIKTOK_COVER_2K_SIZE,
                "quality": LOCAL_IMAGE_2_QUALITY,
            },
            "files": [{
                "field": "image",
                "filename": "reference.png",
                "content_type": "image/png",
                "source": object_key,
            }],
            "image_prompts": image_prompts,
        }
        replay.update({
            "default_url": url,
            "default_api_key": api_key,
        })
        return full_request, replay

    if provider == "openrouter":
        url, _content_type = _cover_full_request_endpoint(provider, base_url)
        openrouter_model = model
        extra_body = {"usage": {"include": True}}
        parsed_openrouter_model = parse_openrouter_openai_image2_model(model)
        if parsed_openrouter_model is not None:
            openrouter_model, quality = parsed_openrouter_model
            extra_body["quality"] = quality
            extra_body["image_config"] = {"image_size": "2K"}
        full_request = {
            "method": "POST",
            "url": url,
            "headers": {**headers, "Content-Type": "application/json"},
            "api_key": api_key,
            "body": {
                "model": openrouter_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,<reference image bytes>",
                                "reference_image_object_key": object_key,
                            },
                        },
                    ],
                }],
                "modalities": ["image", "text"],
                "extra_body": extra_body,
            },
            "files": [],
            "image_prompts": image_prompts,
        }
        replay["reason"] = "该供应商暂不支持调试生成"
        return full_request, replay

    if provider == "apimart":
        url, _content_type = _cover_full_request_endpoint(provider, base_url)
        size, resolution, output_params_source = _apimart_output_params_for_reference(object_key)
        full_request = {
            "method": "POST",
            "url": url,
            "headers": {**headers, "Content-Type": "application/json"},
            "api_key": api_key,
            "body": {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "resolution": resolution,
                "output_params_source": output_params_source,
                "image_urls": ["data:image/png;base64,<reference image bytes>"],
                "reference_image_object_key": object_key,
            },
            "files": [],
            "image_prompts": image_prompts,
        }
        replay["reason"] = "该供应商暂不支持调试生成"
        return full_request, replay

    if provider in {"gemini_aistudio", "gemini_vertex_adc"}:
        full_request = {
            "method": "SDK",
            "url": provider,
            "headers": headers,
            "api_key": api_key,
            "body": {
                "provider": provider,
                "model": model,
                "prompt": prompt,
                "source_image": _reference_image_summary(object_key),
            },
            "files": [],
            "image_prompts": image_prompts,
        }
        replay["reason"] = "该供应商暂不支持调试生成"
        return full_request, replay

    raise VideoCoverGenerationError(f"未知封面供应商：{provider}")


def _build_text_full_request(request_payload: dict) -> dict:
    body = {}
    if request_payload.get("messages") is not None:
        body["messages"] = request_payload.get("messages")
    if request_payload.get("prompt") is not None:
        body["prompt"] = request_payload.get("prompt")
    if request_payload.get("request_data") is not None:
        body["request_data"] = request_payload.get("request_data")
    return {
        "method": "SDK",
        "url": "",
        "headers": {},
        "api_key": "",
        "body": body,
        "files": [],
    }


def _build_step_debug_payload(task_id: str, state: dict, step: str, prompt_index: int = 1) -> dict:
    if step not in STEP_ORDER:
        raise VideoCoverGenerationError(f"未知步骤：{step}")
    view_state = _state_with_urls(task_id, state)
    request_payload = _step_debug_request(view_state, step)
    result_payload = _step_debug_result(view_state, step)

    if step == "cover_generation":
        full_request, replay = _build_cover_full_request(view_state, request_payload, prompt_index)
        response_data = dict(view_state.get("result") or {})
    else:
        full_request = _build_text_full_request(request_payload)
        replay = {"supported": False, "reason": "该步骤暂不支持调试生成"}
        response_data = result_payload.get("structured_result")

    image_prompts = request_payload.get("image_prompts")
    if not isinstance(image_prompts, list):
        image_prompts = []
    return {
        "step": step,
        "label": STEP_LABELS.get(step, step),
        "status": ((view_state.get("steps") or {}).get(step)) or "pending",
        "request_data": {
            "request": request_payload,
            "product": view_state.get("product") or {},
            "image_count": view_state.get("image_count") or DEFAULT_IMAGE_COUNT,
            "image_prompts": image_prompts,
        },
        "full_request": full_request,
        "response_data": response_data,
        "raw_response": result_payload.get("raw_response"),
        "replay": replay,
    }


def _debug_payload_json_response(payload: dict, status: int = 200):
    response = _json_response(payload, status)
    if isinstance(response, tuple):
        body, status_code = response
        body.headers["Cache-Control"] = "no-store"
        body.headers["Pragma"] = "no-cache"
        return body, status_code
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _request_json_payload() -> dict:
    payload = request.get_json(silent=True) if request.is_json else None
    return payload if isinstance(payload, dict) else {}


def _cover_reference_image_bytes(state: dict) -> tuple[bytes, str, str]:
    object_key = _cover_reference_object_key(state)
    if not object_key:
        raise VideoCoverGenerationError("缺少封面生成参考图，无法调试生成")
    path = local_media_storage.safe_local_path_for(object_key)
    if not path.is_file():
        local_media_storage.download_to(object_key, path)
    if not path.is_file():
        raise VideoCoverGenerationError("封面生成参考图文件不存在，无法调试生成")
    return path.read_bytes(), "image/png", object_key


def _post_debug_cover_request(
    *,
    request_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    image_mime: str,
) -> tuple[bytes, str, dict]:
    if not request_url:
        raise VideoCoverGenerationError("请填写请求 URL")
    if not api_key:
        raise VideoCoverGenerationError("请填写 API key")
    response = requests.post(
        request_url,
        headers={"Authorization": f"Bearer {api_key}"},
        data={"model": model or "gpt-image-2", "prompt": prompt, "n": "1", "size": "1024x1536"},
        files={"image": ("reference.png", image_bytes, image_mime or "image/png")},
        timeout=360,
    )
    try:
        raw_response = response.json()
    except Exception:
        raw_response = {"text": str(getattr(response, "text", "") or "")}
    if getattr(response, "status_code", 0) >= 400:
        message = ""
        if isinstance(raw_response, dict):
            message = str(raw_response.get("error") or raw_response.get("message") or "")
        message = message or str(getattr(response, "text", "") or "")
        raise VideoCoverGenerationError(f"调试生成失败（HTTP {response.status_code}）：{message}")
    image, mime = _decode_image_response_payload(raw_response)
    return image, mime, raw_response


@bp.route("/video-cover/api/<task_id>/debug-payload/<step>", methods=["GET"])
@login_required
@superadmin_required
def api_debug_payload(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _debug_payload_json_response({"ok": False, "error": "not found"}, 404)
    try:
        payload = _build_step_debug_payload(task_id, state, step, _prompt_index_from_request())
    except VideoCoverGenerationError as exc:
        return _debug_payload_json_response({"ok": False, "error": str(exc)}, 400)
    return _debug_payload_json_response({"ok": True, "data": _json_safe(payload)})


@bp.route("/video-cover/api/<task_id>/debug-replay/<step>", methods=["POST"])
@login_required
@superadmin_required
def api_debug_replay(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    if step != "cover_generation":
        return _json_response({"ok": False, "error": "该步骤暂不支持调试生成"}, 400)
    payload = _request_json_payload()
    try:
        prompt_index = _parse_prompt_index(payload.get("prompt_index") or 1)
        request_payload = _step_debug_request(state, step)
        provider = str(request_payload.get("provider") or "local").strip() or "local"
        if provider != "local":
            return _json_response({"ok": False, "error": "该供应商暂不支持调试生成"}, 400)
        prompt_row = _cover_prompt_row(request_payload, prompt_index)
        prompt = str(prompt_row.get("prompt") or request_payload.get("prompt") or "")
        model = str(request_payload.get("model") or request_payload.get("model_id") or "gpt-image-2")
        image_bytes, image_mime, _object_key = _cover_reference_image_bytes(state)
        generated, mime, raw_response = _post_debug_cover_request(
            request_url=str(payload.get("request_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    data_url = f"data:{mime or 'image/png'};base64,{base64.b64encode(generated).decode('ascii')}"
    return _json_response({
        "ok": True,
        "image": {"data_url": data_url, "mime": mime or "image/png"},
        "raw_response": raw_response,
        "request_url": str(payload.get("request_url") or "").strip(),
        "prompt_index": prompt_index,
    })


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
                cover_execution_mode=request.form.get("cover_execution_mode") or "",
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
