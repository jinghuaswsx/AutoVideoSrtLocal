from copy import deepcopy
import importlib
import json
import shutil
import time
import uuid
from os import name as os_name
from pathlib import Path, PureWindowsPath
from zipfile import ZIP_DEFLATED, ZipFile

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT
from appcore.safe_paths import remove_tree_under_roots, resolve_under_allowed_roots
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
    jianying_project_root: str | None = None,
    subtitle_font: str = "Impact",
    subtitle_size=None,
    subtitle_position_y: float | None = None,
) -> dict:
    source_name = draft_title or Path(video_path).name
    draft_name = build_capcut_draft_name(source_name, variant=variant)
    output_root = Path(output_dir)
    project_dir = resolve_under_allowed_roots(output_root / draft_name, [output_root])
    if project_dir.exists():
        remove_tree_under_roots(project_dir, [output_root], ignore_errors=True)

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
            subtitle_font=subtitle_font,
            subtitle_size=subtitle_size,
            subtitle_position_y=subtitle_position_y,
        )
        export_backend = "pyJianYingDraft"
    except Exception as exc:
        backend_error = str(exc)
        if project_dir.exists():
            remove_tree_under_roots(project_dir, [output_root], ignore_errors=True)
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

    archive_path = resolve_under_allowed_roots(
        output_root / build_capcut_archive_name(source_name, variant=variant),
        [output_root],
    )
    jianying_project_dir = rewrite_capcut_project_paths(
        project_dir=str(project_dir),
        manifest_path=str(manifest_path),
        archive_path=str(archive_path),
        jianying_project_root=jianying_project_root,
    )

    return {
        "project_dir": str(project_dir),
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "jianying_project_dir": jianying_project_dir,
    }


def rewrite_capcut_project_paths(
    project_dir: str,
    manifest_path: str | None = None,
    archive_path: str | None = None,
    jianying_project_root: str | None = None,
) -> str:
    source_dir = Path(project_dir)
    jianying_project_dir = _build_export_jianying_project_dir(source_dir.name, jianying_project_root)

    _rewrite_draft_content_paths(source_dir, jianying_project_dir)
    _rewrite_draft_meta_info(source_dir, jianying_project_dir)
    if manifest_path:
        _rewrite_export_manifest(Path(manifest_path), jianying_project_dir)
    if archive_path:
        _write_archive(source_dir, Path(archive_path))

    return str(jianying_project_dir)


def deploy_capcut_project(project_dir: str, target_root: str | None = None) -> str:
    source_dir = Path(project_dir)
    if not source_dir.exists():
        raise FileNotFoundError(f"CapCut project directory not found: {project_dir}")

    deployed_project_dir = _build_jianying_deploy_path(source_dir.name, target_root=target_root)
    if not deployed_project_dir:
        raise RuntimeError("Jianying project directory is not configured and no default path is available")

    if deployed_project_dir.exists():
        remove_tree_under_roots(deployed_project_dir, [deployed_project_dir.parent], ignore_errors=True)
    shutil.copytree(source_dir, deployed_project_dir)
    return str(deployed_project_dir)


