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


class TimelineAudioOverflowError(RuntimeError):
    """Raised when a TTS segment cannot fit in the fixed source timeline."""


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
        clip_duration = seg.get("clip_duration")
        if clip_duration is not None:
            safe_clip_duration = max(0.001, float(clip_duration))
            filter_parts.append(
                f"[{i}:a]atrim=duration={safe_clip_duration:.3f},"
                f"asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms},apad[a{i}]"
            )
        else:
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


def _segment_label(segment: Dict[str, Any], fallback_index: int) -> str:
    asr_index = segment.get("asr_index", segment.get("index"))
    if asr_index is None:
        return f"#{fallback_index}"
    return f"asr_index={asr_index}"


def apply_compact_audio_schedule(
    sentences: List[Dict[str, Any]],
    *,
    max_gap: float = 0.25,
) -> List[Dict[str, Any]]:
    """Return sentences with a compact ASR-primary audio timeline.

    Source timing is preserved in ``source_*`` diagnostics. Actual audio
    stitching should use ``audio_start_time`` / ``audio_end_time``.
    """
    scheduled: List[Dict[str, Any]] = []
    cursor = 0.0
    previous_source_end: float | None = None
    gap_limit = max(0.0, float(max_gap))

    for index, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        item = dict(sentence)
        source_start = _float_value(item.get("source_start_time", item.get("start_time")), 0.0)
        source_end = _float_value(item.get("source_end_time", item.get("end_time")), source_start)
        tts_duration = _float_value(item.get("tts_duration"), 0.0)

        if index == 0 or previous_source_end is None:
            source_gap = 0.0
            audio_gap = 0.0
        else:
            source_gap = max(0.0, source_start - previous_source_end)
            audio_gap = min(source_gap, gap_limit)

        audio_start = cursor + audio_gap
        audio_end = audio_start + max(0.0, tts_duration)

        item["source_start_time"] = round(source_start, 3)
        item["source_end_time"] = round(source_end, 3)
        item["audio_start_time"] = round(audio_start, 3)
        item["audio_end_time"] = round(audio_end, 3)
        item["source_gap_before"] = round(source_gap, 3)
        item["audio_gap_before"] = round(audio_gap, 3)
        item["compact_gap_applied"] = source_gap > audio_gap + 0.001
        item["max_compact_gap"] = round(gap_limit, 3)
        item["timeline_mode"] = "compact_asr_primary"

        scheduled.append(item)
        cursor = audio_end
        previous_source_end = source_end

    return scheduled


def apply_asr_window_audio_schedule(
    sentences: List[Dict[str, Any]],
    *,
    max_gap: float = 0.25,
    preserve_gap_threshold: float = 1.0,
) -> List[Dict[str, Any]]:
    """Return sentences placed on ASR speech windows.

    Long no-ASR windows are treated as source background/silence and preserved.
    Short ASR segmentation gaps remain compact so split sentences do not sound
    choppy.
    """
    scheduled: List[Dict[str, Any]] = []
    cursor = 0.0
    previous_source_end: float | None = None
    gap_limit = max(0.0, float(max_gap))
    preserve_threshold = max(0.0, float(preserve_gap_threshold))

    for index, sentence in enumerate(sentences or []):
        if not isinstance(sentence, dict):
            continue
        item = dict(sentence)
        source_start = _float_value(item.get("source_start_time", item.get("start_time")), 0.0)
        source_end = _float_value(item.get("source_end_time", item.get("end_time")), source_start)
        tts_duration = _float_value(item.get("tts_duration"), 0.0)

        if index == 0 or previous_source_end is None:
            source_gap = max(0.0, source_start)
            gap_preserved = source_gap >= preserve_threshold and source_gap > 0.0
            audio_gap = source_gap if gap_preserved else 0.0
            compact_applied = False
        else:
            source_gap = max(0.0, source_start - previous_source_end)
            gap_preserved = source_gap >= preserve_threshold and source_gap > 0.0
            audio_gap = source_gap if gap_preserved else min(source_gap, gap_limit)
            compact_applied = source_gap > audio_gap + 0.001

        audio_start = cursor + audio_gap
        audio_end = audio_start + max(0.0, tts_duration)

        item["source_start_time"] = round(source_start, 3)
        item["source_end_time"] = round(source_end, 3)
        item["audio_start_time"] = round(audio_start, 3)
        item["audio_end_time"] = round(audio_end, 3)
        item["source_gap_before"] = round(source_gap, 3)
        item["audio_gap_before"] = round(audio_gap, 3)
        item["compact_gap_applied"] = compact_applied
        item["asr_window_gap_preserved"] = gap_preserved
        item["max_compact_gap"] = round(gap_limit, 3)
        item["preserve_gap_threshold"] = round(preserve_threshold, 3)
        item["timeline_mode"] = "asr_window_primary"

        scheduled.append(item)
        cursor = audio_end
        previous_source_end = source_end

    return scheduled


