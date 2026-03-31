import importlib
import json
import shutil
import time
import uuid
from os import name as os_name
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from config import CAPCUT_TEMPLATE_DIR, JIANYING_PROJECT_DIR


def export_capcut_project(
    video_path: str,
    tts_audio_path: str,
    srt_path: str,
    timeline_manifest: dict,
    output_dir: str,
    subtitle_position: str = "bottom",
    draft_title: str | None = None,
    variant: str | None = None,
) -> dict:
    source_name = draft_title or Path(video_path).name
    draft_name = _build_draft_name(source_name, variant=variant)
    project_dir = Path(output_dir) / draft_name
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
        "draft_name": draft_name,
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

    archive_path = Path(output_dir) / f"{draft_name}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in project_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(project_dir.parent))

    return {
        "project_dir": str(project_dir),
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "jianying_project_dir": "",
    }


def deploy_capcut_project(project_dir: str, target_root: str | None = None) -> str:
    source_dir = Path(project_dir)
    if not source_dir.exists():
        raise FileNotFoundError(f"CapCut project directory not found: {project_dir}")

    deployed_project_dir = _build_jianying_deploy_path(source_dir.name, target_root=target_root)
    if not deployed_project_dir:
        raise RuntimeError("Jianying project directory is not configured and no default path is available")

    if deployed_project_dir.exists():
        shutil.rmtree(deployed_project_dir)
    shutil.copytree(source_dir, deployed_project_dir)
    return str(deployed_project_dir)


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
    media_duration = _probe_media_duration(video_path) or float(timeline_manifest.get("video_duration", 0.0) or 0.0)

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
            clip_start = max(float(clip["start"]), 0.0)
            clip_end = float(clip["end"])
            if media_duration > 0:
                clip_end = min(clip_end, media_duration)
            clip_duration = max(clip_end - clip_start, 0.0)
            if clip_duration <= 0:
                continue
            clip_start = round(clip_start, 3)
            clip_duration = round(clip_duration, 3)
            script.add_segment(
                draft.VideoSegment(
                    str(copied_video),
                    draft.trange(f"{round(target_cursor, 3)}s", f"{clip_duration}s"),
                    source_timerange=draft.trange(f"{clip_start}s", f"{clip_duration}s"),
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


def _build_draft_name(source_name: str, variant: str | None = None) -> str:
    timestamp = time.strftime("%y-%m-%d-%H-%M-%S")
    stem = _sanitize_draft_name(Path(source_name).stem)
    if variant:
        return f"{stem}_{variant}_{timestamp}"
    return f"{stem}_{timestamp}"


def _sanitize_draft_name(name: str) -> str:
    sanitized = "".join("_" if char in '<>:"/\\|?*' else char for char in name).strip()
    return sanitized or "capcut_project"


def _build_jianying_deploy_path(draft_name: str, target_root: str | None = None) -> Path | None:
    root = _resolve_jianying_project_root(target_root=target_root)
    if not root:
        return None
    return root / draft_name


def _resolve_jianying_project_root(target_root: str | None = None) -> Path | None:
    if target_root:
        root = Path(target_root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    if JIANYING_PROJECT_DIR:
        root = Path(JIANYING_PROJECT_DIR)
        root.mkdir(parents=True, exist_ok=True)
        return root

    if os_name != "nt":
        return None

    default_root = Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
    if default_root.exists() or default_root.parent.exists():
        default_root.mkdir(parents=True, exist_ok=True)
        return default_root
    return None


def _probe_media_duration(video_path: Path) -> float:
    try:
        media_info = importlib.import_module("pymediainfo").MediaInfo
    except ModuleNotFoundError:
        return 0.0

    try:
        info = media_info.parse(str(video_path))
    except Exception:
        return 0.0

    for track in getattr(info, "video_tracks", []) or []:
        duration_ms = getattr(track, "duration", None)
        try:
            if duration_ms is not None:
                return float(duration_ms) / 1000.0
        except (TypeError, ValueError):
            continue

    for track in getattr(info, "audio_tracks", []) or []:
        duration_ms = getattr(track, "duration", None)
        try:
            if duration_ms is not None:
                return float(duration_ms) / 1000.0
        except (TypeError, ValueError):
            continue

    return 0.0
