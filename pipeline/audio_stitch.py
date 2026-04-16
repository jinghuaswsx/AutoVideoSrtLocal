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
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


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
