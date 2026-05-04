"""AI 视频分析 service：trigger + 后台 worker + DB 落库。

目前支持两种 source_type：
  - multi_translate_task：从 task_state.get(task_id) 抽取源/目标视频和文案
  - media_item：从 media_items + media_raw_sources 表抽取（Phase C 接入）

参考 appcore.quality_assessment 的实现范式（pending row → thread → done row）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from appcore import runner_lifecycle, task_state
from appcore.db import execute as db_execute, query_one as db_query_one
from pipeline import video_ai_review

log = logging.getLogger(__name__)

CHANNEL = "gemini_vertex_adc"
MODEL = "gemini-3.1-pro-preview"


class ReviewInProgressError(RuntimeError):
    def __init__(self, run_id: int):
        super().__init__(f"video_ai_review in progress (run_id={run_id})")
        self.run_id = run_id


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------

def _build_inputs_for_task(task_id: str) -> dict:
    """从 multi_translate task_state 抽取 AI 视频分析需要的资料。

    返回字段：
      - source_language / target_language
      - source_text （ASR 后的源语言文案）
      - target_text （localized_translation.full_text）
      - source_video_path （task_dir/source.mp4 之类）
      - target_video_path （compose 后的成品；不强求）
      - product_info / product_image_paths（multi_translate 没有，留空）
    """
    task = task_state.get(task_id)
    if not task:
        raise RuntimeError(f"task {task_id} not found")

    utterances = task.get("utterances") or []
    source_text = " ".join(
        (u.get("text") or "").strip() for u in utterances if u.get("text")
    ).strip()

    loc = task.get("localized_translation") or {}
    target_text = (loc.get("full_text") or "").strip()
    if not target_text:
        sentences = loc.get("sentences") or []
        target_text = " ".join(
            (s.get("text") or "").strip() for s in sentences if s.get("text")
        ).strip()

    source_language = (
        task.get("detected_source_language")
        or task.get("source_language")
        or ""
    )
    target_language = task.get("target_lang") or ""

    task_dir = task.get("task_dir") or ""
    source_video_path = None
    target_video_path = None
    if task_dir and os.path.isdir(task_dir):
        # 源视频：extract step 落盘的原视频。常见命名 source.mp4 / video.mp4 / original.mp4。
        for cand in ("source.mp4", "video.mp4", "original.mp4", "input.mp4"):
            p = os.path.join(task_dir, cand)
            if os.path.isfile(p):
                source_video_path = p
                break
        if not source_video_path:
            video_path = task.get("video_path")
            if video_path and os.path.isfile(video_path):
                source_video_path = video_path
        # 目标视频：compose 产物。优先 hard_video_<variant>.mp4 / final_video.mp4
        for cand in (
            "hard_video_normal.mp4", "hard_video.mp4",
            "final_video_normal.mp4", "final_video.mp4",
            "composed_normal.mp4", "composed.mp4",
        ):
            p = os.path.join(task_dir, cand)
            if os.path.isfile(p):
                target_video_path = p
                break

    return {
        "source_language": source_language,
        "target_language": target_language,
        "source_text": source_text,
        "target_text": target_text,
        "source_video_path": source_video_path,
        "target_video_path": target_video_path,
        "product_info": None,
        "product_image_paths": [],
    }


def _build_inputs_for_media(media_item_id: int) -> dict:
    """从 media_items + media_raw_sources 抽取。Phase C 实现。"""
    raise NotImplementedError("media_item video_ai_review not yet supported")


_TRANSLATE_TASK_SOURCE_TYPES = (
    "multi_translate_task",
    "omni_translate_task",
    "av_sync_task",
)


def _build_inputs(source_type: str, source_id: str) -> dict:
    # 三种翻译型 task（multi / omni / av_sync）的 task_state 字段（utterances /
    # localized_translation / target_lang / detected_source_language / task_dir）
    # 结构通用，共享同一个 _build_inputs_for_task 实现；source_type 仅用于 DB
    # 表里区分历史归类，不影响输入抽取。
    if source_type in _TRANSLATE_TASK_SOURCE_TYPES:
        return _build_inputs_for_task(source_id)
    if source_type == "media_item":
        return _build_inputs_for_media(int(source_id))
    raise ValueError(f"unknown source_type: {source_type}")


# ---------------------------------------------------------------------------
# Submitted-inputs snapshot (for Modal display)
# ---------------------------------------------------------------------------

def _snapshot_for_db(inputs: dict) -> dict:
    """提交资料快照——文件用 path + size + duration 描述，文本截断到合理长度。"""
    def file_meta(path: str | None) -> dict | None:
        if not path or not os.path.isfile(path):
            return None
        try:
            return {
                "path": path,
                "name": os.path.basename(path),
                "size_bytes": os.path.getsize(path),
            }
        except Exception:
            return {"path": path}

    return {
        "source_language": inputs.get("source_language"),
        "target_language": inputs.get("target_language"),
        "source_text": (inputs.get("source_text") or "")[:8000],
        "target_text": (inputs.get("target_text") or "")[:8000],
        "source_video":  file_meta(inputs.get("source_video_path")),
        "target_video":  file_meta(inputs.get("target_video_path")),
        "product_info":  inputs.get("product_info"),
        "product_images": [
            file_meta(p) for p in (inputs.get("product_image_paths") or [])
        ],
    }


# ---------------------------------------------------------------------------
# DB / next-run helpers
# ---------------------------------------------------------------------------

def _next_run_id(source_type: str, source_id: str) -> int:
    row = db_query_one(
        "SELECT MAX(run_id) AS max_run FROM video_ai_reviews "
        "WHERE source_type=%s AND source_id=%s",
        (source_type, source_id),
    )
    return (row["max_run"] or 0) + 1 if row else 1


def _has_in_flight(source_type: str, source_id: str) -> int | None:
    row = db_query_one(
        "SELECT run_id FROM video_ai_reviews "
        "WHERE source_type=%s AND source_id=%s AND status IN ('pending', 'running')",
        (source_type, source_id),
    )
    return row["run_id"] if row else None


def latest_review(source_type: str, source_id: str) -> dict | None:
    row = db_query_one(
        "SELECT * FROM video_ai_reviews "
        "WHERE source_type=%s AND source_id=%s "
        "ORDER BY run_id DESC LIMIT 1",
        (source_type, source_id),
    )
    if not row:
        return None
    return _row_to_payload(row)


def _row_to_payload(row: dict) -> dict:
    def jload(val):
        if val is None or val == "":
            return None
        if isinstance(val, (dict, list)):
            return val
        try:
            return json.loads(val)
        except Exception:
            return None
    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "run_id": row["run_id"],
        "status": row["status"],
        "channel": row.get("channel"),
        "model": row.get("model"),
        "triggered_by": row.get("triggered_by"),
        "submitted_inputs": jload(row.get("submitted_inputs")),
        "prompt_text": row.get("prompt_text"),
        "raw_response": jload(row.get("raw_response")),
        "overall_score": row.get("overall_score"),
        "dimensions": jload(row.get("dimensions")),
        "verdict": row.get("verdict"),
        "verdict_reason": row.get("verdict_reason"),
        "issues": jload(row.get("issues")) or [],
        "highlights": jload(row.get("highlights")) or [],
        "request_duration_ms": row.get("request_duration_ms"),
        "started_at": row.get("started_at").isoformat() if row.get("started_at") else None,
        "completed_at": row.get("completed_at").isoformat() if row.get("completed_at") else None,
        "error_text": row.get("error_text"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }


# ---------------------------------------------------------------------------
# Trigger + worker
# ---------------------------------------------------------------------------

def trigger_review(
    *,
    source_type: str,
    source_id: str,
    user_id: int | None,
    triggered_by: str = "manual",
    run_in_thread: bool = True,
) -> int:
    in_flight = _has_in_flight(source_type, source_id)
    if in_flight is not None:
        raise ReviewInProgressError(in_flight)

    run_id = _next_run_id(source_type, source_id)
    db_execute(
        "INSERT INTO video_ai_reviews "
        "(source_type, source_id, run_id, status, channel, model, "
        " triggered_by, triggered_by_user_id) "
        "VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)",
        (source_type, source_id, run_id, CHANNEL, MODEL, triggered_by, user_id),
    )

    if run_in_thread:
        try:
            started = runner_lifecycle.start_tracked_thread(
                project_type="video_ai_review",
                task_id=f"{source_type}:{source_id}:{run_id}",
                target=_run_review_job,
                kwargs={
                    "source_type": source_type, "source_id": source_id,
                    "run_id": run_id, "user_id": user_id,
                },
                daemon=False,
                user_id=user_id,
                runner="appcore.video_ai_review._run_review_job",
                entrypoint="video_ai_review.trigger",
                stage="queued_review",
                details={"run_id": run_id, "source_type": source_type},
            )
        except BaseException as exc:
            db_execute(
                "UPDATE video_ai_reviews SET status='failed', error_text=%s, "
                "completed_at=NOW() WHERE source_type=%s AND source_id=%s AND run_id=%s",
                (str(exc), source_type, source_id, run_id),
            )
            raise
        if not started:
            db_execute(
                "UPDATE video_ai_reviews SET status='failed', error_text=%s, "
                "completed_at=NOW() WHERE source_type=%s AND source_id=%s AND run_id=%s",
                ("review thread already running", source_type, source_id, run_id),
            )
            raise ReviewInProgressError(run_id)
    return run_id


def _run_review_job(
    *, source_type: str, source_id: str, run_id: int, user_id: int | None,
) -> None:
    db_execute(
        "UPDATE video_ai_reviews SET status='running', started_at=NOW() "
        "WHERE source_type=%s AND source_id=%s AND run_id=%s",
        (source_type, source_id, run_id),
    )
    try:
        inputs = _build_inputs(source_type, source_id)
        snapshot = _snapshot_for_db(inputs)
        if not inputs.get("source_text") or not inputs.get("target_text"):
            raise RuntimeError("missing source_text or target_text")

        # 立刻把 submitted_inputs 写进去，让 Modal 在 running 阶段就能看到
        db_execute(
            "UPDATE video_ai_reviews SET submitted_inputs=%s "
            "WHERE source_type=%s AND source_id=%s AND run_id=%s",
            (json.dumps(snapshot, ensure_ascii=False),
             source_type, source_id, run_id),
        )

        result = video_ai_review.assess(
            source_language=inputs["source_language"],
            target_language=inputs["target_language"],
            source_text=inputs["source_text"],
            target_text=inputs["target_text"],
            source_video_path=inputs.get("source_video_path"),
            target_video_path=inputs.get("target_video_path"),
            product_info=inputs.get("product_info"),
            product_image_paths=inputs.get("product_image_paths") or [],
            task_id=source_id,
            user_id=user_id,
        )

        db_execute(
            "UPDATE video_ai_reviews SET "
            "  status='done', "
            "  prompt_text=%s, raw_response=%s, "
            "  overall_score=%s, dimensions=%s, verdict=%s, verdict_reason=%s, "
            "  issues=%s, highlights=%s, "
            "  request_duration_ms=%s, completed_at=NOW() "
            "WHERE source_type=%s AND source_id=%s AND run_id=%s",
            (
                result["system_prompt"] + "\n\n--- USER ---\n" + result["user_text"],
                json.dumps(result["raw_response"], ensure_ascii=False),
                result["overall_score"],
                json.dumps(result["dimensions"], ensure_ascii=False),
                result["verdict"], result["verdict_reason"],
                json.dumps(result["issues"], ensure_ascii=False),
                json.dumps(result["highlights"], ensure_ascii=False),
                result["elapsed_ms"],
                source_type, source_id, run_id,
            ),
        )
        log.info(
            "[video_ai_review] %s/%s run=%d done score=%s verdict=%s elapsed_ms=%s",
            source_type, source_id, run_id,
            result["overall_score"], result["verdict"], result["elapsed_ms"],
        )
    except Exception as exc:
        log.exception("[video_ai_review] %s/%s run=%d failed", source_type, source_id, run_id)
        db_execute(
            "UPDATE video_ai_reviews SET status='failed', error_text=%s, "
            "completed_at=NOW() WHERE source_type=%s AND source_id=%s AND run_id=%s",
            (str(exc)[:5000], source_type, source_id, run_id),
        )
