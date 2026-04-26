# B 子系统：新品审核 + AI 评估矩阵 — 30-task 实施计划

- **Spec**：[../specs/2026-04-26-new-product-review-design.md](../specs/2026-04-26-new-product-review-design.md)
- **Worktree**：`g:\Code\AutoVideoSrtLocal\.worktrees\new-product-review`
- **分支**：`feature/new-product-review`
- **基础**：A 子系统 mk-import 已上线（commit `cc424a9` 之前），任务中心 C 子系统已建表 + service + 接口
- **执行模式**：可全部委派给 `superpowers:subagent-driven-development`，逐任务 commit

---

## 总览

| Phase | 任务范围 | 任务数 | 难度 |
|---|---|---|---|
| 1 | DB migration + medias.update_product 白名单 | 2 | 易 |
| 2 | service 骨架 + helper | 5 | 中 |
| 3 | service 主入口（list/evaluate/decide） | 4 | 中 |
| 4 | service 单元测试 | 5 | 中 |
| 5 | Blueprint + 5 路由 | 5 | 易 |
| 6 | 路由集成测试 | 4 | 中 |
| 7 | 模板 + 矩阵 UI + Modal | 4 | 中 |
| 8 | Tab 切换栏 + Blueprint 注册 + 冒烟 | 1 | 易 |

合计 30 任务。

---

## 总体约定

- **本地无 MySQL**：service 单元测试在 worktree 本地用 mock；DB 交互测试通过 SSH 跑在 `/opt/autovideosrt-test`，参考 CLAUDE.md 约定
- **路由测试用 fixture**：`authed_client_no_db` / `authed_user_client_no_db`（mock DB 层）
- **commit 模板**：所有 commit 加 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- **commit 信息风格**：`feat(new-product-review): ...` / `test(new-product-review): ...` / `fix(new-product-review): ...`
- **每完成一个任务立即 commit**，方便回滚 + 上下文清晰

---

## Phase 1：DB Migration + 字段白名单

### Task 1：写 migration `2026_04_28_media_products_npr_decision.sql`

**文件**：`db/migrations/2026_04_28_media_products_npr_decision.sql`（新增）

**内容**：
```sql
-- B 子系统：新品审核决策字段
-- 复用 media_products 表，加 6 列承载新品审核决策状态
-- 启动时 appcore.db_migrations.apply_pending() 自动 apply

ALTER TABLE media_products
  ADD COLUMN npr_decision_status ENUM('pending','approved','rejected') NULL DEFAULT NULL COMMENT '新品审核决策状态' AFTER listing_status,
  ADD COLUMN npr_decided_countries JSON NULL DEFAULT NULL COMMENT '决策上架国家清单(大写ISO)' AFTER npr_decision_status,
  ADD COLUMN npr_decided_at DATETIME NULL DEFAULT NULL COMMENT '决策时间' AFTER npr_decided_countries,
  ADD COLUMN npr_decided_by INT NULL DEFAULT NULL COMMENT '决策人user_id' AFTER npr_decided_at,
  ADD COLUMN npr_rejected_reason VARCHAR(500) NULL DEFAULT NULL COMMENT '不上架理由' AFTER npr_decided_by,
  ADD COLUMN npr_eval_clip_path VARCHAR(512) NULL DEFAULT NULL COMMENT '15s截短产物本地路径' AFTER npr_rejected_reason;
```

**验证**：
- 文件名格式正确（YYYY_MM_DD_purpose.sql）
- 用 `IF NOT EXISTS` 类型保护？参考 `2026_04_23_media_products_evaluation_fields.sql` 的写法 — 该文件用了 `INFORMATION_SCHEMA` 检查模式。**实施时如果不确定 ALTER + ADD COLUMN 在 MySQL 重复执行是否幂等，参照该写法包一层"先查再加"**。
- 在 `appcore/db_migrations.py` 验证 migration 自动发现 + apply

**Commit**：`feat(new-product-review): migration — add npr_* fields to media_products`

---

### Task 2：扩展 `appcore/medias.update_product` 白名单

**文件**：`appcore/medias.py`（修改 line ~407）

**改动**：把以下字段加到 `allowed` 集：
- `npr_decision_status`
- `npr_decided_countries`（JSON 序列化兼容已有 `selling_points` / `localized_links_json` 等的处理）
- `npr_decided_at`
- `npr_decided_by`
- `npr_rejected_reason`
- `npr_eval_clip_path`

