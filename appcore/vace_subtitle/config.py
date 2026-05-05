"""Profile / env config for the VACE Windows subtitle backend.

Profiles control the trade-off between quality and VRAM/throughput.

Three profiles (RTX 3060 12GB targeted):
- ``rtx3060_safe`` — default. 1.3B model, 480p, 41 frames/chunk, 20 steps.
- ``rtx3060_balanced`` — 1.3B model, 480p, 81 frames/chunk, 25 steps.
- ``rtx3060_quality_experimental`` — 14B model, 720p, 41 frames/chunk, 25 steps.
  Likely OOMs; auto-falls-back to ``rtx3060_safe`` when so.

Environment variables consumed (all optional unless noted):
- ``VACE_REPO_DIR``      — clone of https://github.com/ali-vilab/VACE
- ``VACE_PYTHON_EXE``    — python.exe inside VACE's own venv
- ``VACE_MODEL_DIR``     — path to ``models/Wan2.1-VACE-1.3B`` (or 14B)
- ``VACE_MODEL_NAME``    — overrides profile model_name (e.g. ``vace-1.3B``)
- ``VACE_SIZE``          — overrides profile size (e.g. ``480p`` / ``720p``)
- ``VACE_PROFILE``       — selects profile by name
- ``VACE_RESULTS_DIR``   — base dir for per-job working dirs (default: temp)
- ``VACE_TIMEOUT_SEC``   — subprocess.run timeout per chunk (default: 1800)
- ``FFMPEG_PATH``        — ffmpeg.exe absolute path (default: 'ffmpeg' on PATH)
- ``FFPROBE_PATH``       — ffprobe.exe absolute path (default: 'ffprobe' on PATH)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping


class VaceConfigError(RuntimeError):
    """Raised when env / profile config is invalid or VACE deps are missing."""


@dataclass(frozen=True)
class VaceProfile:
    """Runtime tuning for one VACE pipeline invocation.

    All fields are explicit so callers can audit before subprocess launch.
    """

    name: str
    model_name: str           # e.g. "vace-1.3B" / "vace-14B"
    size: str                 # e.g. "480p" / "720p"
    frame_num: int            # must satisfy 4n+1; capped at 81
    sample_steps: int
    offload_model: bool
    t5_cpu: bool
    chunk_seconds: float      # nominal chunk length before frame_num clamp
    max_long_edge: int        # ROI long-edge cap before VACE input
    max_short_edge: int       # ROI short-edge cap before VACE input

    def __post_init__(self) -> None:
        if (self.frame_num - 1) % 4 != 0:
            raise VaceConfigError(
                f"frame_num must satisfy 4n+1, got {self.frame_num} (profile={self.name})"
            )
        if self.frame_num > 81:
            raise VaceConfigError(
                f"frame_num must be <= 81, got {self.frame_num} (profile={self.name})"
            )
        if self.chunk_seconds <= 0:
            raise VaceConfigError(
                f"chunk_seconds must be > 0, got {self.chunk_seconds}"
            )


PROFILES: Mapping[str, VaceProfile] = {
    "rtx3060_safe": VaceProfile(
        name="rtx3060_safe",
        model_name="vace-1.3B",
        size="480p",
        frame_num=41,
        sample_steps=20,
        offload_model=True,
        t5_cpu=True,
        chunk_seconds=2.7,
        max_long_edge=832,
        max_short_edge=480,
    ),
    "rtx3060_balanced": VaceProfile(
        name="rtx3060_balanced",
        model_name="vace-1.3B",
        size="480p",
        frame_num=81,
        sample_steps=25,
        offload_model=True,
        t5_cpu=True,
        chunk_seconds=4.8,
        max_long_edge=832,
        max_short_edge=480,
    ),
    "rtx3060_quality_experimental": VaceProfile(
        name="rtx3060_quality_experimental",
        model_name="vace-14B",
        size="720p",
        frame_num=41,
        sample_steps=25,
        offload_model=True,
        t5_cpu=True,
        chunk_seconds=2.7,
        max_long_edge=1280,
        max_short_edge=720,
    ),
}

DEFAULT_PROFILE_NAME = "rtx3060_safe"
DEFAULT_TIMEOUT_SEC = 1800
DEFAULT_PROMPT = (
    "clean natural video background, no subtitles, no text, no watermark"
)


def get_profile(name: str | None) -> VaceProfile:
    """Resolve a profile by name. Defaults to rtx3060_safe."""
    pname = (name or DEFAULT_PROFILE_NAME).strip()
    if pname not in PROFILES:
        raise VaceConfigError(
            f"unknown profile {pname!r}; available: {sorted(PROFILES)}"
        )
    return PROFILES[pname]


def fallback_profile(current: VaceProfile) -> VaceProfile | None:
    """Compute a more conservative variant of ``current`` for OOM recovery.

    Returns ``None`` when ``current`` is already at the safe floor.

    Recovery policy (one step at a time, caller may chain):
      1. quality_experimental -> rtx3060_safe
      2. frame_num=81 -> 41
      3. sample_steps>20 -> 20
      4. chunk_seconds>2.5 -> 2.5
    """
    if current.name == "rtx3060_quality_experimental":
        return PROFILES["rtx3060_safe"]
    if current.frame_num > 41:
        return replace(current, name=f"{current.name}+frame41", frame_num=41)
    if current.sample_steps > 20:
        return replace(current, name=f"{current.name}+steps20", sample_steps=20)
    if current.chunk_seconds > 2.5:
        return replace(current, name=f"{current.name}+chunk2.5", chunk_seconds=2.5)
    return None


@dataclass(frozen=True)
class VaceEnv:
    """Resolved environment paths/binaries for one invocation.

    All paths are pathlib.Path so Windows backslashes/spaces are handled
    consistently. Validation is on-demand via :meth:`require`.
    """

    repo_dir: Path | None
    python_exe: Path | None
    model_dir: Path | None
    results_dir: Path | None
    timeout_sec: int
    ffmpeg_path: str            # 'ffmpeg' or absolute path string
    ffprobe_path: str           # 'ffprobe' or absolute path string
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def require(self) -> "VaceEnv":
        """Raise :class:`VaceConfigError` if any required path is missing.

        Required: repo_dir, python_exe, model_dir.
        Optional: results_dir (will create temp), ffmpeg/ffprobe (PATH lookup).
        """
        missing = []
        if self.repo_dir is None or not Path(self.repo_dir).is_dir():
            missing.append(
                f"VACE_REPO_DIR (got {self.repo_dir!r}) — clone "
                "https://github.com/ali-vilab/VACE there"
            )
        if self.python_exe is None or not Path(self.python_exe).is_file():
            missing.append(
                f"VACE_PYTHON_EXE (got {self.python_exe!r}) — point at "
                "<VACE_REPO_DIR>\\.venv\\Scripts\\python.exe"
            )
        if self.model_dir is None or not Path(self.model_dir).is_dir():
            missing.append(
                f"VACE_MODEL_DIR (got {self.model_dir!r}) — should contain "
                "Wan2.1-VACE weights"
            )
        if missing:
            raise VaceConfigError(
                "VACE backend not configured:\n  - " + "\n  - ".join(missing)
            )
        return self


def env_from_os(
    *,
    repo_dir: str | os.PathLike | None = None,
    python_exe: str | os.PathLike | None = None,
    model_dir: str | os.PathLike | None = None,
    results_dir: str | os.PathLike | None = None,
    timeout_sec: int | None = None,
    ffmpeg_path: str | os.PathLike | None = None,
    ffprobe_path: str | os.PathLike | None = None,
    environ: Mapping[str, str] | None = None,
) -> VaceEnv:
    """Build a VaceEnv. Explicit args override env vars; missing -> None.

    No filesystem checks here — call :meth:`VaceEnv.require` to validate.
    """
    env = environ if environ is not None else os.environ

    def _path(arg, key) -> Path | None:
        val = arg if arg is not None else env.get(key)
        return Path(val) if val else None

    timeout = timeout_sec
    if timeout is None:
        raw = env.get("VACE_TIMEOUT_SEC")
        if raw:
            try:
                timeout = int(raw)
            except ValueError as exc:
                raise VaceConfigError(
                    f"VACE_TIMEOUT_SEC must be int, got {raw!r}"
                ) from exc
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SEC

    ffmpeg_resolved = (
        str(ffmpeg_path) if ffmpeg_path is not None else env.get("FFMPEG_PATH") or "ffmpeg"
    )
    ffprobe_resolved = (
        str(ffprobe_path) if ffprobe_path is not None else env.get("FFPROBE_PATH") or "ffprobe"
    )

    return VaceEnv(
        repo_dir=_path(repo_dir, "VACE_REPO_DIR"),
        python_exe=_path(python_exe, "VACE_PYTHON_EXE"),
        model_dir=_path(model_dir, "VACE_MODEL_DIR"),
        results_dir=_path(results_dir, "VACE_RESULTS_DIR"),
        timeout_sec=timeout,
        ffmpeg_path=ffmpeg_resolved,
        ffprobe_path=ffprobe_resolved,
    )


def resolve_profile_with_overrides(
    profile_name: str | None,
    *,
    model_name_override: str | None = None,
    size_override: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> VaceProfile:
    """Resolve a profile, applying VACE_MODEL_NAME / VACE_SIZE / explicit overrides.

    Precedence: explicit kwarg > env var > profile default.
    """
    env = environ if environ is not None else os.environ

    name = (profile_name or env.get("VACE_PROFILE") or DEFAULT_PROFILE_NAME).strip()
    base = get_profile(name)

    model = model_name_override or env.get("VACE_MODEL_NAME") or base.model_name
    size = size_override or env.get("VACE_SIZE") or base.size

    if model == base.model_name and size == base.size:
        return base
    return replace(base, model_name=model, size=size)
