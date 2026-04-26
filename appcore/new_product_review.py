"""B 子系统：新品审核 + AI 评估矩阵 service。

详见 docs/superpowers/specs/2026-04-26-new-product-review-design.md
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Use case (复用 material_evaluation 的注册)
USE_CASE_CODE = "material_evaluation.evaluate"

# 截短产物本地路径
EVAL_CLIPS_ROOT = Path("instance") / "eval_clips"


# ---- 异常 ----
class NewProductReviewError(Exception):
    """B 子系统通用基类。"""


class ProductNotFoundError(NewProductReviewError):
    pass


class InvalidStateError(NewProductReviewError):
    pass


class ClipGenerationError(NewProductReviewError):
    pass


class EvaluationError(NewProductReviewError):
    pass


class TranslatorInvalidError(NewProductReviewError):
    pass


class NoVideoError(NewProductReviewError):
    pass


# ---- Task 4: _resolve_translator ----

from appcore.db import query_one, query


def _resolve_translator(translator_id: int) -> dict:
    """校验翻译员存在 + 有 can_translate 权限位。

    Raises:
        TranslatorInvalidError — 用户不存在 / 已停用 / 无权限位
    """
    if not translator_id:
        raise TranslatorInvalidError("translator_id required")
    row = query_one(
        "SELECT id, username, role, permissions, is_active "
        "FROM users WHERE id=%s",
        (int(translator_id),),
    )
    if not row:
        raise TranslatorInvalidError(f"user {translator_id} not found")
    if not row.get("is_active"):
        raise TranslatorInvalidError(f"user {translator_id} is inactive")

    perms = row.get("permissions") or "{}"
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except (TypeError, ValueError):
            perms = {}
    if not perms.get("can_translate"):
        raise TranslatorInvalidError(
            f"user {translator_id} lacks can_translate permission"
        )
    return row


# ---- Task 5: _make_eval_clip_15s ----

from appcore import material_evaluation


def _make_eval_clip_15s(product_id: int, item: dict) -> str:
    """生成（或复用）15 秒截短产物，返回相对项目根的路径。

    步骤：
      1. 若 instance/eval_clips/<pid>/<iid>_15s.mp4 已存在 → 直接返回
      2. 用 material_evaluation._materialize_media(object_key) 拿原视频本地 Path
      3. ffmpeg -ss 0 -i <input> -t 15 -c copy -avoid_negative_ts 1 -y <out>
      4. ffmpeg 失败时 fallback 用原视频路径（不抛异常）
    """
    item_id = int(item["id"])
    out_dir = EVAL_CLIPS_ROOT / str(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{item_id}_15s.mp4"

    if out_path.is_file() and out_path.stat().st_size > 0:
        return str(out_path)

    try:
        src_path = material_evaluation._materialize_media(item["object_key"])
    except Exception as e:
        raise ClipGenerationError(f"materialize source video failed: {e}") from e

    cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-i", str(src_path),
        "-t", "15",
        "-c", "copy",
        "-avoid_negative_ts", "1",
        str(out_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
            logger.warning(
                "ffmpeg clip cut failed, fallback to original. cmd=%s stderr=%s",
                cmd, result.stderr.decode("utf-8", errors="replace")[:500],
            )
            try:
                if out_path.is_file():
                    out_path.unlink()
            except Exception:
                pass
            return str(src_path)
        return str(out_path)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg clip cut timed out, fallback to original")
        return str(src_path)
    except FileNotFoundError as e:
        # ffmpeg 不在 PATH，fallback 到原视频
        logger.warning("ffmpeg not found, fallback to original: %s", e)
        return str(src_path)


# ---- Task 6: _build_evaluation_inputs ----

from appcore import medias as _medias


def _build_evaluation_inputs(product: dict) -> tuple[Path, str, list]:
    """收集评估所需输入 (cover_path, video_clip_path, languages)。

    Raises:
        EvaluationError — 缺少封面 / 无启用语种
        NoVideoError — 该产品没有英文视频
    """
    pid = int(product["id"])

    # 1. cover
    cover_key = material_evaluation._resolve_product_cover_key(pid, product)
    if not cover_key:
        raise EvaluationError("missing product cover")
    cover_path = material_evaluation._materialize_media(cover_key)

    # 2. video → 截短
    video = material_evaluation._first_english_video(pid)
    if not video:
        raise NoVideoError(f"product {pid} has no english video")
    clip_path = _make_eval_clip_15s(pid, video)

    # 3. languages
    languages = _medias.list_enabled_languages_kv()
    if not languages:
        raise EvaluationError("no enabled languages configured")

    return cover_path, clip_path, languages


# ---- Task 7: list_pending ----

def list_pending(*, limit: int = 200) -> list[dict]:
    """返回待评估 + 已评估未决策的新品列表。"""
    rows = query(
        """
        SELECT
          p.id, p.name, p.product_code, p.product_link, p.main_image,
          p.user_id AS translator_id,
          u.username AS translator_name,
          p.cover_object_key, p.mk_id,
          p.ai_score, p.ai_evaluation_result, p.ai_evaluation_detail,
          p.npr_decision_status, p.npr_decided_countries, p.npr_decided_at,
          p.npr_eval_clip_path,
          p.created_at, p.updated_at
        FROM media_products p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.deleted_at IS NULL
          AND p.mk_id IS NOT NULL
          AND COALESCE(p.archived, 0) = 0
          AND (p.npr_decision_status IS NULL OR p.npr_decision_status = 'pending')
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT %s
        """,
        (int(limit),),
    )

    # 解析 ai_evaluation_detail / npr_decided_countries JSON
    for row in rows:
        detail = row.get("ai_evaluation_detail")
        if detail and isinstance(detail, str):
            try:
                row["ai_evaluation_detail"] = json.loads(detail)
            except (TypeError, ValueError):
                row["ai_evaluation_detail"] = None
        countries = row.get("npr_decided_countries")
        if countries and isinstance(countries, str):
            try:
                row["npr_decided_countries"] = json.loads(countries)
            except (TypeError, ValueError):
                row["npr_decided_countries"] = None
    return rows


# ---- Task 8: evaluate_product ----

from appcore import llm_client


def evaluate_product(product_id: int, *, actor_user_id: int) -> dict:
    """同步执行新品 AI 评估。

    Raises:
        ProductNotFoundError / NoVideoError / EvaluationError
    """
    pid = int(product_id)
    product = _medias.get_product(pid)
    if not product:
        raise ProductNotFoundError(f"product {pid} not found")

    cover_path, clip_path, languages = _build_evaluation_inputs(product)

    from appcore.material_evaluation import (
        build_prompt, build_response_schema, build_system_prompt, normalize_result,
    )
    from appcore import pushes

    product_url = pushes.resolve_product_page_url("en", product) or product.get("product_link") or ""
    prompt = build_prompt(product, product_url, languages)

    try:
        llm_result = llm_client.invoke_generate(
            USE_CASE_CODE,
            prompt=prompt,
            system=build_system_prompt(),
            media=[cover_path, clip_path],
            user_id=product.get("user_id") or actor_user_id,
            project_id=f"npr-product-{pid}",
            response_schema=build_response_schema(languages),
            temperature=0.2,
            max_output_tokens=4096,
        )
    except Exception as e:
        logger.exception("LLM evaluation failed for product=%s", pid)
        try:
            _medias.update_product(pid, ai_evaluation_result="评估失败")
        except Exception:
            logger.exception("save evaluation failure status failed")
        raise EvaluationError(f"LLM call failed: {e}") from e

    raw = llm_result.get("json")
    if raw is None:
        raw = llm_result.get("text") or "{}"

    try:
        normalized = normalize_result(raw, languages)
    except Exception as e:
        try:
            _medias.update_product(pid, ai_evaluation_result="评估失败")
        except Exception:
            pass
        raise EvaluationError(f"normalize failed: {e}") from e

    detail = {
        "schema_version": 1,
        "use_case": USE_CASE_CODE,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "product_id": pid,
        "product_url": product_url,
        "evaluator_user_id": actor_user_id,
        "countries": normalized["countries"],
    }

    update_fields: dict[str, Any] = {
        "ai_score": normalized["ai_score"],
        "ai_evaluation_result": normalized["ai_evaluation_result"],
        "ai_evaluation_detail": json.dumps(detail, ensure_ascii=False),
        "npr_eval_clip_path": str(clip_path),
    }
    # 评估完进入"待决策"状态（已决策的不覆盖）
    if product.get("npr_decision_status") not in ("approved", "rejected"):
        update_fields["npr_decision_status"] = "pending"

    _medias.update_product(pid, **update_fields)

    return {
        "status": "evaluated",
        "product_id": pid,
        "ai_score": normalized["ai_score"],
        "ai_evaluation_result": normalized["ai_evaluation_result"],
        "detail": detail,
    }


# ---- Task 9: decide_approve ----

from appcore import tasks as _tasks


def decide_approve(
    product_id: int,
    *,
    countries: list[str],
    translator_id: int,
    actor_user_id: int,
) -> dict:
    """事务化决策上架 + 建任务。

    Raises:
        ProductNotFoundError / InvalidStateError /
        TranslatorInvalidError / ValueError / NoVideoError
    """
    pid = int(product_id)

    # 1. 校验
    if not countries:
        raise ValueError("countries must be non-empty")
    norm_countries = [c.strip().upper() for c in countries if c and c.strip()]
    if not norm_countries:
        raise ValueError("countries normalized to empty")

    product = _medias.get_product(pid)
    if not product:
        raise ProductNotFoundError(f"product {pid} not found")
    if product.get("npr_decision_status") == "approved":
        raise InvalidStateError(f"product {pid} already approved")
    if product.get("npr_decision_status") == "rejected":
        raise InvalidStateError(f"product {pid} already rejected; cannot approve")

    _resolve_translator(translator_id)  # raises TranslatorInvalidError

    # 2. 取第一条英语视频 id
    video = material_evaluation._first_english_video(pid)
    if not video:
        raise NoVideoError(f"product {pid} has no english video")
    media_item_id = int(video["id"])

    # 3. 如翻译员变了 → 走 update_product_owner 触发 cascade
    if int(product.get("user_id") or 0) != int(translator_id):
        _medias.update_product_owner(pid, int(translator_id))

    # 4. 建父任务
    try:
        task_id = _tasks.create_parent_task(
            media_product_id=pid,
            media_item_id=media_item_id,
            countries=norm_countries,
            translator_id=int(translator_id),
            created_by=int(actor_user_id),
        )
    except Exception as e:
        logger.exception("create_parent_task failed for product=%s", pid)
        raise NewProductReviewError(f"create_parent_task failed: {e}") from e

    # 5. 写 npr_* 字段
    _medias.update_product(
        pid,
        npr_decision_status="approved",
        npr_decided_countries=norm_countries,
        npr_decided_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        npr_decided_by=int(actor_user_id),
    )

    return {
        "task_id": int(task_id),
        "product_id": pid,
        "countries": norm_countries,
    }


# ---- Task 10: decide_reject ----

def decide_reject(
    product_id: int,
    *,
    reason: str,
    actor_user_id: int,
) -> dict:
    """admin 决定该新品不上架。

    Raises:
        ProductNotFoundError / InvalidStateError / ValueError
    """
    pid = int(product_id)
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("reason must be at least 10 characters")

    product = _medias.get_product(pid)
    if not product:
        raise ProductNotFoundError(f"product {pid} not found")
    if product.get("npr_decision_status") == "approved":
        raise InvalidStateError(f"product {pid} already approved")

    _medias.update_product(
        pid,
        npr_decision_status="rejected",
        npr_rejected_reason=reason[:500],
        npr_decided_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        npr_decided_by=int(actor_user_id),
    )
    return {"product_id": pid}