**验证**：grep 现有 `localized_links_json` / `link_check_tasks_json` 看 update_product 是否有 JSON 字段特殊处理；参照该处理对 `npr_decided_countries` 做相同包装

**Commit**：`feat(new-product-review): allow npr_* fields in medias.update_product`

---

## Phase 2：Service 骨架 + Helper

### Task 3：创建 `appcore/new_product_review.py` 骨架 + 异常类

**文件**：`appcore/new_product_review.py`（新建）

**内容**：
```python
"""B 子系统：新品审核 + AI 评估矩阵 service。

详见 docs/superpowers/specs/2026-04-26-new-product-review-design.md
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
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

class ProductNotFoundError(NewProductReviewError): pass
class InvalidStateError(NewProductReviewError): pass
class ClipGenerationError(NewProductReviewError): pass
class EvaluationError(NewProductReviewError): pass
class TranslatorInvalidError(NewProductReviewError): pass
class NoVideoError(NewProductReviewError): pass
```

**验证**：import 成功 + 异常类继承链正确

**Commit**：`feat(new-product-review): scaffold module + exceptions`

---

### Task 4：实现 `_resolve_translator(translator_id)` helper

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
from appcore.db import query_one


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
```

**验证**：
- 无 translator_id → raise
- 用户不存在 → raise
- is_active=0 → raise
- 无 can_translate 权限 → raise
- 正常用户 → 返回 dict

**Commit**：`feat(new-product-review): _resolve_translator helper`

---

### Task 5：实现 `_make_eval_clip_15s(product_id, item)` helper

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
from appcore import material_evaluation


def _make_eval_clip_15s(product_id: int, item: dict) -> str:
    """生成（或复用）15 秒截短产物，返回相对项目根的路径。
    
    步骤：
      1. 若 instance/eval_clips/<pid>/<iid>_15s.mp4 已存在 → 直接返回
      2. 用 material_evaluation._materialize_media(object_key) 拿原视频本地 Path
      3. ffmpeg -ss 0 -i <input> -t 15 -c copy -avoid_negative_ts 1 -y <out>
      4. ffmpeg 失败时 fallback 用原视频路径，但仍返回原始路径（不抛异常）
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
```

**验证**：
- 复用已存在的产物（不重跑 ffmpeg）
- ffmpeg 失败 → fallback 原视频
- ffmpeg 不在 PATH → fallback 原视频
- 产物路径与 spec 一致：`instance/eval_clips/<pid>/<iid>_15s.mp4`

**Commit**：`feat(new-product-review): _make_eval_clip_15s with ffmpeg + fallback`

---

### Task 6：实现 `_build_evaluation_inputs(product)` helper

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
from appcore import medias as _medias


