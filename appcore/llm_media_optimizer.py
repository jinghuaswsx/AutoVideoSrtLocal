"""Prepare temporary, smaller media files for multimodal LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import Iterable

from pipeline.ffutil import probe_media_info


log = logging.getLogger(__name__)

MIB = 1024 * 1024


@dataclass(frozen=True)
class VideoOptimizationPolicy:
    name: str
    max_height: int
    fps: int = 15
    video_bitrate: str | None = "600k"
    maxrate: str | None = "800k"
    bufsize: str | None = "1200k"
    drop_audio: bool = True
    audio_bitrate: str = "64k"
    target_bytes: int | None = None
    bitrate_safety_ratio: float = 0.82
    min_video_bitrate_k: int = 180
    first_pass_height: int | None = None
    timeout_seconds: int = 600
    suffix_label: str = "llm"


@dataclass(frozen=True)
class OptimizedMedia:
    original_path: str
    llm_path: str
    optimized: bool
    cleanup_path: str | None = None
    original_bytes: int | None = None
    llm_bytes: int | None = None
    command: list[str] | None = None
    error: str | None = None
    policy_name: str = ""


VISUAL_480P_SILENT = VideoOptimizationPolicy(
    name="visual_480p_silent",
    max_height=480,
    fps=15,
    video_bitrate="600k",
    maxrate="800k",
    bufsize="1200k",
    drop_audio=True,
    suffix_label="visual480p",
)

REVIEW_480P_AUDIO = VideoOptimizationPolicy(
    name="review_480p_audio",
    max_height=480,
    fps=15,
    video_bitrate="600k",
    maxrate="800k",
    bufsize="1200k",
    drop_audio=False,
    audio_bitrate="64k",
    suffix_label="review480p",
)

SHORT_CLIP_AUDIO = VideoOptimizationPolicy(
    name="short_clip_audio",
    max_height=480,
    fps=15,
    video_bitrate="600k",
    maxrate="800k",
    bufsize="1200k",
    drop_audio=False,
    audio_bitrate="64k",
    suffix_label="short480p",
)

VERTEX_INLINE_AUDIO = VideoOptimizationPolicy(
    name="vertex_inline_audio",
    max_height=480,
    fps=15,
    video_bitrate="600k",
    maxrate="800k",
    bufsize="1200k",
    drop_audio=False,
    audio_bitrate="64k",
    suffix_label="vertexinline",
)


def prepare_video_for_llm(
    video_path: str | os.PathLike[str],
    policy: VideoOptimizationPolicy,
    *,
    output_dir: str | os.PathLike[str] | None = None,
    output_path: str | os.PathLike[str] | None = None,
) -> OptimizedMedia:
    """Return the path that should be sent to an LLM, falling back on failure."""
    original = Path(video_path)
    original_path = str(original)
    original_bytes = _file_size(original)
    if not original.exists():
        return OptimizedMedia(
            original_path=original_path,
            llm_path=original_path,
            optimized=False,
            original_bytes=original_bytes,
            llm_bytes=original_bytes,
            error="source_missing",
            policy_name=policy.name,
        )

    try:
        info = probe_media_info(str(original)) or {}
    except Exception as exc:  # pragma: no cover - defensive around ffprobe wrappers
        log.warning("probe failed before LLM media optimization for %s: %s", original, exc)
        info = {}

    duration = _as_float(info.get("duration"))
    command: list[str] | None = None
    last_error: str | None = None
    for height in _candidate_heights(policy):
        if policy.target_bytes and duration <= 0:
            last_error = "duration_unavailable"
            break
        out_path = _new_output_path(original, policy, output_dir=output_dir, output_path=output_path)
        try:
            command = _build_ffmpeg_command(
                source=original,
                output=out_path,
                policy=policy,
                height=height,
                duration=duration,
            )
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                check=True,
            )
            output_bytes = _file_size(out_path)
            if not output_bytes:
                raise RuntimeError("ffmpeg produced empty optimized video")
            if policy.target_bytes and output_bytes > policy.target_bytes:
                last_error = (
                    f"optimized_over_target:{output_bytes}>{policy.target_bytes}"
                )
                _unlink_quietly(out_path)
                continue
            return OptimizedMedia(
                original_path=original_path,
                llm_path=str(out_path),
                optimized=True,
                cleanup_path=str(out_path),
                original_bytes=original_bytes,
                llm_bytes=output_bytes,
                command=command,
                policy_name=policy.name,
            )
        except subprocess.CalledProcessError as exc:
            last_error = _called_process_error_message(exc)
            _unlink_quietly(out_path)
            break
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            _unlink_quietly(out_path)
            break

    return OptimizedMedia(
        original_path=original_path,
        llm_path=original_path,
        optimized=False,
        cleanup_path=None,
        original_bytes=original_bytes,
        llm_bytes=original_bytes,
        command=command,
        error=last_error or "optimization_failed",
        policy_name=policy.name,
    )


def cleanup_optimized_media(media: OptimizedMedia | None) -> None:
    if not media or not media.cleanup_path:
        return
    cleanup_path = Path(media.cleanup_path)
    try:
        if cleanup_path.resolve() != Path(media.original_path).resolve():
            cleanup_path.unlink(missing_ok=True)
    except OSError:
        log.warning("failed to cleanup optimized LLM media: %s", cleanup_path, exc_info=True)


def media_debug_snapshot(media: OptimizedMedia | None) -> dict:
    if media is None:
        return {}
    return {
        "original_video_path": media.original_path,
        "llm_video_path": media.llm_path,
        "optimized": media.optimized,
        "policy_name": media.policy_name,
        "original_bytes": media.original_bytes,
        "llm_bytes": media.llm_bytes,
        "ffmpeg_command": _format_command(media.command),
        "optimization_error": media.error,
        "cleanup_path": media.cleanup_path,
    }


def _candidate_heights(policy: VideoOptimizationPolicy) -> Iterable[int]:
    heights: list[int] = []
    if policy.first_pass_height:
        heights.append(policy.first_pass_height)
    heights.append(policy.max_height)
    seen: set[int] = set()
    for height in heights:
        if height > 0 and height not in seen:
            seen.add(height)
            yield height


def _build_ffmpeg_command(
    *,
    source: Path,
    output: Path,
    policy: VideoOptimizationPolicy,
    height: int,
    duration: float,
) -> list[str]:
    video_bitrate, maxrate, bufsize = _bitrate_settings(policy, duration)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        f"scale=-2:min({height}\\,ih),fps={policy.fps}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        video_bitrate,
        "-maxrate",
        maxrate,
        "-bufsize",
        bufsize,
    ]
    if policy.drop_audio:
        cmd.append("-an")
    else:
        cmd.extend(["-c:a", "aac", "-b:a", policy.audio_bitrate, "-ac", "1"])
    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def _bitrate_settings(
    policy: VideoOptimizationPolicy,
    duration: float,
) -> tuple[str, str, str]:
    if not policy.target_bytes:
        return (
            policy.video_bitrate or "600k",
            policy.maxrate or "800k",
            policy.bufsize or "1200k",
        )
    audio_k = 0 if policy.drop_audio else _parse_bitrate_k(policy.audio_bitrate)
    usable_video_bits = int(policy.target_bytes * 8 * policy.bitrate_safety_ratio)
    video_k = max(
        policy.min_video_bitrate_k,
        int(usable_video_bits / max(duration, 1.0) / 1000) - audio_k,
    )
    maxrate_k = max(video_k, int(video_k * 1.25))
    bufsize_k = max(video_k, int(video_k * 2))
    return f"{video_k}k", f"{maxrate_k}k", f"{bufsize_k}k"


def _parse_bitrate_k(value: str) -> int:
    normalized = value.strip().lower()
    try:
        if normalized.endswith("k"):
            return int(float(normalized[:-1]))
        if normalized.endswith("m"):
            return int(float(normalized[:-1]) * 1000)
        return int(float(normalized) / 1000)
    except ValueError:
        return 64


def _new_output_path(
    original: Path,
    policy: VideoOptimizationPolicy,
    *,
    output_dir: str | os.PathLike[str] | None,
    output_path: str | os.PathLike[str] | None,
) -> Path:
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    directory = Path(output_dir) if output_dir is not None else original.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f"{original.stem}.{policy.suffix_label}.",
        suffix=".mp4",
        dir=str(directory),
    )
    os.close(fd)
    return Path(temp_path)


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _as_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _called_process_error_message(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if stderr:
        return str(stderr)[-500:]
    return f"ffmpeg failed with return code {exc.returncode}"


def _format_command(command: list[str] | None) -> str | None:
    if not command:
        return None
    return " ".join(shlex.quote(str(part)) for part in command)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
