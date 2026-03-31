"""
视频合成模块：
1. 使用 timeline_manifest 按段消费原视频画面
2. 保留完整英文 TTS 音轨，不拉伸不压缩
3. 输出软字幕版（mp4 + srt）和硬字幕版（烧录）
"""
import os
import subprocess


def compose_video(
    video_path: str,
    tts_audio_path: str,
    srt_path: str,
    output_dir: str,
    subtitle_position: str = "bottom",
    timeline_manifest: dict | None = None,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]

    soft_output = os.path.join(output_dir, f"{base_name}_soft.mp4")
    hard_output = os.path.join(output_dir, f"{base_name}_hard.mp4")

    if timeline_manifest:
        _compose_soft_from_manifest(video_path, tts_audio_path, timeline_manifest, soft_output)
    else:
        tts_duration = _get_duration(tts_audio_path)
        video_duration = _get_duration(video_path)
        _compose_soft_legacy(video_path, tts_audio_path, min(tts_duration, video_duration), soft_output)

    _compose_hard(soft_output, srt_path, subtitle_position, hard_output)

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


def _compose_hard(video_path: str, srt_path: str, position: str, output_path: str):
    position_map = {
        "bottom": "Alignment=2,MarginV=50",
        "middle": "Alignment=5",
        "top": "Alignment=8,MarginV=50",
    }
    style_override = position_map.get(position, position_map["bottom"])
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    vf = (
        "subtitles="
        f"{srt_escaped}:force_style='FontName=Arial,FontSize=18,PrimaryColour=&HFFFFFF,"
        f"OutlineColour=&H000000,Outline=2,Bold=1,{style_override}'"
    )

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
