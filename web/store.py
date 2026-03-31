"""
任务状态内存存储

MVP 阶段用进程内字典；后续可替换为 Redis 而不影响其他层。
外部代码统一通过此模块的函数访问，不直接操作 _tasks 字典。
"""
from typing import Optional

from pipeline.localization import VARIANT_LABELS

_tasks: dict = {}


def _empty_variant_state(label: str) -> dict:
    return {
        "label": label,
        "localized_translation": {},
        "tts_script": {},
        "tts_result": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "timeline_manifest": {},
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
    }


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
        "source_full_text_zh": "",
        "localized_translation": {},
        "tts_script": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "voice_gender": "male",
        "voice_id": None,
        "recommended_voice_id": None,
        "subtitle_position": "bottom",
        "interactive_review": False,
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
        "variants": {
            key: _empty_variant_state(label)
            for key, label in VARIANT_LABELS.items()
        },
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


def update_variant(task_id: str, variant: str, **kwargs):
    task = _tasks.get(task_id)
    if task:
        variants = task.setdefault("variants", {})
        variant_state = dict(variants.get(variant, _empty_variant_state(variant)))
        variant_state.update(kwargs)
        variants[variant] = variant_state


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


def set_variant_artifact(task_id: str, variant: str, step: str, payload: dict):
    task = _tasks.get(task_id)
    if task:
        variants = task.setdefault("variants", {})
        variant_state = variants.setdefault(variant, _empty_variant_state(variant))
        variant_state.setdefault("artifacts", {})[step] = payload


def set_variant_preview_file(task_id: str, variant: str, name: str, path: str):
    task = _tasks.get(task_id)
    if task:
        variants = task.setdefault("variants", {})
        variant_state = variants.setdefault(variant, _empty_variant_state(variant))
        variant_state.setdefault("preview_files", {})[name] = path


def _localized_translation_from_segments(task: dict, segments: list) -> dict:
    sentences = []
    full_text_parts = []
    source_segments = task.get("script_segments") or []

    for fallback_index, segment in enumerate(segments):
        translated = (segment.get("translated") or "").strip()
        if not translated:
            continue

        indices = segment.get("source_segment_indices") or []
        if not indices:
            segment_index = segment.get("index")
            if segment_index is not None:
                indices = [segment_index]
        if not indices and fallback_index < len(source_segments):
            source_index = source_segments[fallback_index].get("index")
            if source_index is not None:
                indices = [source_index]
        if not indices:
            indices = [fallback_index]

        sentences.append(
            {
                "index": len(sentences),
                "text": translated,
                "source_segment_indices": indices,
            }
        )
        full_text_parts.append(translated)

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "sentences": sentences,
    }


def confirm_segments(task_id: str, segments: list):
    task = _tasks.get(task_id)
    if task:
        task["segments"] = segments
        if not task.get("script_segments"):
            task["script_segments"] = segments
        task["localized_translation"] = _localized_translation_from_segments(task, segments)
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
