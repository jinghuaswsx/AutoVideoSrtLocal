"""Run the VACE official pipeline as a subprocess.

We invoke ``python vace/vace_pipeline.py`` from ``VACE_REPO_DIR`` using
``VACE_PYTHON_EXE`` (the dedicated venv). This keeps VACE's heavy deps
(torch + diffusers + wan) out of the main project's interpreter.

Important quirks:
- VACE's CLI evolves; argument names may shift between commits. We surface
  ``--help`` parsing as a defensive sanity check (see :func:`probe_help`).
- OOM on RTX 3060 manifests as ``RuntimeError: CUDA out of memory`` in
  stderr; we string-match a small set of well-known patterns.
- We pass paths as strings via list[str]; never use shell=True.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .config import VaceEnv, VaceProfile

log = logging.getLogger(__name__)

OOM_PATTERNS = (
    re.compile(r"CUDA out of memory", re.IGNORECASE),
    re.compile(r"out of memory", re.IGNORECASE),
    re.compile(r"CUBLAS_STATUS_ALLOC_FAILED", re.IGNORECASE),
    re.compile(r"CUDNN_STATUS_NOT_SUPPORTED", re.IGNORECASE),
    re.compile(r"cudaErrorMemoryAllocation", re.IGNORECASE),
)
_STDERR_TAIL = 2000


class VaceSubprocessError(RuntimeError):
    """Raised when the VACE pipeline subprocess fails."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr_tail: str | None = None,
        cmd_summary: str | None = None,
        oom: bool = False,
    ):
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        self.cmd_summary = cmd_summary
        self.oom = oom


@dataclass(frozen=True)
class VaceInvocation:
    """The precise list[str] command we're about to run, plus context.

    ``command`` is exposed for unit-testing without actually launching the
    subprocess: tests assert on its shape, then mock subprocess.run.
    """

    command: list[str]
    cwd: Path
    save_dir: Path
    save_file: Path
    pre_save_dir: Path


def is_oom(stderr_text: str) -> bool:
    """Return True iff ``stderr_text`` matches any well-known OOM pattern."""
    if not stderr_text:
        return False
    return any(pat.search(stderr_text) for pat in OOM_PATTERNS)


def build_command(
    env: VaceEnv,
    profile: VaceProfile,
    *,
    input_video: Path,
    bbox_in_vace: tuple[int, int, int, int],
    prompt: str,
    save_dir: Path,
    save_file: Path,
    pre_save_dir: Path,
) -> VaceInvocation:
    """Construct the VACE pipeline command. No subprocess launched here."""
    repo = env.repo_dir
    py = env.python_exe
    if repo is None or py is None or env.model_dir is None:
        # Defensive: caller should have run env.require() already.
        raise VaceSubprocessError(
            "VACE env not validated; call VaceEnv.require() before build_command()"
        )

    pipeline_script = repo / "vace" / "vace_pipeline.py"
    bbox_str = ",".join(str(int(v)) for v in bbox_in_vace)

    cmd = [
        str(py), str(pipeline_script),
        "--base", "wan",
        "--task", "inpainting",
        "--mode", "bbox",
        "--bbox", bbox_str,
        "--video", str(input_video),
        "--prompt", prompt,
        "--model_name", profile.model_name,
        "--ckpt_dir", str(env.model_dir),
        "--size", profile.size,
        "--frame_num", str(profile.frame_num),
        "--sample_steps", str(profile.sample_steps),
        "--save_dir", str(save_dir),
        "--save_file", str(save_file),
        "--pre_save_dir", str(pre_save_dir),
    ]
    if profile.offload_model:
        cmd += ["--offload_model", "True"]
    if profile.t5_cpu:
        cmd += ["--t5_cpu"]

    return VaceInvocation(
        command=cmd,
        cwd=repo,
        save_dir=save_dir,
        save_file=save_file,
        pre_save_dir=pre_save_dir,
    )


@dataclass
class VaceRunResult:
    """Outcome of a single VACE pipeline subprocess invocation."""

    returncode: int
    elapsed_seconds: float
    stderr_tail: str
    output_video: Path | None = None
    fields: dict = field(default_factory=dict)


def run_invocation(
    inv: VaceInvocation,
    *,
    timeout_sec: int,
    runner=subprocess.run,
) -> VaceRunResult:
    """Execute ``inv``, returning :class:`VaceRunResult` on success.

    Raises :class:`VaceSubprocessError` (with ``oom=True`` for OOM patterns)
    on non-zero return codes. ``runner`` is injectable for unit tests.

    Note: stdout is NOT captured to avoid log explosions for long renders.
    """
    cmd_summary = " ".join(inv.command[:5]) + " ..."
    log.info("vace_subtitle: launching %s (cwd=%s)", cmd_summary, inv.cwd)
    started = time.monotonic()
    try:
        proc = runner(
            inv.command,
            cwd=str(inv.cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VaceSubprocessError(
            f"VACE pipeline timed out after {timeout_sec}s",
            cmd_summary=cmd_summary,
        ) from exc
    elapsed = time.monotonic() - started

    stderr_tail = (getattr(proc, "stderr", "") or "")[-_STDERR_TAIL:]
    rc = getattr(proc, "returncode", None) or 0

    if rc != 0:
        oom = is_oom(stderr_tail)
        raise VaceSubprocessError(
            f"VACE pipeline failed (rc={rc}, oom={oom}): {cmd_summary}",
            returncode=rc,
            stderr_tail=stderr_tail,
            cmd_summary=cmd_summary,
            oom=oom,
        )

    return VaceRunResult(
        returncode=rc,
        elapsed_seconds=round(elapsed, 2),
        stderr_tail=stderr_tail,
        output_video=_pick_output_video(inv),
    )


def _pick_output_video(inv: VaceInvocation) -> Path | None:
    """Locate the result mp4. Prefer save_file; fall back to newest in save_dir."""
    if inv.save_file.is_file():
        return inv.save_file
    if not inv.save_dir.is_dir():
        return None
    mp4s = sorted(
        (p for p in inv.save_dir.iterdir() if p.suffix.lower() == ".mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return mp4s[0] if mp4s else None


def probe_help(
    env: VaceEnv,
    *,
    timeout_sec: int = 30,
    runner=subprocess.run,
) -> str:
    """Run ``python vace/vace_pipeline.py --help`` and return its stdout.

    Use this defensively when you suspect the upstream CLI changed; the
    main runtime still launches the canonical command shape, but the
    --help output is logged so operators can diff arguments.
    """
    if env.repo_dir is None or env.python_exe is None:
        raise VaceSubprocessError("VACE_REPO_DIR / VACE_PYTHON_EXE missing")
    pipeline_script = env.repo_dir / "vace" / "vace_pipeline.py"
    cmd = [str(env.python_exe), str(pipeline_script), "--help"]
    try:
        proc = runner(
            cmd,
            cwd=str(env.repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VaceSubprocessError(f"--help spawn failed: {exc}") from exc
    return (getattr(proc, "stdout", "") or "")
