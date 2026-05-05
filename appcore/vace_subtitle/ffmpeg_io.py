"""FFmpeg / ffprobe wrappers used by the VACE backend.

Design constraints (per CLAUDE.md / user requirements):
- All subprocess calls use ``list[str]``; ``shell=True`` is forbidden.
- Paths are :class:`pathlib.Path` so Windows backslashes/spaces work.
- Failures raise :class:`FFmpegError` with a tail-trimmed stderr summary.
- Long stdout is dropped (we don't pipe stdout for the inpainting workloads).
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

log = logging.getLogger(__name__)

_STDERR_TAIL = 2000   # max chars of stderr we surface in errors
_DEFAULT_RUN_TIMEOUT = 600   # 10min cap for a single ffmpeg invocation


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg / ffprobe invocation fails."""

    def __init__(self, message: str, *, returncode: int | None = None,
                 stderr_tail: str | None = None, cmd_summary: str | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.cmd_summary = cmd_summary


@dataclass(frozen=True)
class MediaInfo:
    """ffprobe-derived metadata."""

    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    nb_frames: int | None        # may be None when ffprobe can't tell

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if (self.width and self.height) else ""


def _run(
    cmd: Sequence[str],
    *,
    timeout: int = _DEFAULT_RUN_TIMEOUT,
    cwd: Path | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a list[str] command with consistent error surface."""
    cmd_summary = " ".join(str(c) for c in cmd[:3]) + (" ..." if len(cmd) > 3 else "")
    log.debug("vace_subtitle: ffmpeg/probe cmd: %s (cwd=%s)", cmd, cwd)
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegError(
            f"ffmpeg/ffprobe spawn failed: {exc}",
            cmd_summary=cmd_summary,
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-_STDERR_TAIL:]
        raise FFmpegError(
            f"command failed (rc={proc.returncode}): {cmd_summary}",
            returncode=proc.returncode,
            stderr_tail=tail,
            cmd_summary=cmd_summary,
        )
    return proc


def probe_media(
    path: Path,
    *,
    ffprobe_path: str = "ffprobe",
    timeout: int = 30,
) -> MediaInfo:
    """Run ffprobe and parse width / height / fps / duration / audio."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"video not found: {path}")
    cmd = [
        ffprobe_path, "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ]
    proc = _run(cmd, timeout=timeout)
    data = json.loads(proc.stdout or "{}")
    return _parse_media_info(data)


def _parse_media_info(data: dict) -> MediaInfo:
    """Pure parser kept separate so unit tests can feed canned ffprobe JSON."""
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    duration = _safe_float(video.get("duration")) or _safe_float(fmt.get("duration")) or 0.0
    nb_frames_raw = video.get("nb_frames")
    nb_frames: int | None = None
    if nb_frames_raw is not None:
        try:
            nb_frames = int(nb_frames_raw)
        except (TypeError, ValueError):
            nb_frames = None

    return MediaInfo(
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        has_audio=audio is not None,
        nb_frames=nb_frames,
    )


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _parse_rate(rate: str) -> float:
    """Parse 'num/den' fraction strings like '30000/1001'. Returns 0.0 on failure."""
    if not rate or "/" not in rate:
        try:
            return float(rate)
        except (TypeError, ValueError):
            return 0.0
    try:
        num_str, den_str = rate.split("/", 1)
        num = float(num_str)
        den = float(den_str)
        return num / den if den else 0.0
    except (TypeError, ValueError):
        return 0.0


def cut_chunk(
    *,
    src: Path,
    dst: Path,
    start_seconds: float,
    duration_seconds: float,
    ffmpeg_path: str = "ffmpeg",
    timeout: int = _DEFAULT_RUN_TIMEOUT,
    extra_filter: str | None = None,
) -> Path:
    """Cut a [start, start+duration] chunk from ``src`` into ``dst``.

    Re-encodes (libx264 + AAC) so frame timestamps are clean and seekable —
    stream-copy can yield non-monotonic PTS across chunk boundaries which
    breaks downstream concat/composite math.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path, "-y",
        "-ss", f"{start_seconds:.3f}",
        "-i", str(src),
        "-t", f"{duration_seconds:.3f}",
    ]
    if extra_filter:
        cmd += ["-vf", extra_filter]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd, timeout=timeout)
    return dst


def crop_chunk(
    *,
    src: Path,
    dst: Path,
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    target_w: int,
    target_h: int,
    pad_left: int,
    pad_top: int,
    ffmpeg_path: str = "ffmpeg",
    timeout: int = _DEFAULT_RUN_TIMEOUT,
) -> Path:
    """Crop, scale, and pad a video chunk into VACE-friendly dimensions.

    Pipeline:
        crop(crop_w, crop_h, crop_x, crop_y)
        -> scale(inner_w, inner_h)        [inner = target - 2*pad]
        -> pad(target_w, target_h, pad_left, pad_top, color=black)

    The output preserves the source frame rate and timestamps; only spatial
    geometry is changed. This lets us re-merge chunks without re-timing.
    """
    if crop_w <= 0 or crop_h <= 0:
        raise ValueError(f"crop must be positive, got {crop_w}x{crop_h}")
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"target must be positive, got {target_w}x{target_h}")
    inner_w = max(1, target_w - 2 * pad_left)
    inner_h = max(1, target_h - 2 * pad_top)
    # Note: pad uses absolute offsets, not symmetric; we replicate ScalePlan's
    # centered placement (pad_left, pad_top, pad_right=pad_left, pad_bottom=pad_top).
    vf = (
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
        f"scale={inner_w}:{inner_h}:flags=lanczos,"
        f"pad={target_w}:{target_h}:{pad_left}:{pad_top}:color=black"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path, "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-an",
        str(dst),
    ]
    _run(cmd, timeout=timeout)
    return dst


def concat_chunks(
    *,
    chunk_paths: Iterable[Path],
    dst: Path,
    list_file_path: Path,
    ffmpeg_path: str = "ffmpeg",
    timeout: int = _DEFAULT_RUN_TIMEOUT,
) -> Path:
    """Concat multiple chunks into ``dst`` via ffmpeg's concat demuxer.

    All chunk files MUST share the same codec/resolution/fps. We re-encode
    on output so PTS is monotonic and the result is web-friendly.
    """
    paths = [Path(p) for p in chunk_paths]
    if not paths:
        raise ValueError("concat_chunks: empty chunk list")
    list_file_path.parent.mkdir(parents=True, exist_ok=True)
    # ffconcat list: each entry must escape backslashes for the demuxer parser.
    list_file_path.write_text(
        "\n".join(f"file '{str(p).replace(chr(92), '/')}'" for p in paths),
        encoding="utf-8",
    )
    cmd = [
        ffmpeg_path, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd, timeout=timeout)
    return dst


def mux_audio_from_source(
    *,
    video_src: Path,    # video-only output we want to keep
    audio_src: Path,    # original video to pull audio from (may have no audio)
    dst: Path,
    ffmpeg_path: str = "ffmpeg",
    timeout: int = _DEFAULT_RUN_TIMEOUT,
) -> Path:
    """Copy the video stream from ``video_src`` and mux audio from ``audio_src``.

    If ``audio_src`` has no audio, the output stays video-only (no error).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path, "-y",
        "-i", str(video_src),
        "-i", str(audio_src),
        "-map", "0:v:0",
        "-map", "1:a:0?",          # '?' = optional, OK if audio absent
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd, timeout=timeout)
    return dst