def _build_evaluation_inputs(product: dict) -> tuple[Path, str, list]:
    """收集评估所需输入 (cover_path, video_clip_path, languages)。
    
    Raises:
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
```

**验证**：
- 无 cover → EvaluationError
- 无视频 → NoVideoError
- 无启用语种 → EvaluationError

**Commit**：`feat(new-product-review): _build_evaluation_inputs helper`

---

### Task 7：实现 `list_pending(limit=200)` 入口

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
from appcore.db import query


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
    
    # 解析 ai_evaluation_detail JSON
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
```

**验证**：
- 返回的字段齐全
- JSON 字段反序列化正确

**Commit**：`feat(new-product-review): list_pending entry`

---

## Phase 3：Service 主入口

### Task 8：实现 `evaluate_product(product_id, actor_user_id)`

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
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
    
    # 走和 material_evaluation 相同的 prompt + schema
    from appcore.material_evaluation import (
        build_prompt, build_response_schema, build_system_prompt, normalize_result
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
    
    update_fields = {
        "ai_score": normalized["ai_score"],
        "ai_evaluation_result": normalized["ai_evaluation_result"],
        "ai_evaluation_detail": json.dumps(detail, ensure_ascii=False),
        "npr_eval_clip_path": str(clip_path),
    }
    # 评估完进入"待决策"状态
    if not (product.get("npr_decision_status") in ("approved", "rejected")):
        update_fields["npr_decision_status"] = "pending"
    
    _medias.update_product(pid, **update_fields)
    
    return {
        "status": "evaluated",
        "product_id": pid,
        "ai_score": normalized["ai_score"],
        "ai_evaluation_result": normalized["ai_evaluation_result"],
        "detail": detail,
    }
```

**验证**：
- 产品不存在 → ProductNotFoundError
- 无视频 → NoVideoError（透传）
- LLM 异常 → 写"评估失败" + raise EvaluationError
- 成功 → 写 ai_score / ai_evaluation_result / ai_evaluation_detail / npr_eval_clip_path / npr_decision_status='pending'

**Commit**：`feat(new-product-review): evaluate_product main entry`

---

### Task 9：实现 `decide_approve(product_id, countries, translator_id, actor_user_id)`

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
from appcore import tasks as _tasks
from appcore.db import execute, get_conn


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
        TranslatorInvalidError / ValueError
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
    
    # 4. 建父任务（C 内部已是事务，create_parent_task 失败抛异常）
    try:
        task_id = _tasks.create_parent_task(
            media_product_id=pid,
            media_item_id=media_item_id,
            countries=norm_countries,
            translator_id=int(translator_id),
            created_by=int(actor_user_id),
        )
    except Exception as e:
        # update_product_owner 已经写了，但任务建失败时该不该回滚 owner？
        # 决策：不回滚 owner — admin 可重新点"上架"重试
        logger.exception("create_parent_task failed for product=%s", pid)
        raise NewProductReviewError(f"create_parent_task failed: {e}") from e
    
    # 5. 写 npr_* 字段
    _medias.update_product(
        pid,
        npr_decision_status="approved",
        npr_decided_countries=norm_countries,  # update_product 已支持 JSON 序列化
        npr_decided_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        npr_decided_by=int(actor_user_id),
    )
    
    return {
        "task_id": int(task_id),
        "product_id": pid,
        "countries": norm_countries,
    }
```

**注意**：
- `medias.update_product` 当前不支持 `npr_decided_at` / `npr_decided_by` 直接 datetime / int 写入 — 实施时核对，如果不支持则用直接 SQL UPDATE
- `npr_decided_countries` 是 JSON 字段，传 list 给 update_product 时确认它会自动 `json.dumps`（参考 `selling_points` / `localized_links_json` 处理）。**实施第一步**：grep `update_product` 看 JSON 字段处理路径

**验证**：
- 国家空 → ValueError
- 已 approved → InvalidStateError
- 已 rejected → InvalidStateError
- 无视频 → NoVideoError
- 翻译员无效 → TranslatorInvalidError
- 翻译员变更 → update_product_owner 调用
- 建任务失败 → 抛 NewProductReviewError
- 成功 → npr_decision_status='approved' + countries + decided_at + decided_by

**Commit**：`feat(new-product-review): decide_approve with task creation`

---

### Task 10：实现 `decide_reject(product_id, reason, actor_user_id)`

**文件**：`appcore/new_product_review.py`（追加）

**内容**：
```python
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
```

**Commit**：`feat(new-product-review): decide_reject entry`

---

### Task 11：核对 `medias.update_product` 是否支持新增字段全写入

**文件**：检查 `appcore/medias.py:update_product`

**操作**：
- grep 检查 `update_product` 中 `localized_links_json` 等 JSON 字段的处理路径
- 如果 JSON 字段是手工 `json.dumps`（在白名单后做）则把 `npr_decided_countries` 加入相同代码块
- `npr_decided_at` 是 DATETIME 字段，传字符串 `"YYYY-MM-DD HH:MM:SS"` 应该可以；如果不行改用直接 `execute("UPDATE ...")`
- 添加单元测试 `test_update_product_writes_npr_fields` 验证

**Commit**：`fix(new-product-review): align update_product with new npr fields`

---

## Phase 4：Service 单元测试

### Task 12：`tests/test_appcore_new_product_review.py` — list_pending 测试

**文件**：`tests/test_appcore_new_product_review.py`（新建）

**用例**：
- `test_list_pending_filters_by_mk_id` — 无 mk_id 的产品不出现
- `test_list_pending_excludes_approved` — npr_decision_status='approved' 不出现
- `test_list_pending_excludes_rejected` — npr_decision_status='rejected' 不出现
- `test_list_pending_includes_failed_evaluation` — ai_evaluation_result='评估失败' + npr_decision_status NULL 出现
- `test_list_pending_orders_by_created_at_desc` — 排序

**注意**：本地无 MySQL，用 monkeypatch mock `appcore.db.query`，断言 SQL 字段 + 输出反序列化

**Commit**：`test(new-product-review): list_pending unit tests`

---

### Task 13：service unit tests — `_make_eval_clip_15s` + `_resolve_translator`

**文件**：`tests/test_appcore_new_product_review.py`（追加）

**用例**：
- `test_make_eval_clip_15s_creates_file` — mock subprocess.run 成功 + mock _materialize_media，验证产物路径 + 不重跑 ffmpeg
- `test_make_eval_clip_15s_falls_back_on_ffmpeg_failure` — mock subprocess returncode=1 → 返回原视频路径
- `test_make_eval_clip_15s_falls_back_on_ffmpeg_not_found` — mock FileNotFoundError → 返回原视频
- `test_make_eval_clip_15s_reuses_existing` — 产物已存在，不调 subprocess
- `test_resolve_translator_rejects_inactive` — is_active=0 → TranslatorInvalidError
- `test_resolve_translator_rejects_no_can_translate_perm` — permissions JSON 缺位 → TranslatorInvalidError
- `test_resolve_translator_accepts_valid` — 正常用户 → 返回

**Commit**：`test(new-product-review): clip + translator helper tests`

---

### Task 14：service unit tests — `evaluate_product`

**文件**：`tests/test_appcore_new_product_review.py`（追加）

**用例**：
- `test_evaluate_product_writes_back_ai_fields` — mock LLM 返回结构化 JSON，断言 update_product 调用含 ai_score / ai_evaluation_result / ai_evaluation_detail / npr_eval_clip_path / npr_decision_status='pending'
- `test_evaluate_product_handles_llm_failure` — mock LLM raise → ai_evaluation_result='评估失败' + raise EvaluationError
- `test_evaluate_product_no_video_raises` — mock _first_english_video 返回 None → NoVideoError
- `test_evaluate_product_product_not_found` — mock get_product → None → ProductNotFoundError
- `test_evaluate_product_preserves_decided_status` — 如果已 approved/rejected 不覆盖 npr_decision_status

**Commit**：`test(new-product-review): evaluate_product tests`

---

### Task 15：service unit tests — `decide_approve` / `decide_reject`

**文件**：`tests/test_appcore_new_product_review.py`（追加）

**用例**：
- `test_decide_approve_creates_task` — mock create_parent_task 返回 task_id，断言 update_product 被调
- `test_decide_approve_changes_owner_when_translator_differs` — translator_id != product.user_id → update_product_owner 调用
- `test_decide_approve_skips_owner_change_when_same` — 翻译员相同 → 不调 update_product_owner
- `test_decide_approve_no_countries_raises` — countries=[] → ValueError
- `test_decide_approve_already_approved` — npr_decision_status='approved' → InvalidStateError
- `test_decide_approve_already_rejected` — npr_decision_status='rejected' → InvalidStateError
- `test_decide_approve_invalid_translator` — TranslatorInvalidError 透传
- `test_decide_reject_writes_status_and_reason`
- `test_decide_reject_short_reason_raises` — len < 10 → ValueError
- `test_decide_reject_already_approved` — InvalidStateError

**Commit**：`test(new-product-review): decide tests`

---

### Task 16：跑全套 service unit tests + 修任何失败

**操作**：
```bash
cd .worktrees/new-product-review
python -m pytest tests/test_appcore_new_product_review.py -v
```

**通过标准**：所有 test 通过，无 import error。

**Commit**：（如果只是修小 bug 才 commit；否则不需要）

---

## Phase 5：Blueprint + 5 路由

### Task 17：创建 Blueprint `web/routes/new_product_review.py` + 注册

**文件**：
- `web/routes/new_product_review.py`（新建）
- `web/app.py` 或 `web/__init__.py` 等（注册 Blueprint，**实施时 grep 现有 Blueprint 注册位置**）

**内容（Blueprint 文件）**：
```python
"""B 子系统：新品审核 + AI 评估矩阵 路由。

