"""
视频合成模块：
1. 使用 timeline_manifest 按段消费原视频画面
2. 保留完整英文 TTS 音轨，不拉伸不压缩
3. 输出软字幕版（mp4 + srt）和硬字幕版（烧录）
"""
import json
import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

FINAL_VIDEO_SIZE_LIMIT_BYTES = 100 * 1024 * 1024
FINAL_VIDEO_SIZE_SAFETY_RATIO = 0.96
DEFAULT_SIZE_ADJUST_AUDIO_BITRATE_BPS = 128_000
MIN_SIZE_ADJUST_AUDIO_BITRATE_BPS = 64_000
MAX_SIZE_ADJUST_AUDIO_BITRATE_BPS = 128_000
MIN_SIZE_ADJUST_VIDEO_BITRATE_BPS = 300_000
SIZE_ADJUST_VIDEO_BITRATE_STEP_BPS = 1_000_000


def _run_ffmpeg(cmd: list, error_prefix: str) -> None:
    """运行 ffmpeg 命令；stderr 写临时文件再读取（避免 eventlet + PIPE 死锁）。

    在 gunicorn + eventlet 环境下，subprocess.run(capture_output=True) 对长时间
    ffmpeg 进程会因 PIPE 缓冲区满导致进程被提前 kill。所以改成把 stderr
    落到磁盘临时文件，等进程退出后再读回来。
    """
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".log", delete=False,
        encoding="utf-8", errors="replace",
    ) as stderr_fp:
        stderr_path = stderr_fp.name
    try:
        with open(stderr_path, "wb") as err_w:
            rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=err_w)
        if rc != 0:
            try:
                with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
                    err_text = f.read()
            except Exception:
                err_text = f"<failed to read {stderr_path}>"
            # 只保留 stderr 最后 4000 字符，足够定位且不撑爆日志
            raise RuntimeError(f"{error_prefix}: {err_text[-4000:]}")
    finally:
        try:
            os.unlink(stderr_path)
        except OSError:
            pass


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _format_size_mb(size_bytes: int | float | None) -> str:
    if not size_bytes:
        return "0.0 MB"
    return f"{float(size_bytes) / 1024 / 1024:.1f} MB"


def _format_bitrate_mbps(bitrate_bps: int | float | None) -> str:
    if not bitrate_bps:
        return "0.00 Mbps"
    kbps = max(1, int(round(float(bitrate_bps) / 1000)))
    return f"{float(bitrate_bps) / 1_000_000:.2f} Mbps ({kbps} kbps)"


def _probe_media_info(video_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 获取视频信息失败: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe 返回了无法解析的视频信息: {exc}") from exc

    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration_seconds": _safe_float(
            video_stream.get("duration") or fmt.get("duration")
        ),
        "format_bitrate_bps": _safe_int(fmt.get("bit_rate")),
        "video_bitrate_bps": _safe_int(video_stream.get("bit_rate")),
        "audio_bitrate_bps": _safe_int(audio_stream.get("bit_rate")),
    }


def _probe_media_info_or_empty(video_path: str) -> dict:
    try:
        return _probe_media_info(video_path)
    except Exception:
        logger.warning("ffprobe 获取视频大小调整信息失败: %s", video_path, exc_info=True)
        return {}


