"""Task upload initialization workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from appcore.db import execute as db_execute, query_one as db_query_one
from web import store
from web.services.task_av_inputs import av_step_maps
from web.services.task_names import default_display_name, resolve_task_display_name_conflict
from web.upload_util import build_source_object_info


@dataclass(frozen=True)
class UploadedTaskInitResult:
    task_id: str

    @property
    def payload(self) -> dict:
        return {
            "task_id": self.task_id,
            "redirect_url": f"/sentence_translate/{self.task_id}",
        }


def initialize_uploaded_av_task(
    task_id: str,
    *,
    video_path: str,
    task_dir: str,
    original_filename: str,
    form_payload: Mapping[str, object],
    av_inputs: Mapping[str, object],
    source_updates: Mapping[str, object],
    file_size: int,
    content_type: str,
    user_id: int | None,
    clock: Callable[[], datetime] = datetime.now,
    create_task: Callable[..., object] = store.create,
    update_task: Callable[..., object] = store.update,
    execute: Callable[..., object] = db_execute,
    query_one: Callable[..., dict | None] = db_query_one,
    resolve_name_conflict: Callable[..., str] = resolve_task_display_name_conflict,
) -> UploadedTaskInitResult:
    create_task(
        task_id,
        video_path,
        task_dir,
        original_filename=original_filename,
        user_id=user_id,
    )

    desired_name = str(form_payload.get("display_name") or "").strip()[:200]
    display_name = desired_name or default_display_name(original_filename)
    if user_id is not None:
        display_name = resolve_name_conflict(
            user_id,
            display_name,
            query_one=query_one,
        )
        execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))

    steps, step_messages = av_step_maps()
    update_task(
        task_id,
        display_name=display_name,
        type="translation",
        source_language=source_updates["source_language"],
        user_specified_source_language=source_updates["user_specified_source_language"],
        pipeline_version="av",
        target_lang=av_inputs["target_language"],
        av_translate_inputs=dict(av_inputs),
        steps=steps,
        step_messages=step_messages,
        source_tos_key="",
        source_object_info=build_source_object_info(
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
            storage_backend="local",
            uploaded_at=clock().isoformat(timespec="seconds"),
        ),
        delivery_mode="local_primary",
    )
    return UploadedTaskInitResult(task_id=task_id)