详见 docs/superpowers/specs/2026-04-26-new-product-review-design.md
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, render_template, request

from appcore import medias, new_product_review, tasks
from appcore.permissions import require_admin

logger = logging.getLogger(__name__)

new_product_review_bp = Blueprint(
    "new_product_review",
    __name__,
    url_prefix="/new-product-review",
)
```

**注册**：在主 app 注册 `new_product_review_bp`。

**Commit**：`feat(new-product-review): blueprint scaffold + register`

---

### Task 18：实现 `GET /` 渲染页面

**文件**：`web/routes/new_product_review.py`

**内容**：
```python
@new_product_review_bp.route("/", methods=["GET"])
@require_admin
def index():
    products = new_product_review.list_pending(limit=200)
    languages = medias.list_enabled_languages_kv()
    translators = tasks.list_translators()  # 复用 C 的接口
    
    languages_dicts = [
        {"code": code, "code_upper": code.upper(), "name_zh": name}
        for code, name in languages
    ]
    
    return render_template(
        "new_product_review_list.html",
        products=products,
        languages=languages_dicts,
        translators=translators,
        active_tab="new_product_review",
    )
```

**注意**：实施时 grep `tasks.list_translators` 是否存在；如果没有则改成直接 `query("SELECT id, username FROM users WHERE ...")`。

**Commit**：`feat(new-product-review): GET / render page`

---

### Task 19：实现 `GET /api/list`

**文件**：`web/routes/new_product_review.py`

```python
@new_product_review_bp.route("/api/list", methods=["GET"])
@require_admin
def api_list():
    products = new_product_review.list_pending(limit=200)
    languages = [
        {"code": code, "code_upper": code.upper(), "name_zh": name}
        for code, name in medias.list_enabled_languages_kv()
    ]
    translators = tasks.list_translators()
    return jsonify({
        "products": products,
        "languages": languages,
        "translators": translators,
    })
