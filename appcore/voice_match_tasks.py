"""
视频匹配任务管理（内存态，带 TTL 清理）。

任务只在进程内存里保存，用户关页面或进程重启即作废。
重度依赖（ffmpeg / resemblyzer / TOS）以可 mock 的方式注入，便于测试。
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


# --------------- 注入点（测试 monkeypatch 用） ---------------

def _download_tos_to_local(object_key: str, dest_path: str) -> str:
    from appcore.tos_clients import download_file
    download_file(object_key, dest_path)
    return dest_path


def _upload_to_tos_signed(local_path: str, object_key: str) -> str:
    from appcore.tos_clients import upload_file, generate_signed_download_url
    upload_file(local_path, object_key)
    return generate_signed_download_url(object_key, expires=3600)


def _extract_sample_clip(video_path: str, out_dir: str) -> str:
    from pipeline.voice_match import extract_sample_clip
    return extract_sample_clip(video_path, out_dir=out_dir)


def _embed_audio_file(path: str):
    from pipeline.voice_embedding import embed_audio_file
    return embed_audio_file(path)


def _match_candidates(vec, *, language, gender, top_k):
    from pipeline.voice_match import match_candidates
    return match_candidates(vec, language=language, gender=gender, top_k=top_k)


# --------------- Public API ---------------

def create_task(*, user_id: int, object_key: str,
                language: str, gender: str) -> str:
    task_id = "vm_" + uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "object_key": object_key,
            "language": language,
            "gender": gender,
            "status": "pending",
            "progress": 0,
            "error": None,
            "result": None,
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


# --------------- 执行 ---------------

def _set(task_id: str, **updates) -> None:
    with _LOCK:
        if task_id in _TASKS:
            _TASKS[task_id].update(updates)


def _run_task_sync(task_id: str) -> None:
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return
        task = dict(t)

    work_dir = Path("uploads") / "voice_match" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    src_mp4 = work_dir / "src.mp4"
    clip_key = f"voice_match/{task['user_id']}/clips/{task_id}.wav"

    try:
        _set(task_id, status="sampling", progress=10)
        _download_tos_to_local(task["object_key"], str(src_mp4))
        clip_wav = _extract_sample_clip(str(src_mp4), out_dir=str(work_dir))

        _set(task_id, status="embedding", progress=40)
        vec = _embed_audio_file(clip_wav)

        _set(task_id, status="matching", progress=70)
        candidates = _match_candidates(
            vec, language=task["language"],
            gender=task["gender"], top_k=3,
        )
        if not candidates:
            raise RuntimeError("该语种声音库尚未同步，请联系管理员")

        signed_url = _upload_to_tos_signed(clip_wav, clip_key)
        _set(task_id, status="done", progress=100, result={
            "sample_audio_url": signed_url,
            "candidates": candidates,
        })
    except Exception as exc:
        log.exception("voice match task %s failed", task_id)
        _set(task_id, status="failed", progress=100, error=str(exc))
    finally:
        # 本地临时文件清理；TOS 清理由 TTL 清理器或 cleanup.py 统一兜底
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


# --------------- TTL 清理 ---------------

def _cleanup_expired() -> None:
    now = time.time()
    to_purge: list[str] = []
    with _LOCK:
        for tid, t in _TASKS.items():
            if t.get("_expires_at", 0) <= now:
                to_purge.append(tid)
        for tid in to_purge:
            _TASKS.pop(tid, None)


def _cleanup_loop() -> None:
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        try:
            _cleanup_expired()
        except Exception:
            log.warning("voice match TTL cleanup failed", exc_info=True)


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="vmt-cleanup")
_cleanup_thread.start()