def _validate_source_timeline_fit(
    segments: List[Dict[str, Any]],
    *,
    total_duration: float | None,
    overflow_tolerance: float,
) -> None:
    ordered = sorted(
        [
            {
                "segment": segment,
                "index": index,
                "start": _float_value(segment.get("audio_start_time", segment.get("start_time")), 0.0),
                "end": _float_value(segment.get("audio_end_time", segment.get("end_time")), 0.0),
                "tts_duration": _float_value(segment.get("tts_duration"), 0.0),
            }
            for index, segment in enumerate(segments)
        ],
        key=lambda item: (item["start"], item["index"]),
    )
    output_limit = _float_value(total_duration, 0.0) if total_duration is not None else 0.0

    for pos, item in enumerate(ordered):
        start = item["start"]
        end = item["end"]
        tts_end = start + item["tts_duration"]
        label = _segment_label(item["segment"], item["index"])

        if end > start and tts_end > end + overflow_tolerance:
            raise TimelineAudioOverflowError(
                f"TTS segment {label} exceeds source window: "
                f"start={start:.3f}, source_end={end:.3f}, tts_end={tts_end:.3f}"
            )

        if output_limit > 0 and tts_end > output_limit + overflow_tolerance:
            raise TimelineAudioOverflowError(
                f"TTS segment {label} exceeds output timeline: "
                f"start={start:.3f}, output_end={output_limit:.3f}, tts_end={tts_end:.3f}"
            )

        if pos + 1 < len(ordered):
            next_start = ordered[pos + 1]["start"]
            if next_start > start and tts_end > next_start + overflow_tolerance:
                raise TimelineAudioOverflowError(
                    f"TTS segment {label} overlaps next sentence: "
                    f"start={start:.3f}, next_start={next_start:.3f}, tts_end={tts_end:.3f}"
                )


def _prepare_source_timeline_segments(
    segments: List[Dict[str, Any]],
    *,
    total_duration: float | None,
    overflow_tolerance: float,
) -> List[Dict[str, Any]]:
    ordered = sorted(
        [
            {
                "segment": segment,
                "index": index,
                "start": _float_value(segment.get("audio_start_time", segment.get("start_time")), 0.0),
                "end": _float_value(segment.get("audio_end_time", segment.get("end_time")), 0.0),
                "tts_duration": max(0.0, _float_value(segment.get("tts_duration"), 0.0)),
            }
            for index, segment in enumerate(segments)
        ],
        key=lambda item: (item["start"], item["index"]),
    )
    output_limit = _float_value(total_duration, 0.0) if total_duration is not None else 0.0

    for pos, item in enumerate(ordered):
        segment = item["segment"]
        start = item["start"]
        end = item["end"]
        tts_duration = item["tts_duration"]
        tts_end = start + tts_duration
        clip_duration = tts_duration
        clip_reason = ""

        def apply_bound(bound_end: float, reason: str) -> None:
            nonlocal clip_duration, clip_reason
            bound_duration = max(0.0, bound_end - start)
            if bound_duration + 0.0005 < clip_duration:
                clip_duration = bound_duration
                clip_reason = reason

        if end > start and tts_end > end + overflow_tolerance:
            apply_bound(end, "source_window")

        if pos + 1 < len(ordered):
            next_start = ordered[pos + 1]["start"]
            if next_start > start and tts_end > next_start + overflow_tolerance:
                apply_bound(next_start, "next_sentence")

        if output_limit > 0 and tts_end > output_limit + overflow_tolerance:
            apply_bound(output_limit, "output_timeline")

        clipped_seconds = max(0.0, tts_duration - clip_duration)
        if clipped_seconds > overflow_tolerance and clip_reason:
            segment["audio_clipped"] = True
            segment["audio_clip_reason"] = clip_reason
            segment["audio_clip_duration"] = round(clip_duration, 3)
            segment["audio_clipped_seconds"] = round(clipped_seconds, 3)
        else:
            segment["audio_clipped"] = False
            segment["audio_clip_duration"] = round(tts_duration, 3)
            segment["audio_clipped_seconds"] = 0.0
            segment.pop("audio_clip_reason", None)

    return segments


def build_source_timeline_audio(
    segments: List[Dict[str, Any]],
    *,
    output_path: str,
    total_duration: float | None = None,
    overflow_tolerance: float = 0.15,
    run_command=None,
) -> str:
    """Build one AV TTS track without moving the source video timeline.

    Each input segment is placed at its source ``start_time``. Gaps between
    translated voice clips are therefore silence in the generated TTS track,
    instead of being collapsed by concat.
    """
    if not segments:
        raise ValueError("segments cannot be empty")

    _prepare_source_timeline_segments(
        segments,
        total_duration=total_duration,
        overflow_tolerance=overflow_tolerance,
    )

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
        clip_duration = segment.get("audio_clip_duration") if segment.get("audio_clipped") else None
        stitch_segments.append(
            {
                "shot_start": start_time,
                "shot_duration": tts_duration,
                "actual_duration": tts_duration,
                "audio_path": segment_path,
                **({"clip_duration": clip_duration} if clip_duration is not None else {}),
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
