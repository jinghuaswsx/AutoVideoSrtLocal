"""TTS 变速短路质量评估 orchestrator。

业务流程：duration loop 跑完变速 pass 后，同步调用 run_evaluation：
1. INSERT 一行 status=pending（占位）
2. concurrent.futures 包 EVAL_TIMEOUT_SECONDS 调 llm_client.invoke_generate
3. UPDATE 该行成 ok/failed + scores + 模型信息

后续 admin 可在跨任务页点"重新评估"触发 retry_evaluation（只更新 score 字段）。

设计点：
- 评估失败永远不向上抛，只写 status=failed，让任务正常返回收敛结果
- audio_pre_path / audio_post_path 是 ElevenLabs 输出的绝对路径，
  直接喂给 invoke_generate(media=...) 让 Gemini 多模态读取
- llm_client.invoke_generate 没有原生 timeout，用 ThreadPoolExecutor.submit().result(timeout=...)
- timeout/异常路径用 pool.shutdown(wait=False)：worker 线程会泄漏直到 LLM 调用自然
  终止（_call_with_network_retry 上限 ~14s），但调用方等待时间硬 cap 到
  EVAL_TIMEOUT_SECONDS，不会因 LLM 卡住而拖累整个 TTS pipeline
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from appcore import llm_client
from appcore.db import execute as db_execute, query_one as db_query_one

log = logging.getLogger(__name__)

USE_CASE_CODE = "video_translate.tts_speedup_quality_review"
EVAL_TIMEOUT_SECONDS = 120  # 双音频多模态评估，留充足余量

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score_naturalness":     {"type": "integer", "minimum": 1, "maximum": 5},
        "score_pacing":          {"type": "integer", "minimum": 1, "maximum": 5},
        "score_timbre":          {"type": "integer", "minimum": 1, "maximum": 5},
        "score_intelligibility": {"type": "integer", "minimum": 1, "maximum": 5},
        "score_overall":         {"type": "integer", "minimum": 1, "maximum": 5},
        "summary":               {"type": "string"},
        "flags":                 {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "score_naturalness", "score_pacing", "score_timbre",
        "score_intelligibility", "score_overall", "summary", "flags",
    ],
}


def _build_prompt(
    *, language: str, speed_ratio: float,
    video_duration: float,
    audio_pre_duration: float, audio_post_duration: float,
    hit_final_range: bool,
) -> str:
    return (
        f"你是带货视频配音质量评审。系统在 TTS 时长收敛流程中尝试用 ElevenLabs "
        f"voice_settings.speed={speed_ratio:.4f} 把目标语言（{language}）配音从 "
        f"{audio_pre_duration:.2f}s 调整到 {audio_post_duration:.2f}s，"
        f"目标视频时长 {video_duration:.2f}s，"
        f"{'变速后已落入最终收敛区间' if hit_final_range else '变速后仍偏离最终收敛区间'}。\n\n"
        "请对比附带的两段音频（第一段=变速前原始合成，第二段=变速重生成）"
        "并按 1-5 分输出五维评分（5 最好）：\n"
        "- naturalness：人声自然度（机械感/鸭嗓/chipmunk 越强分越低）\n"
        "- pacing：节奏稳定性（拖音/卡顿/时间拉伸抖动）\n"
        "- timbre：音色保留度（变速后是否还像同一个人）\n"
        "- intelligibility：可懂度（母语听众能否清晰理解每个词）\n"
        "- overall：整体是否愿意发布\n\n"
        "summary 用中文写一段总结（≤120 字）。"
        "flags 是问题点的英文短标签数组（如 chipmunk_effect / tail_wobble / "
        "pace_jitter / muffled_consonant），无问题给空数组。"
    )


def run_evaluation(
    *,
    task_id: str,
    round_index: int,
    language: str,
    video_duration: float,
    audio_pre_path: str,
    audio_pre_duration: float,
    audio_post_path: str,
    audio_post_duration: float,
    speed_ratio: float,
    hit_final_range: bool,
    user_id: int | None,
) -> int:
    """同步执行评估。返回 eval_id。永远不抛异常 — 失败也写 status=failed 行。"""
    try:
        db_execute(
            """
            INSERT INTO tts_speedup_evaluations
              (task_id, round_index, language, video_duration,
               audio_pre_duration, audio_post_duration, speed_ratio, hit_final_range,
               audio_pre_path, audio_post_path, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
              audio_pre_duration=VALUES(audio_pre_duration),
              audio_post_duration=VALUES(audio_post_duration),
              speed_ratio=VALUES(speed_ratio),
              hit_final_range=VALUES(hit_final_range),
              audio_pre_path=VALUES(audio_pre_path),
              audio_post_path=VALUES(audio_post_path),
              status='pending', error_text=NULL,
              score_naturalness=NULL, score_pacing=NULL, score_timbre=NULL,
              score_intelligibility=NULL, score_overall=NULL, summary_text=NULL,
              flags_json=NULL, llm_input_tokens=NULL, llm_output_tokens=NULL,
              llm_cost_usd=NULL, evaluated_at=NULL
            """,
            (task_id, round_index, language, video_duration,
             audio_pre_duration, audio_post_duration, speed_ratio,
             1 if hit_final_range else 0,
             audio_pre_path, audio_post_path),
        )
        row = db_query_one(
            "SELECT id FROM tts_speedup_evaluations WHERE task_id=%s AND round_index=%s",
            (task_id, round_index),
        )
        eval_id: int = int(row["id"]) if row else 0
    except Exception:
        log.exception("[tts_speedup_eval] failed to insert/select eval row for task %s round %s",
                      task_id, round_index)
        return 0

    if eval_id == 0:
        return 0

    prompt = _build_prompt(
        language=language, speed_ratio=speed_ratio,
        video_duration=video_duration,
        audio_pre_duration=audio_pre_duration,
        audio_post_duration=audio_post_duration,
        hit_final_range=hit_final_range,
    )

    def _do_call():
        return llm_client.invoke_generate(
            USE_CASE_CODE,
            prompt=prompt,
            user_id=user_id,
            project_id=task_id,
            media=[audio_pre_path, audio_post_path],
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
        )

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_do_call)
    try:
        result = future.result(timeout=EVAL_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        pool.shutdown(wait=False)  # 不等 worker，超时即返回；worker 线程会自然终止
        log.warning("[tts_speedup_eval] timeout for task %s round %s", task_id, round_index)
        _write_failed(eval_id, f"timeout after {EVAL_TIMEOUT_SECONDS}s")
        return eval_id
    except Exception as exc:
        pool.shutdown(wait=False)
        log.exception("[tts_speedup_eval] LLM error for task %s round %s",
                      task_id, round_index)
        _write_failed(eval_id, str(exc)[:1000])
        return eval_id
    pool.shutdown(wait=False)  # happy path 也不等：worker 已 done

    _write_ok(eval_id, result)
    return eval_id


def retry_evaluation(*, eval_id: int, user_id: int | None) -> bool:
    """对已存在的 eval 行重跑 LLM 调用。成功返回 True。"""
    row = db_query_one(
        """SELECT id, task_id, round_index, language, video_duration,
                  audio_pre_path, audio_post_path,
                  audio_pre_duration, audio_post_duration,
                  speed_ratio, hit_final_range
           FROM tts_speedup_evaluations WHERE id=%s""",
        (eval_id,),
    )
    if not row:
        return False
    db_execute(
        """UPDATE tts_speedup_evaluations
           SET status='pending', error_text=NULL,
               score_naturalness=NULL, score_pacing=NULL, score_timbre=NULL,
               score_intelligibility=NULL, score_overall=NULL,
               summary_text=NULL, flags_json=NULL,
               llm_input_tokens=NULL, llm_output_tokens=NULL,
               llm_cost_usd=NULL, evaluated_at=NULL
           WHERE id=%s""",
        (eval_id,),
    )
    prompt = _build_prompt(
        language=row["language"],
        speed_ratio=float(row["speed_ratio"]),
        video_duration=float(row["video_duration"]),
        audio_pre_duration=float(row["audio_pre_duration"]),
        audio_post_duration=float(row["audio_post_duration"]),
        hit_final_range=bool(row["hit_final_range"]),
    )

    def _do_call():
        return llm_client.invoke_generate(
            USE_CASE_CODE,
            prompt=prompt,
            user_id=user_id,
            project_id=row["task_id"],
            media=[row["audio_pre_path"], row["audio_post_path"]],
            response_schema=RESPONSE_SCHEMA,
            temperature=0.2,
        )

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_do_call)
    try:
        result = future.result(timeout=EVAL_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        pool.shutdown(wait=False)  # 不等 worker，超时即返回；worker 线程会自然终止
        _write_failed(eval_id, f"timeout after {EVAL_TIMEOUT_SECONDS}s")
        return False
    except Exception as exc:
        pool.shutdown(wait=False)
        _write_failed(eval_id, str(exc)[:1000])
        return False
    pool.shutdown(wait=False)  # happy path 也不等：worker 已 done

    _write_ok(eval_id, result)
    return True


def _write_ok(eval_id: int, result: dict) -> None:
    payload = result.get("json") or {}
    usage = result.get("usage") or {}
    binding = _resolve_binding_for_log()
    try:
        db_execute(
            """UPDATE tts_speedup_evaluations
               SET status=%s, error_text=NULL,
                   score_naturalness=%s, score_pacing=%s, score_timbre=%s,
                   score_intelligibility=%s, score_overall=%s,
                   summary_text=%s, flags_json=%s,
                   model_provider=%s, model_id=%s,
                   llm_input_tokens=%s, llm_output_tokens=%s, llm_cost_usd=%s,
                   evaluated_at=CURRENT_TIMESTAMP
               WHERE id=%s""",
            (
                "ok",
                payload.get("score_naturalness"), payload.get("score_pacing"),
                payload.get("score_timbre"), payload.get("score_intelligibility"),
                payload.get("score_overall"),
                payload.get("summary") or "",
                json.dumps(payload.get("flags") or [], ensure_ascii=False),
                binding["provider"], binding["model"],
                usage.get("input_tokens"), usage.get("output_tokens"),
                usage.get("cost_usd"),
                eval_id,
            ),
        )
    except Exception:
        log.exception("[tts_speedup_eval] _write_ok DB write failed for eval_id=%s", eval_id)


def _write_failed(eval_id: int, error_text: str) -> None:
    binding = _resolve_binding_for_log()
    try:
        db_execute(
            """UPDATE tts_speedup_evaluations
               SET status=%s, error_text=%s,
                   model_provider=%s, model_id=%s,
                   evaluated_at=CURRENT_TIMESTAMP
               WHERE id=%s""",
            ("failed", error_text, binding["provider"], binding["model"], eval_id),
        )
    except Exception:
        log.exception("[tts_speedup_eval] _write_failed DB write failed for eval_id=%s", eval_id)


def _resolve_binding_for_log() -> dict:
    """提前 resolve binding 以便记录实际使用的 provider/model（即便后续 LLM 调用失败）。"""
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(USE_CASE_CODE)
    except Exception:
        return {"provider": "unknown", "model": "unknown"}
