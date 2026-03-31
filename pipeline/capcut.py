import importlib
import json
import shutil
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from config import CAPCUT_TEMPLATE_DIR


def export_capcut_project(
    video_path: str,
    tts_audio_path: str,
    srt_path: str,
    timeline_manifest: dict,
    output_dir: str,
    subtitle_position: str = "bottom",
) -> dict:
    project_dir = Path(output_dir) / "capcut_project"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    export_backend = "template_scaffold"
    backend_error = None

    try:
        _export_with_pyjianyingdraft(
            project_dir=project_dir,
            video_path=Path(video_path),
            tts_audio_path=Path(tts_audio_path),
            srt_path=Path(srt_path),
            timeline_manifest=timeline_manifest,
            subtitle_position=subtitle_position,
        )
        export_backend = "pyJianYingDraft"
    except Exception as exc:
        backend_error = str(exc)
        if project_dir.exists():
            shutil.rmtree(project_dir)
        _export_with_template_scaffold(
            project_dir=project_dir,
            video_path=Path(video_path),
            tts_audio_path=Path(tts_audio_path),
            srt_path=Path(srt_path),
            timeline_manifest=timeline_manifest,
            subtitle_position=subtitle_position,
        )

    manifest_path = project_dir / "codex_export_manifest.json"
    export_manifest = {
        "backend": export_backend,
        "video": "Resources/auto_generated/" + Path(video_path).name,
        "audio": "Resources/auto_generated/" + Path(tts_audio_path).name,
        "subtitle": "Resources/auto_generated/" + Path(srt_path).name,
        "subtitle_position": subtitle_position,
        "timeline_manifest": timeline_manifest,
    }
    if backend_error:
        export_manifest["fallback_reason"] = backend_error
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(export_manifest, fh, ensure_ascii=False, indent=2)

    archive_path = Path(output_dir) / "capcut_project.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in project_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(project_dir.parent))

    return {
        "project_dir": str(project_dir),
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
    }


def _export_with_pyjianyingdraft(
    project_dir: Path,
    video_path: Path,
    tts_audio_path: Path,
    srt_path: Path,
    timeline_manifest: dict,
    subtitle_position: str,
):
    draft = importlib.import_module("pyJianYingDraft")
    output_dir = project_dir.parent
    draft_name = project_dir.name

    draft_folder = draft.DraftFolder(str(output_dir))
    script = draft_folder.create_draft(draft_name, 1080, 1920, allow_replace=True)

    resources_dir, copied_video, copied_audio, copied_srt = _copy_resources(
        project_dir, video_path, tts_audio_path, srt_path
    )
    if not resources_dir.exists():
        raise RuntimeError("pyJianYingDraft export failed to create resource directory")

    script.add_track(draft.TrackType.video, track_name="video")
    script.add_track(draft.TrackType.audio, track_name="audio")

    total_duration = float(timeline_manifest.get("total_tts_duration", 0.0) or 0.0)
    if total_duration > 0:
        script.add_segment(
            draft.AudioSegment(
                str(copied_audio),
                draft.trange("0s", f"{round(total_duration, 3)}s"),
            ),
            track_name="audio",
        )

    for segment in timeline_manifest.get("segments", []):
        target_cursor = float(segment.get("timeline_start", 0.0) or 0.0)
        for clip in segment.get("video_ranges", []):
            clip_start = float(clip["start"])
            clip_duration = max(float(clip["end"]) - clip_start, 0.0)
            if clip_duration <= 0:
                continue
            script.add_segment(
                draft.VideoSegment(
                    str(copied_video),
                    draft.trange(f"{round(target_cursor, 3)}s", f"{round(clip_duration, 3)}s"),
                    source_timerange=draft.trange(f"{round(clip_start, 3)}s", f"{round(clip_duration, 3)}s"),
                ),
                track_name="video",
            )
            target_cursor += clip_duration

    script.import_srt(
        str(copied_srt),
        track_name="subtitle",
        text_style=draft.TextStyle(size=5.6, color=(1.0, 1.0, 1.0), align=1, auto_wrapping=True, max_line_width=0.76),
        clip_settings=draft.ClipSettings(transform_y=_subtitle_transform_y(subtitle_position)),
    )
    script.save()


def _export_with_template_scaffold(
    project_dir: Path,
    video_path: Path,
    tts_audio_path: Path,
    srt_path: Path,
    timeline_manifest: dict,
    subtitle_position: str,
):
    template_dir = Path(CAPCUT_TEMPLATE_DIR)
    if template_dir.exists():
        shutil.copytree(template_dir, project_dir)
    else:
        project_dir.mkdir(parents=True, exist_ok=True)

    resources_dir, copied_video, copied_audio, copied_srt = _copy_resources(
        project_dir, video_path, tts_audio_path, srt_path
    )

    timeline_id = str(uuid.uuid4()).upper()
    now = int(time.time() * 1_000_000)
    timelines_dir = project_dir / "Timelines"
    timelines_dir.mkdir(parents=True, exist_ok=True)
    project_json = {
        "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": now,
        "id": str(uuid.uuid4()).upper(),
        "main_timeline_id": timeline_id,
        "timelines": [
            {
                "create_time": now,
                "id": timeline_id,
                "is_marked_delete": False,
                "name": "AutoVideoSrt Timeline",
                "update_time": now,
            }
        ],
        "update_time": now,
        "version": 0,
    }
    with open(timelines_dir / "project.json", "w", encoding="utf-8") as fh:
        json.dump(project_json, fh, ensure_ascii=False, indent=2)


def _copy_resources(project_dir: Path, video_path: Path, tts_audio_path: Path, srt_path: Path):
    resources_dir = project_dir / "Resources" / "auto_generated"
    resources_dir.mkdir(parents=True, exist_ok=True)

    copied_video = resources_dir / video_path.name
    copied_audio = resources_dir / tts_audio_path.name
    copied_srt = resources_dir / srt_path.name
    shutil.copy2(video_path, copied_video)
    shutil.copy2(tts_audio_path, copied_audio)
    shutil.copy2(srt_path, copied_srt)
    return resources_dir, copied_video, copied_audio, copied_srt


def _subtitle_transform_y(position: str) -> float:
    mapping = {
        "top": 0.78,
        "middle": 0.0,
        "bottom": -0.78,
    }
    return mapping.get(position, -0.78)
