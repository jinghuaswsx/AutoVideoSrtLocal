"""Task creator (non-mk) — 7-step pipeline from Shopify link to task center."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from appcore import llm_client, local_media_storage, medias, object_keys
from appcore.material_filename_rules import build_initial_suggested_material_filename
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
from appcore.product_link_domains import build_product_page_url, list_domains
from appcore.tasks import create_parent_task
from appcore.video_cover_generation import (
    VideoCoverGenerationError,
    generate_ad_copy_sets,
    generate_product_analysis,
    generate_video_analysis,
    generate_video_covers,
    normalize_product_image_jpg,
    _fetch_product_image,
)
from config import OUTPUT_DIR, UPLOAD_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration


STEP_ORDER = (
    "shopify_extract",
    "video_analysis",
    "product_analysis",
    "ad_copy",
    "cover_generation",
    "material_ingest",
    "task_creation",
)

STEP_LABELS = {
    "shopify_extract": "Shopify商品信息提取",
    "video_analysis": "视频分析",
    "product_analysis": "产品分析",
    "ad_copy": "文案创作",
    "cover_generation": "封面生成",
    "material_ingest": "素材入库",
    "task_creation": "创建翻译任务",
}

STEP_OUTPUT_KEYS = {
    "shopify_extract": ("shopify_product",),
    "video_analysis": ("video_analysis",),
    "product_analysis": ("product_analysis",),
    "ad_copy": ("ad_copy_sets",),
    "cover_generation": ("cover_result",),
    "material_ingest": ("material_result",),
    "task_creation": ("task_result",),
}

# Steps 1-5 run as an auto chain; 6 and 7 need user config.
AUTO_CHAIN_LAST = "cover_generation"


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
        "id", "type", "status", "user_id", "display_name",
        "product_name_cn", "shopify_url", "video_path", "video_filename",
        "task_dir", "thumbnail_path", "image_count",
    )
    preserved = {key: state[key] for key in preserved_keys if key in state}
    state.clear()
    state.update(preserved)
    state["steps"] = {step: "pending" for step in STEP_ORDER}
    state["step_messages"] = {step: "" for step in STEP_ORDER}
    state["status"] = "running"


def _initial_state(task_id, user_id, product_name_cn, shopify_url,
                   video_path, video_filename, task_dir, display_name,
                   thumbnail_path) -> dict:
    return {
        "id": task_id,
        "type": "task_creator",
        "status": "uploaded",
        "user_id": user_id,
        "product_name_cn": product_name_cn,
        "shopify_url": shopify_url,
        "video_path": video_path,
        "video_filename": video_filename,
        "task_dir": task_dir,
        "display_name": display_name,
        "thumbnail_path": thumbnail_path,
        "steps": {step: "pending" for step in STEP_ORDER},
        "step_messages": {step: "" for step in STEP_ORDER},
        "step_timing": {},
        "config": {},
    }


# ---------------------------------------------------------------------------
# Step 1: Shopify 商品信息提取
# ---------------------------------------------------------------------------

def run_shopify_extract(state: dict) -> None:
    shopify_url = str(state.get("shopify_url") or "").strip()
    if not shopify_url:
        raise VideoCoverGenerationError("未设置 Shopify 商品链接")

    product = fetch_product_analysis(shopify_url)
    if not product or not getattr(product, "title", None):
        raise VideoCoverGenerationError("无法从 Shopify 链接提取商品信息，请检查链接")

    title = str(getattr(product, "title", "") or "").strip()
    main_image_url = str(getattr(product, "main_image_url", "") or "").strip()

    # extract product code from URL path
    product_code = ""
    from urllib.parse import urlparse
    parsed = urlparse(shopify_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if "products" in path_parts:
        idx = path_parts.index("products")
        if idx + 1 < len(path_parts):
            product_code = path_parts[idx + 1]

    task_dir = str(state.get("task_dir") or "")
    main_image_path = ""
    if main_image_url and task_dir:
        try:
            img_bytes = _fetch_product_image(main_image_url)
            img_bytes = normalize_product_image_jpg(img_bytes)
            main_image_path = os.path.join(task_dir, "product_main_image.jpg")
            Path(main_image_path).write_bytes(img_bytes)
        except Exception:
            pass  # non-fatal, image will be re-fetched on demand

    state["shopify_product"] = {
        "title": title,
        "main_image_url": main_image_url,
        "main_image_path": main_image_path,
        "product_code": product_code,
        "price_min": getattr(product, "price_min", None),
        "price_max": getattr(product, "price_max", None),
        "currency": getattr(product, "currency", "USD"),
    }


# ---------------------------------------------------------------------------
# Step 2-5: 复用 video_cover 生成函数
# ---------------------------------------------------------------------------

def _shopify_product(state: dict) -> dict:
    return state.get("shopify_product") or {}


def _product_title(state: dict) -> str:
    cn = str(state.get("product_name_cn") or "").strip()
    sp = _shopify_product(state)
    en = str(sp.get("title") or "").strip()
    return cn or en or "未命名产品"


def _main_image_url(state: dict) -> str:
    return str(_shopify_product(state).get("main_image_url") or "")


def _product_image_path(state: dict) -> str | None:
    p = str(_shopify_product(state).get("main_image_path") or "")
    return p if p and Path(p).is_file() else None


def _video_path(state: dict) -> str:
    return str(state.get("video_path") or "")


def run_video_analysis(state: dict, *, user_id: int = 0) -> None:
    video_path = _video_path(state)
    if not video_path or not Path(video_path).is_file():
        raise VideoCoverGenerationError("视频文件不存在，请重新上传")
    title = _product_title(state)
    shopify_url = str(state.get("shopify_url") or "")
    main_image_url = _main_image_url(state)
    product_image_path = _product_image_path(state)

    result = generate_video_analysis(
        video_path=video_path,
        product_title=title,
        product_url=shopify_url,
        main_image_url=main_image_url,
        product_image_path=product_image_path,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["video_analysis"] = result


def run_product_analysis(state: dict, *, user_id: int = 0) -> None:
    title = _product_title(state)
    main_image_url = _main_image_url(state)
    product_image_path = _product_image_path(state)
    product = _shopify_product(state)

    result = generate_product_analysis(
        product=product,
        product_title=title,
        main_image_url=main_image_url,
        product_image_path=product_image_path,
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["product_analysis"] = result


def run_ad_copy(state: dict, *, user_id: int = 0) -> None:
    title = _product_title(state)
    main_image_url = _main_image_url(state)
    product_analysis = state.get("product_analysis") or ""
    video_analysis = state.get("video_analysis") or ""

    result = generate_ad_copy_sets(
        product_title=title,
        main_image_url=main_image_url,
        product_analysis=str(product_analysis),
        video_analysis=str(video_analysis),
        current_date=date.today().strftime("%Y-%m-%d"),
        user_id=user_id,
        task_id=state.get("id"),
    )
    state["ad_copy_sets"] = result


def run_cover_generation(state: dict, *, user_id: int = 0) -> None:
    shopify_url = str(state.get("shopify_url") or "")
    video_path = _video_path(state)
    video_filename = str(state.get("video_filename") or "")
    title = _product_title(state)
    main_image_url = _main_image_url(state)
    product_image_path = _product_image_path(state)
    ad_copy_payload = state.get("ad_copy_sets") or {}
    product_analysis_text = state.get("product_analysis") or ""
    video_analysis_text = state.get("video_analysis") or ""

    # Skip product fetch since we already have data from step 1
    def _no_fetch(url: str):
        return {"title": title, "main_image_url": main_image_url}

    task_id = str(state.get("id") or "")
    task_dir = str(state.get("task_dir") or "")

    result = generate_video_covers(
        product_url=shopify_url,
        video_path=video_path,
        video_filename=video_filename,
        product_title=title,
        main_image_url=main_image_url,
        product_image_path=product_image_path,
        user_id=user_id,
        task_id=task_id,
        product_analysis_text=product_analysis_text,
        video_analysis_text=video_analysis_text,
        ad_copy_payload=ad_copy_payload,
        product_fetch_fn=_no_fetch,
        image_count=1,
    )
    state["cover_result"] = result


# ---------------------------------------------------------------------------
# Step 6: 素材入库
# ---------------------------------------------------------------------------

def run_material_ingest(state: dict, *, user_id: int = 0) -> None:
    config = state.get("config") or {}
    domain = str(config.get("domain") or "").strip()
    if not domain:
        raise VideoCoverGenerationError("请先在步骤6的配置面板中选择域名")

    product_name_cn = str(state.get("product_name_cn") or "").strip()
    sp = _shopify_product(state)
    product_code = str(sp.get("product_code") or "").strip()
    shopify_title = str(sp.get("title") or "").strip()
    video_path = _video_path(state)
    video_filename = str(state.get("video_filename") or "")

    # 1. Create media_product
    product_id = medias.create_product(
        user_id=user_id,
        name=product_name_cn,
        source="task_creator",
        product_code=product_code,
    )

    # 2. Save shopify title via update
    from appcore.db import execute
    execute(
        "UPDATE media_products SET shopify_title=%s WHERE id=%s",
        (shopify_title, product_id),
    )

    # 3. Build standardized filename
    suggested = config.get("filename") or ""
    if not suggested:
        suggested = build_initial_suggested_material_filename(video_filename, product_name_cn)
    state.setdefault("config", {})["filename"] = suggested

    # 4. Upload video to media store
    user_id_str = str(user_id)
    object_key = object_keys.build_media_object_key(user_id_str, product_id, suggested)
    local_media_storage.write_bytes(object_key, Path(video_path).read_bytes())

    # 5. Get video metadata
    duration = None
    file_size = None
    try:
        duration = get_media_duration(video_path)
    except Exception:
        pass
    try:
        file_size = Path(video_path).stat().st_size
    except Exception:
        pass

    # 6. Create media_item (English library)
    item_id = medias.create_item(
        product_id=product_id,
        user_id=user_id,
        filename=suggested,
        object_key=object_key,
        display_name=product_name_cn,
        file_url=None,
        thumbnail_path=state.get("thumbnail_path") or "",
        duration_seconds=duration,
        file_size=file_size,
        cover_object_key=None,
        lang="en",
        skip_push=1,  # 传入 skip_push=1 表示本英文原始素材默认不推送
    )

    # 7. Store result
    state["material_result"] = {
        "product_id": product_id,
        "item_id": item_id,
        "object_key": object_key,
        "filename": suggested,
        "domain": domain,
        "product_page_url": build_product_page_url(domain, "en", product_code),
    }


# ---------------------------------------------------------------------------
# Step 7: 创建翻译任务
# ---------------------------------------------------------------------------

def run_task_creation(state: dict, *, user_id: int = 0) -> None:
    config = state.get("config") or {}
    material = state.get("material_result") or {}
    product_id = material.get("product_id")
    item_id = material.get("item_id")

    if not product_id:
        raise VideoCoverGenerationError("请先完成素材入库步骤")

    countries = list(config.get("countries") or [])
    if not countries:
        raise VideoCoverGenerationError("请先在步骤7的配置面板中选择目标语种")

    language_assignments = config.get("language_assignments") or {}
    raw_processor_id = config.get("raw_processor_id")

    parent_id = create_parent_task(
        media_product_id=int(product_id),
        media_item_id=int(item_id) if item_id else None,
        countries=countries,
        language_assignments=language_assignments if language_assignments else None,
        translator_id=None,
        raw_processor_id=int(raw_processor_id) if raw_processor_id else None,
        created_by=int(user_id),
    )

    # Bind task_id to media_item
    if item_id:
        medias.update_item_task_id(int(item_id), parent_id)

    state["task_result"] = {
        "parent_task_id": parent_id,
        "product_id": product_id,
        "item_id": item_id,
        "countries": countries,
    }


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

STEP_RUNNERS = {
    "shopify_extract": run_shopify_extract,
    "video_analysis": run_video_analysis,
    "product_analysis": run_product_analysis,
    "ad_copy": run_ad_copy,
    "cover_generation": run_cover_generation,
    "material_ingest": run_material_ingest,
    "task_creation": run_task_creation,
}


def run_step(state: dict, step: str, *, user_id: int = 0) -> None:
    runner = STEP_RUNNERS.get(step)
    if not runner:
        raise VideoCoverGenerationError(f"未知步骤：{step}")
    kwargs = {}
    # steps 2-7 need user_id
    if step != "shopify_extract":
        kwargs["user_id"] = user_id
    runner(state, **kwargs)