def _rewrite_draft_content_paths(project_dir: Path, jianying_project_dir: PureWindowsPath) -> None:
    draft_content_path = project_dir / "draft_content.json"
    if not draft_content_path.exists():
        return

    try:
        data = json.loads(draft_content_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    resource_path_map = _build_resource_path_map(project_dir, jianying_project_dir)
    _rewrite_path_fields(data, resource_path_map)
    draft_content_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_draft_meta_info(project_dir: Path, jianying_project_dir: PureWindowsPath) -> None:
    draft_meta_path = project_dir / "draft_meta_info.json"
    if not draft_meta_path.exists():
        return

    try:
        data = json.loads(draft_meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["draft_fold_path"] = str(jianying_project_dir)
    data["draft_name"] = project_dir.name
    draft_meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_export_manifest(manifest_path: Path, jianying_project_dir: PureWindowsPath) -> None:
    if not manifest_path.exists():
        return

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["jianying_project_dir"] = str(jianying_project_dir)
    data["timeline_manifest"] = _sanitize_timeline_manifest(data.get("timeline_manifest") or {})
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_resource_path_map(project_dir: Path, jianying_project_dir: PureWindowsPath) -> dict[str, str]:
    resources_dir = project_dir / "Resources" / "auto_generated"
    if not resources_dir.exists():
        return {}

    target_resources_dir = jianying_project_dir / "Resources" / "auto_generated"
    path_map = {}
    for file_path in resources_dir.iterdir():
        if file_path.is_file():
            path_map[file_path.name] = str(target_resources_dir / file_path.name)
    return path_map


def _rewrite_path_fields(node, resource_path_map: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "path" and isinstance(value, str):
                replacement = resource_path_map.get(_basename_from_any_path(value))
                if replacement:
                    node[key] = replacement
            else:
                _rewrite_path_fields(value, resource_path_map)
        return

    if isinstance(node, list):
        for item in node:
            _rewrite_path_fields(item, resource_path_map)


def _sanitize_timeline_manifest(node):
    if isinstance(node, dict):
        cleaned = {}
        for key, value in node.items():
            if key == "tts_path" and isinstance(value, str):
                cleaned[key] = ""
            else:
                cleaned[key] = _sanitize_timeline_manifest(value)
        return cleaned
    if isinstance(node, list):
        return [_sanitize_timeline_manifest(item) for item in node]
    return deepcopy(node)


def _basename_from_any_path(path_value: str) -> str:
    return path_value.replace("\\", "/").rstrip("/").split("/")[-1]


def _write_archive(project_dir: Path, archive_path: Path) -> None:
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in project_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(project_dir.parent))


def _export_with_pyjianyingdraft(
    project_dir: Path,
    video_path: Path,
    tts_audio_path: Path,
    srt_path: Path,
    timeline_manifest: dict,
    subtitle_position: str,
    subtitle_font: str = "Impact",
    subtitle_size=None,
    subtitle_position_y: float | None = None,
):
    draft = importlib.import_module("pyJianYingDraft")
    output_dir = project_dir.parent
    draft_name = project_dir.name
    media_duration = _probe_media_duration(video_path) or float(timeline_manifest.get("video_duration", 0.0) or 0.0)
    audio_duration = _probe_media_duration(tts_audio_path)

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
    manifest_video_duration = float(timeline_manifest.get("video_duration", 0.0) or 0.0)
    output_duration = manifest_video_duration or media_duration or total_duration
    if output_duration > 0:
        audio_segment_duration = min(total_duration or output_duration, output_duration)
        if audio_duration > 0:
            audio_segment_duration = min(audio_segment_duration, _truncate_milliseconds(audio_duration))
        script.add_segment(
            draft.AudioSegment(
                str(copied_audio),
                draft.trange("0s", f"{round(audio_segment_duration, 3)}s"),
            ),
            track_name="audio",
        )

    prev_end_us = -1
    video_consumed_end = 0.0
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
            target_start = round(target_cursor, 3)
            # Prevent overlap: nudge start forward by 1ms if it would overlap
            start_us = int(target_start * 1_000_000)
            if prev_end_us >= 0 and start_us < prev_end_us:
                target_start = (prev_end_us + 1000) / 1_000_000.0
                clip_duration = max(clip_duration - (target_start - round(target_cursor, 3)), 0.001)
            script.add_segment(
                draft.VideoSegment(
                    str(copied_video),
                    draft.trange(f"{target_start}s", f"{clip_duration}s"),
                    source_timerange=draft.trange(f"{clip_start}s", f"{clip_duration}s"),
                ),
                track_name="video",
            )
            prev_end_us = int((target_start + clip_duration) * 1_000_000)
            target_cursor += clip_duration
            video_consumed_end = max(video_consumed_end, target_start + clip_duration)

    manifest_consumed = float(timeline_manifest.get("video_consumed_duration", 0.0) or 0.0)
    if manifest_consumed > 0:
        video_consumed_end = max(video_consumed_end, min(manifest_consumed, media_duration or manifest_consumed))
    if media_duration > 0 and output_duration > video_consumed_end + 0.001:
        tail_start = round(video_consumed_end, 3)
        tail_end = min(output_duration, media_duration)
        tail_duration = round(max(tail_end - tail_start, 0.0), 3)
        if tail_duration > 0:
            script.add_segment(
                draft.VideoSegment(
                    str(copied_video),
                    draft.trange(f"{tail_start}s", f"{tail_duration}s"),
                    source_timerange=draft.trange(f"{tail_start}s", f"{tail_duration}s"),
                ),
                track_name="video",
            )

    _import_srt_safe(
        draft, script, str(copied_srt), subtitle_position,
        subtitle_font=subtitle_font,
        subtitle_size=subtitle_size,
        subtitle_position_y=subtitle_position_y,
    )
    script.save()


def _import_srt_safe(
    draft, script, srt_path: str, subtitle_position: str,
    subtitle_font: str = "Impact",
    subtitle_size=None,
    subtitle_position_y: float | None = None,
) -> None:
    """Import SRT with overlap fix: sort entries and remove overlapping ones."""
    _fix_srt_overlaps(srt_path)
    size = _resolve_capcut_font_size(subtitle_size) if subtitle_size is not None else 5.6
    transform_y = _resolve_capcut_transform_y(subtitle_position_y, subtitle_position)
    text_style = draft.TextStyle(
        size=size, color=(1.0, 1.0, 1.0), align=1,
        auto_wrapping=True, max_line_width=0.76,
    )
    clip_settings = draft.ClipSettings(transform_y=transform_y)

    # 字体：UI 字体在 CapCut 内置枚举里有对应才注入；Oswald/Bebas 等没有时
    # 不传 font，剪映会使用默认字体（Concert One-Regular）。
    # 整块包 try/except：老版 pyJianYingDraft 或测试替身可能没有 FontType/TextSegment，
    # 这种情况下回退到无 style_reference 路径，保持字幕导入行为不倒退。
    style_reference = None
    try:
        font_enum_name = _resolve_capcut_font_enum_name(subtitle_font)
        font_type_enum = getattr(draft, "FontType", None)
        text_segment_cls = getattr(draft, "TextSegment", None)
        if font_enum_name and font_type_enum is not None and text_segment_cls is not None:
            font_value = getattr(font_type_enum, font_enum_name, None)
            if font_value is not None:
                style_reference = text_segment_cls(
                    "placeholder",
                    draft.trange("0s", "1s"),
                    font=font_value,
                    style=text_style,
                    clip_settings=clip_settings,
                )
    except Exception:
        style_reference = None

    kwargs = dict(
        track_name="subtitle",
        text_style=text_style,
        clip_settings=clip_settings,
    )
    if style_reference is not None:
        kwargs["style_reference"] = style_reference

    script.import_srt(srt_path, **kwargs)


def _fix_srt_overlaps(srt_path: str) -> None:
    """Parse SRT, sort by start time, remove overlapping entries, rewrite file."""
    import re

    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return

    pattern = re.compile(
        r"(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n((?:(?!\n\d+\s*\n\d{2}:\d{2}).)*)",
        re.DOTALL,
    )

    def ts_to_ms(ts: str) -> int:
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

    def ms_to_ts(ms: int) -> str:
        h = ms // 3600000
        ms %= 3600000
        m = ms // 60000
        ms %= 60000
        s = ms // 1000
        ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    entries = []
    for match in pattern.finditer(content):
        start_ms = ts_to_ms(match.group(2))
        end_ms = ts_to_ms(match.group(3))
        text = match.group(4).strip()
        entries.append((start_ms, end_ms, text))

    entries.sort(key=lambda e: e[0])

    cleaned = []
    for start, end, text in entries:
        if cleaned and start < cleaned[-1][1]:
            continue
        cleaned.append((start, end, text))

    if len(cleaned) == len(entries):
        return

    lines = []
    for i, (start, end, text) in enumerate(cleaned, 1):
        lines.append(f"{i}\n{ms_to_ts(start)} --> {ms_to_ts(end)}\n{text}\n")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


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


# 硬字幕 UI 字体 → CapCut FontType 枚举名。没列进来的字体（Oswald、Bebas 等）
# 剪映内置字体库里没有对应款，走剪映默认字体（Concert One-Regular）。
_CAPCUT_FONT_ALIAS: dict[str, str] = {
    "Impact": "Anton",              # 与硬字幕 Impact→Anton 保持一致
    "Anton": "Anton",
    "Poppins Bold": "Poppins_Bold",
    "Poppins": "Poppins_Bold",
    "Montserrat ExtraBold": "Montserrat_Black",
    "Montserrat": "Montserrat_Black",
}

# 硬字幕 FontSize 14 视觉上对应 CapCut TextStyle.size 5.6（剪映里中等字号）。
# 其他字号按线性缩放，保留 2 位小数。
_CAPCUT_FONT_SIZE_PRESET: dict[str, float] = {
    "small": 4.4,
    "medium": 5.6,
    "large": 7.2,
}
_CAPCUT_FONT_SIZE_BASELINE_PT = 14.0
_CAPCUT_FONT_SIZE_BASELINE_UI = 5.6


def _resolve_capcut_font_enum_name(font_name: str) -> str | None:
    """把 UI 侧字体名映射到 CapCut FontType 枚举成员名；无匹配返回 None。"""
    return _CAPCUT_FONT_ALIAS.get((font_name or "").strip())


def _resolve_capcut_font_size(preset) -> float:
    """把硬字幕字号预设（'small'/'medium'/'large' 或整数 pt）换算成剪映 UI 字号。"""
    if isinstance(preset, (int, float)) and not isinstance(preset, bool):
        return round(float(preset) / _CAPCUT_FONT_SIZE_BASELINE_PT * _CAPCUT_FONT_SIZE_BASELINE_UI, 2)
    return _CAPCUT_FONT_SIZE_PRESET.get(preset, _CAPCUT_FONT_SIZE_BASELINE_UI)


def _resolve_capcut_transform_y(position_y, legacy_position: str) -> float:
    """把硬字幕「距顶百分比」换算成剪映 transform_y。

    硬字幕坐标系：0 顶，1 底；剪映 transform_y：+1 顶，-1 底。
    老任务没有 position_y 时回退到 top/middle/bottom 三档映射。
    """
    if position_y is None:
        return _subtitle_transform_y(legacy_position)
    return round(1.0 - 2.0 * float(position_y), 3)


_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}


def build_capcut_draft_name(source_name: str, variant: str | None = None) -> str:
    p = Path(source_name)
    stem = p.stem if p.suffix.lower() in _VIDEO_SUFFIXES else source_name
    stem = _sanitize_draft_name(stem)[:50]
    if variant:
        safe_variant = _sanitize_draft_name(str(variant))[:50]
        return f"{stem}_capcut_{safe_variant}"
    return f"{stem}_capcut"


def build_capcut_archive_name(source_name: str, variant: str | None = None) -> str:
    return f"{build_capcut_draft_name(source_name, variant=variant)}.zip"


def _sanitize_draft_name(name: str) -> str:
    sanitized = "".join("_" if char in '<>:"/\\|?*' else char for char in name).strip()
    return sanitized or "capcut_project"


def _build_export_jianying_project_dir(draft_name: str, jianying_project_root: str | None = None) -> PureWindowsPath:
    root = _resolve_export_jianying_project_root(jianying_project_root)
    return PureWindowsPath(root) / draft_name


def _resolve_export_jianying_project_root(jianying_project_root: str | None = None) -> str:
    root = (jianying_project_root or "").strip().strip('"').strip("'")
    return root or DEFAULT_JIANYING_PROJECT_ROOT


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


def _truncate_milliseconds(value: float) -> float:
    return max(int(value * 1000), 0) / 1000.0