def _clamp_audio_bitrate_bps(original_audio_bitrate_bps: int | None) -> int:
    audio_bps = int(original_audio_bitrate_bps or DEFAULT_SIZE_ADJUST_AUDIO_BITRATE_BPS)
    audio_bps = max(
        MIN_SIZE_ADJUST_AUDIO_BITRATE_BPS,
        min(audio_bps, MAX_SIZE_ADJUST_AUDIO_BITRATE_BPS),
    )
    return max(MIN_SIZE_ADJUST_AUDIO_BITRATE_BPS, (audio_bps // 1000) * 1000)


def _floor_bitrate_to_kbps_bps(bitrate_bps: int | float | None) -> int:
    return max(0, int(float(bitrate_bps or 0)) // 1000 * 1000)


def _snap_video_bitrate_bps(raw_video_bitrate_bps: int | float | None) -> int:
    """Snap target video bitrate down to clear operator-facing steps.

    Normal outputs use whole 1000 kbps steps such as 4000k / 5000k. When the
    file-size budget is below the first 1000 kbps step, keep an integer-kbps
    emergency fallback so long videos can still be reduced below the limit.
    """
    bitrate = _floor_bitrate_to_kbps_bps(raw_video_bitrate_bps)
    if bitrate >= SIZE_ADJUST_VIDEO_BITRATE_STEP_BPS:
        return max(
            SIZE_ADJUST_VIDEO_BITRATE_STEP_BPS,
            (bitrate // SIZE_ADJUST_VIDEO_BITRATE_STEP_BPS) * SIZE_ADJUST_VIDEO_BITRATE_STEP_BPS,
        )
    return max(MIN_SIZE_ADJUST_VIDEO_BITRATE_BPS, bitrate)


def _calculate_size_adjustment_bitrates(
    duration_seconds: float,
    *,
    limit_bytes: int = FINAL_VIDEO_SIZE_LIMIT_BYTES,
    safety_ratio: float = FINAL_VIDEO_SIZE_SAFETY_RATIO,
    original_audio_bitrate_bps: int | None = None,
    target_total_bitrate_bps: int | None = None,
) -> dict:
    duration = float(duration_seconds or 0.0)
    if duration <= 0:
        raise ValueError("duration_seconds must be greater than 0")

    target_total_budget = _floor_bitrate_to_kbps_bps(
        target_total_bitrate_bps
        or (int(limit_bytes) * float(safety_ratio) * 8 / duration)
    )
    target_audio = _clamp_audio_bitrate_bps(original_audio_bitrate_bps)
    target_video = target_total_budget - target_audio
    if target_video < MIN_SIZE_ADJUST_VIDEO_BITRATE_BPS:
        target_audio = MIN_SIZE_ADJUST_AUDIO_BITRATE_BPS
        target_video = target_total_budget - target_audio
    target_video = _snap_video_bitrate_bps(target_video)
    target_audio = _clamp_audio_bitrate_bps(target_audio)
    target_total = target_video + target_audio
    return {
        "target_total_bitrate_bps": int(target_total),
        "target_video_bitrate_bps": int(target_video),
        "target_audio_bitrate_bps": int(target_audio),
    }


def _bitrate_arg(bitrate_bps: int) -> str:
    return f"{max(1, int(int(bitrate_bps) // 1000))}k"


def _bitrate_kbps(bitrate_bps: int) -> int:
    return max(1, int(int(bitrate_bps) // 1000))


def _size_adjusted_output_path(input_path: str) -> str:
    root, ext = os.path.splitext(input_path)
    return f"{root}_size_adjusted{ext or '.mp4'}"


def _encode_video_with_bitrate(
    input_path: str,
    output_path: str,
    *,
    target_video_bitrate_bps: int,
    target_audio_bitrate_bps: int,
) -> None:
    video_kbps = _bitrate_kbps(target_video_bitrate_bps)
    video_rate = f"{video_kbps}k"
    audio_rate = _bitrate_arg(target_audio_bitrate_bps)
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "slow",
        "-b:v", video_rate,
        "-maxrate", video_rate,
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac",
        "-b:a", audio_rate,
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, "视频大小调整失败")


def adjust_video_size_to_limit(
    video_path: str,
    *,
    limit_bytes: int = FINAL_VIDEO_SIZE_LIMIT_BYTES,
    safety_ratio: float = FINAL_VIDEO_SIZE_SAFETY_RATIO,
    output_path: str | None = None,
    variant: str | None = None,
) -> dict:
    """Ensure a final video file stays within the configured byte limit."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)

    input_size_bytes = os.path.getsize(video_path)
    media_info = _probe_media_info_or_empty(video_path)
    duration_seconds = _safe_float(media_info.get("duration_seconds"))
    original_total_bitrate_bps = _safe_int(media_info.get("format_bitrate_bps"))
    original_audio_bitrate_bps = _safe_int(media_info.get("audio_bitrate_bps"))
    original_video_bitrate_bps = _safe_int(media_info.get("video_bitrate_bps"))

    if not original_total_bitrate_bps and duration_seconds > 0:
        original_total_bitrate_bps = int(input_size_bytes * 8 / duration_seconds)
    if not original_video_bitrate_bps and original_total_bitrate_bps:
        original_video_bitrate_bps = max(
            0,
            original_total_bitrate_bps - original_audio_bitrate_bps,
        )

    summary = {
        "status": "skipped",
        "limit_bytes": int(limit_bytes),
        "safety_ratio": float(safety_ratio),
        "variant": variant or "",
        "input_path": video_path,
        "output_path": video_path,
        "input_size_bytes": input_size_bytes,
        "output_size_bytes": input_size_bytes,
        "duration_seconds": duration_seconds,
        "original_total_bitrate_bps": original_total_bitrate_bps,
        "original_video_bitrate_bps": original_video_bitrate_bps,
        "original_audio_bitrate_bps": original_audio_bitrate_bps,
        "target_total_bitrate_bps": original_total_bitrate_bps,
        "target_video_bitrate_bps": original_video_bitrate_bps,
        "target_audio_bitrate_bps": original_audio_bitrate_bps,
        "attempts": [],
        "message": (
            f"视频大小 {_format_size_mb(input_size_bytes)}，未超过 "
            f"{_format_size_mb(limit_bytes)}，无需调整。"
        ),
    }

    if input_size_bytes <= int(limit_bytes):
        return summary

    if duration_seconds <= 0:
        summary.update({
            "status": "failed",
            "message": f"视频大小超过 {_format_size_mb(limit_bytes)}，但无法读取视频时长，不能安全计算目标码率。",
        })
        return summary

    targets = _calculate_size_adjustment_bitrates(
        duration_seconds,
        limit_bytes=limit_bytes,
        safety_ratio=safety_ratio,
        original_audio_bitrate_bps=original_audio_bitrate_bps,
    )
    target_total_bitrate_bps = targets["target_total_bitrate_bps"]
    target_video_bitrate_bps = targets["target_video_bitrate_bps"]
    target_audio_bitrate_bps = targets["target_audio_bitrate_bps"]
    final_output_path = output_path or _size_adjusted_output_path(video_path)

    for attempt_no in (1, 2):
        _encode_video_with_bitrate(
            video_path,
            final_output_path,
            target_video_bitrate_bps=target_video_bitrate_bps,
            target_audio_bitrate_bps=target_audio_bitrate_bps,
        )
        output_size_bytes = os.path.getsize(final_output_path)
        attempt = {
            "attempt": attempt_no,
            "target_total_bitrate_bps": int(target_total_bitrate_bps),
            "target_video_bitrate_bps": int(target_video_bitrate_bps),
            "target_audio_bitrate_bps": int(target_audio_bitrate_bps),
            "output_size_bytes": int(output_size_bytes),
        }
        summary["attempts"].append(attempt)
        summary.update({
            "output_path": final_output_path,
            "output_size_bytes": int(output_size_bytes),
            "target_total_bitrate_bps": int(target_total_bitrate_bps),
            "target_video_bitrate_bps": int(target_video_bitrate_bps),
            "target_audio_bitrate_bps": int(target_audio_bitrate_bps),
        })
        if output_size_bytes <= int(limit_bytes):
            summary.update({
                "status": "adjusted",
                "message": (
                    f"视频大小 {_format_size_mb(input_size_bytes)}，已按总码率 "
                    f"{_format_bitrate_mbps(original_total_bitrate_bps)} -> "
                    f"{_format_bitrate_mbps(target_total_bitrate_bps)}、视频码率 "
                    f"{_format_bitrate_mbps(original_video_bitrate_bps)} -> "
                    f"{_format_bitrate_mbps(target_video_bitrate_bps)} 重编码，"
                    f"最终 {_format_size_mb(output_size_bytes)}。"
                ),
            })
            return summary

        overshoot_ratio = (int(limit_bytes) * float(safety_ratio)) / max(output_size_bytes, 1)
        next_total_bitrate_bps = int(target_total_bitrate_bps * max(0.5, min(0.95, overshoot_ratio)))
        targets = _calculate_size_adjustment_bitrates(
            duration_seconds,
            limit_bytes=limit_bytes,
            safety_ratio=safety_ratio,
            original_audio_bitrate_bps=target_audio_bitrate_bps,
            target_total_bitrate_bps=next_total_bitrate_bps,
        )
        target_total_bitrate_bps = targets["target_total_bitrate_bps"]
        target_video_bitrate_bps = targets["target_video_bitrate_bps"]
        target_audio_bitrate_bps = targets["target_audio_bitrate_bps"]

    summary.update({
        "status": "failed",
        "message": (
            f"视频大小调整后仍为 {_format_size_mb(summary.get('output_size_bytes'))}，"
            f"超过 {_format_size_mb(limit_bytes)}，已停止交付超限视频。"
        ),
    })
    return summary

_FONT_SIZE_BASE: dict[str, int] = {"small": 11, "medium": 14, "large": 18}
_VALID_FONT_NAME = re.compile(r'^[A-Za-z0-9 \-_]+$')

# Impact 是 Microsoft 专有字体，Linux 服务器上没有；用开源视觉相近的 Anton 代替
_FONT_ALIAS: dict[str, str] = {
    "Impact": "Anton",
}


def _fonts_dir() -> str:
    """返回项目 fonts/ 目录的绝对路径。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _compute_font_size(preset) -> int:
    """把字号预设转成 libass 可用的 ASS FontSize（基于 PlayResY=288）。

    preset 可以是数字（直接作为 ASS FontSize）或字符串档位（small/medium/large）。

    libass 会把这个值按 video_height/288 线性缩放到实际像素，因此这里
    给定的值是"SRT 默认 ASS 画布"上的尺寸，视频分辨率越高字号自动越大，
    不需要我们再乘 video_height/1080。
    """
    if isinstance(preset, (int, float)):
        return int(preset)
    return _FONT_SIZE_BASE.get(preset, _FONT_SIZE_BASE["medium"])


def _compute_margin_v(position_y: float) -> int:
    """把字幕外框底边的「距顶百分比」转成 ASS MarginV（基于 PlayResY=288）。

    libass 会按 video_height/288 线性缩放，因此 MarginV 基准用 288 而不是
    视频实际高度；不然会被 libass 再缩放一次把字幕推出画面。
    position_y=0.68 → MarginV=round(288*0.32)=92，生成字幕底边落在画面
    高度约 68% 处。前端预览必须用同一个底边锚点，不能把它当中心点。
    """
    return round(288 * (1.0 - position_y))


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
    with_soft: bool = True,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    suffix = f".{variant}" if variant else ""

    soft_output = os.path.join(output_dir, f"{base_name}_soft{suffix}.mp4")
    hard_output = os.path.join(output_dir, f"{base_name}_hard{suffix}.mp4")

    # 硬字幕合成依赖软字幕中间产物；with_soft=False 时合成后把 soft 文件删掉
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

    if not with_soft:
        try:
            os.remove(soft_output)
        except OSError:
            pass
        soft_output = None

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
    video_duration = float(manifest.get("video_duration", 0.0) or 0.0)
    video_consumed = float(manifest.get("video_consumed_duration", 0.0) or 0.0)
    output_duration = video_duration if video_duration > 0 else total_duration
    if video_duration > 0 and total_duration + 0.001 < video_duration and video_consumed + 0.001 < video_duration:
        label = f"v{segment_idx}"
        trim_labels.append(f"[{label}]")
        filter_parts.append(
            f"[0:v]trim=start={round(video_consumed, 3)}:end={round(video_duration, 3)},setpts=PTS-STARTPTS[{label}]"
        )
        segment_idx += 1
        video_consumed = video_duration

    concat_label = "vcat"
    if len(trim_labels) == 1:
        filter_parts.append(f"{trim_labels[0]}null[{concat_label}]")
    else:
        filter_parts.append("".join(trim_labels) + f"concat=n={len(trim_labels)}:v=1:a=0[{concat_label}]")

    final_label = "vout"
    pad_duration = max(output_duration - video_consumed, 0.0)
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
        "-t", str(round(output_duration, 3)),
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, "软字幕版合成失败")


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
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, "软字幕版合成失败")


def _compose_hard(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_name: str = "Impact",
    font_size_preset: str = "medium",
    subtitle_position_y: float = 0.68,
) -> None:
    font_size_pt = _compute_font_size(font_size_preset)
    margin_v = _compute_margin_v(subtitle_position_y)
    vf = _build_subtitle_filter(srt_path, font_name, font_size_pt, margin_v)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    _run_ffmpeg(cmd, "硬字幕版合成失败")


def _build_subtitle_filter(srt_path: str, font_name: str, font_size_pt: int, margin_v: int) -> str:
    if not _VALID_FONT_NAME.match(font_name):
        font_name = "Impact"
    # 把用户选择的字体名映射到服务器端实际可用的字体（如 Impact → Anton）
    font_name = _FONT_ALIAS.get(font_name, font_name)
    escaped_path = _escape_subtitle_filter_path(srt_path)
    # 只在 fonts 目录存在时才传 fontsdir；目录不存在时 libass 仍能用系统字体渲染
    fd = _fonts_dir()
    fontsdir_param = f":fontsdir='{_escape_subtitle_filter_path(fd)}'" if os.path.isdir(fd) else ""
    # 使用完整 8 位十六进制颜色（AABBGGRR）避免部分 libass 版本把 3 字节 &HFFFFFF
    # 误判为半透明/空 alpha 导致字幕不可见；显式指定 BorderStyle=1 确保走
    # outline + shadow 渲染路径，避免系统字体回退时走 opaque box 或跳过渲染。
    return (
        f"subtitles=filename='{escaped_path}'"
        f"{fontsdir_param}"
        f":force_style='FontName={font_name},FontSize={font_size_pt},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,"
        f"Outline=2,Shadow=0,Bold=1,Alignment=2,MarginV={margin_v}'"
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
