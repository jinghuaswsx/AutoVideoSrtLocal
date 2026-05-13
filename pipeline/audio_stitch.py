"""
按分镜时间轴将多段 TTS 音频拼接为一整段 MP3（中间用静音填充）。

V2 流水线给每个分镜生成一段独立 MP3，而 ``pipeline.compose.compose_video``
只接受"单条合并音轨 + timeline_manifest"。本模块负责两个适配：

- :func:`build_stitched_audio` 用 ffmpeg 单命令把所有分镜 MP3 按 ``shot_start``
  delay 后混合（不足处自然是静音）为单条 MP3。
- :func:`build_timeline_manifest` 构造简单的 timeline_manifest，保持画面区间
  等于分镜区间，让 compose_video 知道如何贴原视频片段。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List


def build_stitched_audio(
    segments: List[Dict[str, Any]],
    *,
    total_duration: float,
    output_path: str,
    run_command=None,
) -> str:
    """根据每段的 ``shot_start`` / ``audio_path`` 拼接整段音轨。

    ``segments`` 每项至少包含：

    - ``shot_start``（float）: 分镜起点（秒）
    - ``audio_path``（str）: 该分镜对应的 MP3 路径
    - ``shot_duration``/``actual_duration``（可选）: 用于元信息

    会调用 ffmpeg 一次性完成 adelay + amix，输出固定 44.1kHz / 立体声 /
    192kbps MP3。空 segments 直接抛 ``ValueError``。
    """
    if not segments:
        raise ValueError("segments 不能为空")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    inputs: List[str] = []
    filter_parts: List[str] = []
    for i, seg in enumerate(segments):
        delay_ms = int(round(float(seg["shot_start"]) * 1000))
        inputs.extend(["-i", str(seg["audio_path"])])
        filter_parts.append(
            f"[{i}:a]adelay={delay_ms}|{delay_ms},apad[a{i}]"
        )
    mix_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    filter_graph = ";".join(filter_parts) + (
        f";{mix_inputs}amix=inputs={len(segments)}:duration=longest[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_graph,
        "-map", "[aout]",
        "-t", f"{float(total_duration):.3f}",
        "-ar", "44100", "-ac", "2",
        "-b:a", "192k",
        output_path,
    ]
    run = run_command or subprocess.run
    run(cmd, check=True, capture_output=True)
    return output_path


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_source_timeline_audio(
    segments: List[Dict[str, Any]],
    *,
    output_path: str,
    total_duration: float | None = None,
    run_command=None,
) -> str:
    """Build one AV TTS track without moving the source video timeline.

    Each input segment is placed at its source ``start_time``. Gaps between
    translated voice clips are therefore silence in the generated TTS track,
    instead of being collapsed by concat.
    """
    if not segments:
        raise ValueError("segments cannot be empty")

    stitch_segments: List[Dict[str, Any]] = []
    timeline_end = 0.0
    for segment in segments:
        segment_path = os.path.abspath(str(segment.get("tts_path") or ""))
        if not segment_path or not os.path.exists(segment_path):
            raise FileNotFoundError(f"missing TTS segment: {segment_path}")

        start_time = _float_value(segment.get("audio_start_time", segment.get("start_time")), 0.0)
        tts_duration = _float_value(segment.get("tts_duration"), 0.0)
        source_end = _float_value(segment.get("audio_end_time", segment.get("end_time")), 0.0)
        timeline_end = max(timeline_end, source_end if source_end > 0 else start_time + tts_duration)
        stitch_segments.append(
            {
                "shot_start": start_time,
                "shot_duration": tts_duration,
                "actual_duration": tts_duration,
                "audio_path": segment_path,
            }
        )

    output_duration = _float_value(total_duration, 0.0) if total_duration is not None else timeline_end
    if output_duration <= 0:
        output_duration = timeline_end
    if output_duration <= 0:
        raise ValueError("total duration cannot be zero")

    return build_stitched_audio(
        stitch_segments,
        total_duration=output_duration,
        output_path=output_path,
        run_command=run_command,
    )


def build_timeline_manifest(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """为 ``pipeline.compose.compose_video`` 构造 timeline_manifest。

    compose_video 在 ``_compose_soft_from_manifest`` 里会按
    ``manifest["segments"][*]["video_ranges"]`` 来切原视频段落。V2 模式下画面
    区间等于分镜区间（不拉伸视频），只是音频被 delay 到对应位置。因此这里
    为每个分镜输出一个 segment + 一个 video_range，首尾衔接，保证 compose
    阶段能还原整条原视频画面。

    另外同时写入一组平铺的 ``entries``（start/end/source_start/source_end），
    便于本功能的 e2e / 单元测试直接验证时间轴，不必再解析嵌套结构。
    """
    seg_entries: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
    total_tts_duration = 0.0
    video_consumed = 0.0
    for seg in segments:
        start = float(seg["shot_start"])
        duration = float(seg.get("shot_duration", 0.0))
        end = start + duration
        entry = {
            "start": start,
            "end": end,
            "source_start": start,
            "source_end": end,
        }
        entries.append(entry)
        seg_entries.append({
            "shot_start": start,
            "shot_duration": duration,
            "actual_duration": float(seg.get("actual_duration", 0.0)),
            "video_ranges": [{"start": start, "end": end}],
        })
        total_tts_duration = max(total_tts_duration, end)
        video_consumed += max(0.0, duration)

    return {
        "segments": seg_entries,
        "entries": entries,
        "total_tts_duration": total_tts_duration,
        "video_consumed_duration": video_consumed,
    }
