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
import tempfile

logger = logging.getLogger(__name__)


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
    """把「距顶百分比」转成 libass 可用的 ASS MarginV（基于 PlayResY=288）。

    libass 会按 video_height/288 线性缩放，因此 MarginV 基准用 288 而不是
    视频实际高度；不然会被 libass 再缩放一次把字幕推出画面。
    position_y=0.68 → MarginV=round(288*0.32)=92 → 1920p 视频实际距底 ≈614 px。
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
