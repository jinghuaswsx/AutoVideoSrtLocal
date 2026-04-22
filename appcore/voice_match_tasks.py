"""
视频匹配任务管理（内存态，带 TTL 清理）。

任务只在进程内保存，上传视频和抽样音频保留在本地目录，
过期后由本模块统一清理。
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

TTL_SECONDS = 30 * 60
_CLEANUP_INTERVAL = 60
_MAX_WORKERS = 2

_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
_UPLOAD_ROOT = Path("uploads").resolve()


def _extract_sample_clip(video_path: str, out_dir: str) -> str:
    from pipeline.voice_match import extract_sample_clip

    return extract_sample_clip(video_path, out_dir=out_dir)


def _embed_audio_file(path: str):
    from pipeline.voice_embedding import embed_audio_file

    return embed_audio_file(path)


def _match_candidates(vec, *, language, gender, top_k):
    from pipeline.voice_match import match_candidates

    return match_candidates(vec, language=language, gender=gender, top_k=top_k)


def create_task(*, user_id: int, source_video_path: str, language: str, gender: str) -> str:
    task_id = "vm_" + uuid.uuid4().hex
    work_dir = str(Path(source_video_path).parent)
    with _LOCK:
        _TASKS[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "language": language,
            "gender": gender,
            "status": "pending",
            "progress": 0,
            "error": None,
            "result": None,
            "_source_video_path": source_video_path,
            "_work_dir": work_dir,
            "_expires_at": time.time() + TTL_SECONDS,
        }
    _EXECUTOR.submit(_run_task_sync, task_id)
    return task_id


def get_task(task_id: str, *, user_id: int) -> Optional[dict]:
    with _LOCK:
        t = _TASKS.get(task_id)
        if t and t.get("user_id") == user_id:
            return {k: v for k, v in t.items() if not k.startswith("_")}
    return None


def _set(task_id: str, **updates) -> None:
    with _LOCK:
        if task_id in _TASKS:
            _TASKS[task_id].update(updates)


def _run_task_sync(task_id: str) -> None:
    with _LOCK:
        task = dict(_TASKS.get(task_id) or {})
    if not task:
        return

    source_video_path = (task.get("_source_video_path") or "").strip()
    work_dir = Path(task.get("_work_dir") or "").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        _set(task_id, status="sampling", progress=10)
        if not source_video_path or not Path(source_video_path).is_file():
            raise RuntimeError("uploaded video file missing")
        clip_wav = _extract_sample_clip(source_video_path, out_dir=str(work_dir))

        _set(task_id, status="embedding", progress=40)
        vec = _embed_audio_file(clip_wav)

        _set(task_id, status="matching", progress=70)
        candidates = _match_candidates(
            vec,
            language=task["language"],
            gender=task["gender"],
            top_k=3,
        )
        if not candidates:
            raise RuntimeError("该语种声音库尚未同步，请联系管理员")

        _set(
            task_id,
            status="done",
            progress=100,
            result={
                "sample_audio_path": clip_wav,
                "candidates": candidates,
            },
        )
    except Exception as exc:
        log.exception("voice match task %s failed", task_id)
        _set(task_id, status="failed", progress=100, error=str(exc))


def _cleanup_task_files(task: dict[str, Any]) -> None:
    work_dir = (task.get("_work_dir") or "").strip()
    if not work_dir:
        return
    path = Path(work_dir).resolve()
    try:
        path.relative_to(_UPLOAD_ROOT)
    except Exception:
        return
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_expired() -> None:
    now = time.time()
    expired_tasks: list[dict[str, Any]] = []
    with _LOCK:
        for tid, task in list(_TASKS.items()):
            if task.get("_expires_at", 0) <= now:
                expired_tasks.append(_TASKS.pop(tid))
    for task in expired_tasks:
        _cleanup_task_files(task)


def _cleanup_loop() -> None:
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        try:
            _cleanup_expired()
        except Exception:
            log.warning("voice match TTL cleanup failed", exc_info=True)


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="vmt-cleanup")
_cleanup_thread.start()
