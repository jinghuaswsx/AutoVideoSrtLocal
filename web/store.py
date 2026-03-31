"""
任务状态内存存储

MVP 阶段用进程内字典；后续可替换为 Redis 而不影响其他层。
外部代码统一通过此模块的函数访问，不直接操作 _tasks 字典。
"""
from typing import Optional

_tasks: dict = {}


def create(task_id: str, video_path: str, task_dir: str, original_filename: str | None = None) -> dict:
    task = {
        "id": task_id,
        "status": "uploaded",
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": original_filename,
        "steps": {
            "extract": "pending",
            "asr": "pending",
            "alignment": "pending",
            "translate": "pending",
            "tts": "pending",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
        "utterances": [],
        "scene_cuts": [],
        "alignment": {},
        "script_segments": [],
        "segments": [],
        "voice_gender": "male",
        "voice_id": None,
        "recommended_voice_id": None,
        "subtitle_position": "bottom",
        "interactive_review": False,
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
    }
    _tasks[task_id] = task
    return task


def get(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


def update(task_id: str, **kwargs):
    """浅更新任务字段"""
    task = _tasks.get(task_id)
    if task:
        task.update(kwargs)


def set_step(task_id: str, step: str, status: str):
    task = _tasks.get(task_id)
    if task:
        task["steps"][step] = status


def set_artifact(task_id: str, step: str, payload: dict):
    task = _tasks.get(task_id)
    if task:
        task.setdefault("artifacts", {})[step] = payload


def set_preview_file(task_id: str, name: str, path: str):
    task = _tasks.get(task_id)
    if task:
        task.setdefault("preview_files", {})[name] = path


def confirm_segments(task_id: str, segments: list):
    task = _tasks.get(task_id)
    if task:
        task["segments"] = segments
        task["script_segments"] = segments
        task["_segments_confirmed"] = True


def confirm_alignment(task_id: str, break_after: list, script_segments: list):
    task = _tasks.get(task_id)
    if task:
        task["alignment"] = {
            "break_after": break_after,
            "script_segments": script_segments,
        }
        task["script_segments"] = script_segments
        task["segments"] = script_segments
        task["_alignment_confirmed"] = True
