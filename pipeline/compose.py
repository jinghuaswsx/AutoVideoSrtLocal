"""
视频合成模块：
1. 使用 timeline_manifest 按段消费原视频画面
2. 保留完整英文 TTS 音轨，不拉伸不压缩
3. 输出软字幕版（mp4 + srt）和硬字幕版（烧录）
"""
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_FONT_SIZE_BASE: dict[str, int] = {"small": 11, "medium": 14, "large": 18}
_VALID_FONT_NAME = re.compile(r'^[A-Za-z0-9 \-_]+$')


def _fonts_dir() -> str:
    """返回项目 fonts/ 目录的绝对路径。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _compute_font_size(video_height: int, preset) -> int:
    """根据视频高度和预设档位计算自适应字号（ASS pt）。

    preset 可以是数字（直接作为 1080p 基准字号）或旧版字符串（small/medium/large）。
    """
    if isinstance(preset, (int, float)):
        base = int(preset)
    else:
        base = _FONT_SIZE_BASE.get(preset, _FONT_SIZE_BASE["medium"])
    return round(video_height / 1080 * base)


def _compute_margin_v(video_height: int, position_y: float) -> int:
    """将「距顶百分比」转换为 ffmpeg ASS MarginV（距底像素）。"""
    return round(video_height * (1.0 - position_y))


def compose_video(
    video_path: str,
    tts_audio_path: str,
    srt_path: str,
    output_dir: str,
    subtitle_position: str = "bottom",   # 保留供 CapCut 模块使用
    timeline_manifest: dict | None = None,
    variant: str | None = None,
    font_name: str = "Impact",
    font_size_preset: str = "medium",
    subtitle_position_y: float = 0.68,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    suffix = f".{variant}" if variant else ""

    soft_output = os.path.join(output_dir, f"{base_name}_soft{suffix}.mp4")
    hard_output = os.path.join(output_dir, f"{base_name}_hard{suffix}.mp4")

    if timeline_manifest:
        _compose_soft_from_manifest(video_path, tts_audio_path, timeline_manifest, soft_output)
    else:
        tts_duration = _get_duration(tts_audio_path)
        video_duration = _get_duration(video_path)
        _compose_soft_legacy(video_path, tts_audio_path, min(tts_duration, video_duration), soft_output)

    _compose_hard(
        soft_output, srt_path, hard_output,
        font_name=font_name,
        font_size_preset=font_size_preset,
        subtitle_position_y=subtitle_position_y,
    )

    return {
        "soft_video": soft_output,
        "hard_video": hard_output,
        "srt": srt_path,
    }


def _compose_soft_from_manifest(video_path: str, audio_path: str, manifest: dict, output_path: str):
    trim_labels = []
    filter_parts = []
    segment_idx = 0

    for segment in manifest.get("segments", []):
        for clip in segment.get("video_ranges", []):
            label = f"v{segment_idx}"
            trim_labels.append(f"[{label}]")
            filter_parts.append(
                f"[0:v]trim=start={clip['start']}:end={clip['end']},setpts=PTS-STARTPTS[{label}]"
            )
            segment_idx += 1

    if not trim_labels:
        raise RuntimeError("timeline_manifest does not contain any video ranges")

    total_duration = float(manifest.get("total_tts_duration", 0.0) or 0.0)
    video_consumed = float(manifest.get("video_consumed_duration", 0.0) or 0.0)
    concat_label = "vcat"
    if len(trim_labels) == 1:
        filter_parts.append(f"{trim_labels[0]}null[{concat_label}]")
    else:
        filter_parts.append("".join(trim_labels) + f"concat=n={len(trim_labels)}:v=1:a=0[{concat_label}]")

    final_label = "vout"
    pad_duration = max(total_duration - video_consumed, 0.0)
    if pad_duration > 0.001:
        filter_parts.append(
            f"[{concat_label}]tpad=stop_mode=clone:stop_duration={round(pad_duration, 3)}[{final_label}]"
        )
    else:
        filter_parts.append(f"[{concat_label}]null[{final_label}]")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{final_label}]",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"软字幕版合成失败: {result.stderr}")


def _compose_soft_legacy(video_path: str, audio_path: str, duration: float, output_path: str):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"软字幕版合成失败: {result.stderr}")


def _compose_hard(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_name: str = "Impact",
    font_size_preset: str = "medium",
    subtitle_position_y: float = 0.68,
) -> None:
    video_height = _get_video_height(video_path)
    font_size_pt = _compute_font_size(video_height, font_size_preset)
    margin_v = _compute_margin_v(video_height, subtitle_position_y)
    vf = _build_subtitle_filter(srt_path, font_name, font_size_pt, margin_v)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"硬字幕版合成失败: {result.stderr}")


def _build_subtitle_filter(srt_path: str, font_name: str, font_size_pt: int, margin_v: int) -> str:
    if not _VALID_FONT_NAME.match(font_name):
        font_name = "Impact"
    fonts_dir = _escape_subtitle_filter_path(_fonts_dir())
    escaped_path = _escape_subtitle_filter_path(srt_path)
    return (
        f"subtitles=filename='{escaped_path}'"
        f":fontsdir='{fonts_dir}'"
        f":force_style='FontName={font_name},FontSize={font_size_pt},"
        f"PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,"
        f"Alignment=2,MarginV={margin_v}'"
    )


def _escape_subtitle_filter_path(srt_path: str) -> str:
    normalized = srt_path.replace("\\", "/")
    return normalized.replace(":", "\\:")


def _get_video_height(video_path: str) -> int:
    """读取视频流高度；读取失败时返回 1080 作为安全默认值。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(
            "ffprobe 获取视频高度失败（returncode=%d），使用默认 1080p: %s",
            result.returncode,
            result.stderr.strip(),
        )
        return 1080
    try:
        return int(result.stdout.strip())
    except (ValueError, TypeError):
        logger.warning("ffprobe 返回了无法解析的高度值 %r，使用默认 1080p", result.stdout)
        return 1080


def _get_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
