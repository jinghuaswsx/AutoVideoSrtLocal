"""TTS 语音生成步骤的统计汇总 + 持久化 + 日志。

每条任务跑完 _step_tts 后，会调用 finalize()，把以下两个核心指标写到：
1) projects.state_json.tts_generation_summary（详情页可读）
2) tts_generation_stats 独立表（聚合分析）
3) 一条粗体蓝色 ANSI 日志（journalctl 可读）

指标口径：
- translate_calls:      round 1 的 1 次初始翻译 + 每个 round 内所有 rewrite_attempt 之和
- audio_rounds:         实际进入完整音频合成的轮数
- audio_segment_calls:  所有 round 的 audio_segments_total 之和（段级 ElevenLabs 调用总数）
- audio_calls:          兼容旧字段，等同 audio_segment_calls
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from appcore.db import execute
from appcore.task_state import update as task_state_update

logger = logging.getLogger(__name__)

# ANSI 转义：粗体 + 蓝色，重置
_ANSI_BOLD_BLUE = "\033[1;34m"
_ANSI_RESET = "\033[0m"


def compute_summary(rounds: Iterable[dict]) -> dict:
    """从 _step_tts 的 rounds 列表汇总两个核心指标。"""
    rounds_list = list(rounds)
    if not rounds_list:
        return {
            "translate_calls": 0,
            "audio_rounds": 0,
            "audio_segment_calls": 0,
            "audio_calls": 0,
        }

    translate_calls = 0
    audio_rounds = 0
    audio_segment_calls = 0
    for idx, rec in enumerate(rounds_list):
        if idx == 0:
            translate_calls += 1
        else:
            translate_calls += len(rec.get("rewrite_attempts") or [])
        segment_calls = int(rec.get("audio_segments_total") or 0)
        audio_segment_calls += segment_calls
        if segment_calls > 0:
            audio_rounds += 1
    return {
        "translate_calls": translate_calls,
        "audio_rounds": audio_rounds,
        "audio_segment_calls": audio_segment_calls,
        "audio_calls": audio_segment_calls,
    }


def format_log_line(summary: dict) -> str:
    """构造一条粗体蓝色 ANSI 总结日志。"""
    audio_rounds = int(summary.get("audio_rounds") or 0)
    audio_segment_calls = int(
        summary.get("audio_segment_calls", summary.get("audio_calls", 0)) or 0
    )
    return (
        f"{_ANSI_BOLD_BLUE}"
        f"本任务用了 {summary['translate_calls']} 次文本翻译，"
        f"{audio_rounds} 轮语音生成，"
        f"{audio_segment_calls} 次分段语音合成。"
        f"{_ANSI_RESET}"
    )


_UPSERT_SQL = """
INSERT INTO tts_generation_stats
    (task_id, project_type, target_lang, user_id,
     translate_calls, audio_calls, finished_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    project_type    = VALUES(project_type),
    target_lang     = VALUES(target_lang),
    user_id         = VALUES(user_id),
    translate_calls = VALUES(translate_calls),
    audio_calls     = VALUES(audio_calls),
    finished_at     = VALUES(finished_at)
"""


def upsert(
    *,
    task_id: str,
    project_type: str,
    target_lang: str,
    user_id: int | None,
    summary: dict,
    finished_at_iso: str,
) -> None:
    """把汇总写入 tts_generation_stats（同 task_id 重复跑覆盖）。"""
    execute(
        _UPSERT_SQL,
        (
            task_id,
            project_type,
            target_lang,
            user_id,
            int(summary["translate_calls"]),
            int(summary["audio_calls"]),
            finished_at_iso,
        ),
    )


def finalize(*, task_id: str, task: dict, rounds: list[dict]) -> None:
    """_step_tts 主循环 return 之前调用一次：算 summary、写 state_json、写 DB、打日志。

    任何 DB 异常都被记录为 warning，不抛出（不阻断主流程）。
    """
    summary = compute_summary(rounds)
    finished_at_iso = datetime.now().replace(microsecond=0).isoformat()
    summary_with_ts = {**summary, "finished_at": finished_at_iso}

    task_state_update(task_id, tts_generation_summary=summary_with_ts)

    logger.info(format_log_line(summary))

    try:
        upsert(
            task_id=task_id,
            project_type=str(task.get("type") or ""),
            target_lang=str(task.get("target_lang") or ""),
            user_id=task.get("user_id"),
            summary=summary,
            finished_at_iso=finished_at_iso,
        )
    except Exception as exc:
        logger.warning("tts_generation_stats upsert failed: %s", exc)
