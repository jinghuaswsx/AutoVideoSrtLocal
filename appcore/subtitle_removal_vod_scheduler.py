"""字幕移除 VOD 定时接力任务。

APScheduler 每 60s 扫一次 DB 里未完成的 VOD 字幕擦除任务，负责推进：
- poll 阶段：调 GetExecution → Success 更新产物 Vid
- download_result 阶段：调 GetPlayInfo → 拿到可播放 URL（Status=1000/转码中视为未就绪，继续等）

与 runner 线程解耦：runner 只负责 submit，之后任何进程重启都由 scheduler 接力。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from appcore import task_state
from appcore.db import query as db_query
from appcore.vod_erase_provider import (
    VodEraseError,
    get_execution,
    get_play_info,
)

log = logging.getLogger(__name__)


def _iter_pending_vod_tasks():
    rows = db_query(
        "SELECT id, state_json FROM projects "
        "WHERE type = 'subtitle_removal' AND status IN ('queued', 'running') AND deleted_at IS NULL"
    )
    for row in rows or []:
        state = {}
        if row.get("state_json"):
            try:
                state = json.loads(row["state_json"])
            except Exception:
                continue
        steps = state.get("steps") or {}
        # 只处理跑到 poll 阶段或更后的任务；submit 阶段由 runner 负责
        if (steps.get("submit") or "") != "done":
            continue
        # provider_task_id（RunId）或 vod_result_vid 任一存在即可推进
        if not (state.get("provider_task_id") or state.get("vod_result_vid")):
            continue
        yield row["id"], state


def _advance_poll(task_id: str, state: dict) -> None:
    """GetExecution → Success 则更新产物 Vid，标 poll=done；失败则标 error。"""
    run_id = (state.get("provider_task_id") or "").strip()
    if not run_id:
        return
    result = get_execution(run_id)
    status = (result.get("Status") or "").strip().lower()
    task_state.update(
        task_id,
        provider_status=status,
        last_polled_at=datetime.now().isoformat(timespec="seconds"),
        poll_attempts=int((task_state.get(task_id) or {}).get("poll_attempts") or 0) + 1,
    )
    if status == "success":
        erase = (((result.get("Output") or {}).get("Task") or {}).get("Erase") or {})
        file_info = erase.get("File") or {}
        task_state.update(
            task_id,
            provider_raw=result,
            vod_result_vid=file_info.get("Vid") or "",
            vod_result_file_name=file_info.get("FileName") or "",
            vod_result_size=int(file_info.get("Size") or 0),
            vod_result_duration=float(erase.get("Duration") or 0.0),
        )
        task_state.set_step(task_id, "poll", "done")
        task_state.set_step_message(task_id, "poll", "字幕擦除完成")
    elif status in {"failed", "cancelled", "canceled", "error"}:
        task_state.update(task_id, status="error", error=f"GetExecution terminal {status}")
        task_state.set_step(task_id, "poll", "error")
        task_state.set_step_message(task_id, "poll", f"擦除任务失败: {status}")


def _advance_play_url(task_id: str, state: dict) -> None:
    """GetPlayInfo → 拿到可播放 URL（带 auth_key）则标 done；未就绪就继续等。"""
    vid = (state.get("vod_result_vid") or "").strip()
    if not vid:
        return
    info = get_play_info(vid)
    play_list = info.get("PlayInfoList") or []
    vod_status = info.get("Status")
    main_url = ""
    if isinstance(play_list, list) and play_list:
        first = play_list[0] if isinstance(play_list[0], dict) else {}
        main_url = first.get("MainPlayUrl") or first.get("BackupPlayUrl") or ""
    if not main_url:
        # Status=1000 或 PlayInfoList 空：VOD 转码还在跑，留给下一轮 tick
        task_state.set_step_message(
            task_id,
            "download_result",
            f"VOD 转码中（Status={vod_status}），等待可播放 URL 就绪…",
        )
        task_state.set_step(task_id, "download_result", "running")
        return
    task_state.update(
        task_id,
        provider_result_url=main_url,
        result_object_info={
            "source": "vod",
            "vid": vid,
            "file_name": state.get("vod_result_file_name") or "",
            "play_url": main_url,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        },
        status="done",
    )
    task_state.set_step(task_id, "download_result", "done")
    task_state.set_step_message(task_id, "download_result", "已获取结果播放地址")
    task_state.set_step(task_id, "upload_result", "done")
    task_state.set_step_message(task_id, "upload_result", "VOD 托管产物，无需回传 TOS")


def tick_once() -> None:
    """APScheduler job 入口：扫一次 DB，推进每个任务的下一步。"""
    import config

    provider = (getattr(config, "SUBTITLE_REMOVAL_PROVIDER", "goodline") or "").strip().lower()
    if provider != "vod":
        return
    for task_id, state in _iter_pending_vod_tasks():
        steps = state.get("steps") or {}
        try:
            if (steps.get("poll") or "") != "done":
                _advance_poll(task_id, state)
                # 推进后如果 poll 没 done，下一轮再看（不要同 tick 内继续追）
                fresh = task_state.get(task_id) or {}
                if ((fresh.get("steps") or {}).get("poll") or "") != "done":
                    continue
                state = fresh
            if ((state.get("steps") or {}).get("download_result") or "") != "done":
                _advance_play_url(task_id, state)
        except VodEraseError as exc:
            log.warning("[sr_vod_scheduler] VOD API error task_id=%s: %s", task_id, exc)
        except Exception:
            log.exception("[sr_vod_scheduler] unexpected error task_id=%s", task_id)


def register(scheduler) -> None:
    """注册到 APScheduler，每 60 秒执行一次。"""
    scheduler.add_job(
        tick_once,
        "interval",
        seconds=60,
        id="subtitle_removal_vod_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