```

**Commit**：`feat(new-product-review): GET /api/list endpoint`

---

### Task 20：实现 `POST /api/<id>/evaluate`

**文件**：`web/routes/new_product_review.py`

```python
from flask_login import current_user


@new_product_review_bp.route("/api/<int:product_id>/evaluate", methods=["POST"])
@require_admin
def api_evaluate(product_id):
    try:
        result = new_product_review.evaluate_product(
            product_id, actor_user_id=int(current_user.id)
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.NoVideoError as e:
        return jsonify({"error": "no_video", "detail": str(e)}), 422
    except new_product_review.EvaluationError as e:
        return jsonify({"error": "evaluation_failed", "detail": str(e)}), 500
    except Exception as e:
        logger.exception("api_evaluate unexpected error product=%s", product_id)
        return jsonify({"error": "internal", "detail": str(e)}), 500
```

**Commit**：`feat(new-product-review): POST /api/<id>/evaluate endpoint`

---

### Task 21：实现 `POST /api/<id>/decide` + `POST /api/<id>/reject`

**文件**：`web/routes/new_product_review.py`

```python
@new_product_review_bp.route("/api/<int:product_id>/decide", methods=["POST"])
@require_admin
def api_decide(product_id):
    payload = request.get_json(silent=True) or {}
    countries = payload.get("countries") or []
    translator_id = payload.get("translator_id")
    
    try:
        result = new_product_review.decide_approve(
            product_id,
            countries=countries,
            translator_id=int(translator_id) if translator_id else 0,
            actor_user_id=int(current_user.id),
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.InvalidStateError as e:
        return jsonify({"error": "already_decided", "detail": str(e)}), 422
    except new_product_review.TranslatorInvalidError as e:
        return jsonify({"error": "invalid_translator", "detail": str(e)}), 422
    except new_product_review.NoVideoError as e:
        return jsonify({"error": "no_video", "detail": str(e)}), 422
    except ValueError as e:
        return jsonify({"error": "no_countries", "detail": str(e)}), 422
    except Exception as e:
        logger.exception("api_decide unexpected error product=%s", product_id)
        return jsonify({"error": "task_create_failed", "detail": str(e)}), 500


@new_product_review_bp.route("/api/<int:product_id>/reject", methods=["POST"])
@require_admin
def api_reject(product_id):
    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason") or ""
    
    try:
        result = new_product_review.decide_reject(
            product_id,
            reason=reason,
            actor_user_id=int(current_user.id),
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.InvalidStateError as e:
        return jsonify({"error": "already_decided", "detail": str(e)}), 422
    except ValueError as e:
        return jsonify({"error": "reason_required", "detail": str(e)}), 422
    except Exception as e:
        logger.exception("api_reject unexpected error product=%s", product_id)
        return jsonify({"error": "internal", "detail": str(e)}), 500
```

**Commit**：`feat(new-product-review): POST decide + reject endpoints`

---

## Phase 6：路由集成测试

### Task 22：`tests/test_new_product_review_routes.py` — 列表 + 权限

**文件**：`tests/test_new_product_review_routes.py`（新建）

**用例**：
- `test_get_index_admin_only` — 普通用户 403，admin 200
- `test_get_index_renders_template` — 含 "新品审核" 文本
- `test_get_list_returns_json` — 字段齐
- `test_get_list_admin_only`

**fixture**：`authed_client_no_db`（admin） + `authed_user_client_no_db`（普通员工）

**Commit**：`test(new-product-review): index + list route tests`

---

### Task 23：`tests/test_new_product_review_routes.py` — evaluate

**用例**：
- `test_post_evaluate_admin_only`
- `test_post_evaluate_calls_service` — monkeypatch service.evaluate_product 返回 mock dict
- `test_post_evaluate_handles_no_video` — mock raise NoVideoError → 422
- `test_post_evaluate_handles_evaluation_error` — mock raise → 500

**Commit**：`test(new-product-review): evaluate route tests`

---

### Task 24：`tests/test_new_product_review_routes.py` — decide

**用例**：
- `test_post_decide_creates_task` — mock decide_approve 返回 {task_id, ...} → 200
- `test_post_decide_no_countries_returns_422`
- `test_post_decide_invalid_translator_returns_422`
- `test_post_decide_already_approved_returns_422`
- `test_post_decide_admin_only`

**Commit**：`test(new-product-review): decide route tests`

---

### Task 25：`tests/test_new_product_review_routes.py` — reject

**用例**：
- `test_post_reject_writes_status` — mock decide_reject → 200
- `test_post_reject_short_reason_returns_422`
- `test_post_reject_admin_only`

**Commit**：`test(new-product-review): reject route tests`

---

## Phase 7：模板 + UI

### Task 26：创建 `web/templates/new_product_review_list.html` 模板骨架

**文件**：`web/templates/new_product_review_list.html`（新建）

**结构**：
- 顶部页面 Tab 切换栏（"明控选品" / "新品审核"）
- 主表格（产品 / AI 综合 / 9 国列 / 翻译员 / 操作）
- 底部 modal 容器（国家选择 modal + reject reason modal + 单元格详情 modal）
- 行内 `<style>`（Ocean Blue 风格）
- 行内 `<script>` 初始化

**继承现有 base 模板**：grep 现有 `medias_list.html` 看 `{% extends ... %}` 用了什么。

**用 `--accent` / `--bg` / `--success` / `--danger` token**，参考 CLAUDE.md "Ocean Blue" 章节。

**Commit**：`feat(new-product-review): template skeleton + matrix table`

---

### Task 27：模板矩阵单元格 + hover tooltip + 详情 modal

**文件**：`web/templates/new_product_review_list.html`

**渲染矩阵单元格**：
- 未评估：灰 "—"
- 评估失败：红 "!"
- 评估成功 is_suitable=true：绿 ✓ + 小字 score
- 评估成功 is_suitable=false：灰 ✗ + 小字 score
- hover → tooltip 显示 score + reason 摘要（前 60 字）
- 点击 → modal 显示完整 reason + suggestions

**JS**：
- `nprBindCellHover()` + `nprBindCellClick()`
- tooltip 用绝对定位 div，hover 时移动到鼠标附近
- modal 用统一 `nprOpenModal(modal_id)` / `nprCloseModal(modal_id)`

**Commit**：`feat(new-product-review): matrix cell + tooltip + detail modal`

---

### Task 28：模板国家选择 modal + reason modal + 操作按钮 binding

**文件**：`web/templates/new_product_review_list.html`

**国家选择 modal**：
- 9 国 checkbox（动态从 `languages` 数组渲染）
- 每国旁显示 AI 推荐分数（hover dataset 已有）
- 翻译员下拉（动态从 `translators` 数组渲染）
- 打开时根据当前行 dataset 预勾 + 预选

**Reject reason modal**：
- textarea（min 10 字符 enable 确认）
- 字符计数

**JS**：
- `nprOpenApprove(productId)` + `nprConfirmApprove()`
- `nprOpenReject(productId)` + `nprConfirmReject()`
- `nprTriggerEval(productId)` 调 `/api/<id>/evaluate`
- 成功 → toast 绿 + 重新拉 `/api/list` 重渲染表格
- 失败 → toast 红 + 按钮复原

**Commit**：`feat(new-product-review): approve + reject modals + action binding`

---

### Task 29：改 `mk_selection.html` 顶部加页面 Tab 切换栏

**文件**：`web/templates/mk_selection.html`

**改动**：在 `<body>` 主内容开头加：
```html
<div class="oc-page-tabs">
  <a class="oc-page-tab active" href="/medias/mk-selection">明控选品</a>
  <a class="oc-page-tab" href="/new-product-review/">新品审核</a>
</div>
```

CSS 同 `new_product_review_list.html` 的 Ocean Blue 风格（共用样式可抽到 `web/static/oc-page-tabs.css`，YAGNI 暂行内）。

**Commit**：`feat(new-product-review): add page tabs to mk_selection`

---

## Phase 8：集成与冒烟

### Task 30：注册 Blueprint + 跑全套测试 + 修任何失败

**操作**：
1. 在主 app 注册 Blueprint（grep 现有 `app.register_blueprint(...)` 位置）
2. 运行全套测试：
   ```bash
   cd .worktrees/new-product-review
   python -m pytest tests/test_appcore_new_product_review.py tests/test_new_product_review_routes.py -v
   ```
3. 运行其他相关测试，确认无 regression：
   ```bash
   python -m pytest tests/test_medias_routes.py tests/test_appcore_medias.py tests/test_appcore_tasks.py -v
   ```
4. **不在本地跑** DB-dependent 测试（要 SSH 测试环境跑）

**最终 commit**：`feat(new-product-review): register blueprint + smoke pass`

**完成标志**：
- 所有 unit + route 测试本地通过
- worktree 上所有改动已 commit
- 准备好 merge 到 master + 部署测试环境

---

## 完成后的步骤（不在本 plan，由 outer 流程触发）

1. `cd G:/Code/AutoVideoSrtLocal && git merge feature/new-product-review --no-ff`
2. `git push origin master`
3. SSH 部署：
   ```bash
   ssh -i C:\Users\admin\.ssh\CC.pem ubuntu@172.30.254.14 \
       'cd /opt/autovideosrt && sudo git pull && sudo systemctl restart autovideosrt'
   ```
4. 验证：`curl -sI http://172.30.254.14/login` 应该 200
5. 浏览器打开 `http://172.30.254.14:8080/new-product-review/`，登录 admin/709709@，看到页面
6. 跑测试环境 SSH 端 pytest（B 子系统的 test 文件）
7. 回写 master 文档 + 需求文档 第 7 节
8. 删 worktree：
   ```bash
   git worktree remove .worktrees/new-product-review
   git branch -d feature/new-product-review  # 已 merge 安全删
   ```
9. 销 cron loop

---

## 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| ffmpeg 不在测试环境 PATH | 低 | _make_eval_clip_15s 已 fallback 原视频 |
| LLM 调用 504 | 中 | 服务端超时 240 秒；admin 重试 |
| `update_product` JSON 字段处理不兼容 npr_decided_countries | 低-中 | Task 11 专门核对 + 修正 |
| `tasks.list_translators` 不存在 | 低 | grep 验证；缺失则直接 SQL |
| `pushes.resolve_product_page_url` 缺失 | 低 | 已存在（material_evaluation 用过） |
| `permissions` JSON 在 user 表是 TEXT 还是 JSON | 中 | _resolve_translator 用 try/except 双兼容 |
| Blueprint 注册顺序 | 低 | 在 admin 路由后注册 |
| 已有 tasks_list.html 模板的 modal 冲突 ID | 低 | npr- 前缀全部 namespace |

---

## 实施模式

可两选一：

**A. 全自动 subagent-driven**：每个 task 派给一个独立 subagent，跑完 commit 给上级；上级跑下一个

**B. 半自动 + /loop 10m 自检**：在 worktree 主会话循环执行 task 1→30，每 10 分钟自动唤醒检查进度 + 修问题

本 plan 默认采用 **B**（外部已设 loop）。
