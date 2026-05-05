"""High-level orchestrator: ``VaceWindowsSubtitleRemover``.

This module ties together :mod:`bbox`, :mod:`chunking`, :mod:`ffmpeg_io`,
:mod:`vace_subprocess`, :mod:`composite`, and :mod:`manifest` into one
public entry point.

Default mode is ``roi_1080``: input resolution is preserved, only the
subtitle region is cropped, sent to VACE, and feather-blended back. This
keeps non-subtitle pixels byte-identical to the source.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .bbox import (
    DEFAULT_CONTEXT_BOTTOM_PX,
    DEFAULT_CONTEXT_TOP_PX,
    DEFAULT_DILATION_PX,
    DEFAULT_FEATHER_PX,
    Bbox,
    compute_crop_plan,
    compute_scale_plan,
    normalize_bbox,
)
from .chunking import plan_chunks
from .config import (
    DEFAULT_PROMPT,
    VaceConfigError,
    VaceEnv,
    VaceProfile,
    env_from_os,
    fallback_profile,
    resolve_profile_with_overrides,
)
from .ffmpeg_io import (
    FFmpegError,
    MediaInfo,
    concat_chunks,
    crop_chunk,
    cut_chunk,
    mux_audio_from_source,
    probe_media,
)
from .manifest import ChunkRecord, Manifest, manifest_path_for
from .vace_subprocess import (
    VaceInvocation,
    VaceSubprocessError,
    build_command,
    run_invocation,
)

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VaceWindowsSubtitleRemover:
    """Subtitle remover using VACE as a subprocess (Windows-friendly).

    All paths are pathlib.Path. No global state, no GPU/torch import at
    construction time. Validation of VACE_REPO_DIR / VACE_PYTHON_EXE etc.
    is deferred to :meth:`remove_subtitles` so unit tests can construct
    instances without VACE installed.
    """

    def __init__(
        self,
        *,
        vace_repo_dir: str | os.PathLike | None = None,
        vace_python_exe: str | os.PathLike | None = None,
        model_dir: str | os.PathLike | None = None,
        model_name: str | None = None,
        size: str | None = None,
        profile: str | None = None,
        results_dir: str | os.PathLike | None = None,
        timeout_sec: int | None = None,
        ffmpeg_path: str | os.PathLike | None = None,
        ffprobe_path: str | os.PathLike | None = None,
    ):
        self._env: VaceEnv = env_from_os(
            repo_dir=vace_repo_dir,
            python_exe=vace_python_exe,
            model_dir=model_dir,
            results_dir=results_dir,
            timeout_sec=timeout_sec,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        self._profile: VaceProfile = resolve_profile_with_overrides(
            profile,
            model_name_override=model_name,
            size_override=size,
        )

    # -----------------------------------------------------------------
    # public surface
    # -----------------------------------------------------------------

    def remove_subtitles(
        self,
        input_video: str | os.PathLike,
        output_video: str | os.PathLike,
        *,
        bbox: Bbox | None = None,
        mask_path: str | os.PathLike | None = None,
        prompt: str = DEFAULT_PROMPT,
        mode: str = "roi_1080",
        profile: str | None = None,
        keep_workdir: bool = False,
        extra_args: Mapping[str, Any] | None = None,
        dry_run: bool = False,
    ) -> Path:
        """Run subtitle removal end-to-end. Returns the output video path.

        Args:
            input_video: source video (any res; we preserve it).
            output_video: destination MP4. Sidecar JSON is written next to it.
            bbox: ``(x1,y1,x2,y2)`` in original-video coords. None = bottom auto.
            mask_path: reserved for OCR-driven dynamic mask. Not used in v1.
            prompt: VACE inpainting prompt.
            mode: ``"roi_1080"`` (default) | ``"proxy_720"`` | ``"native_vace"``.
            profile: profile-name override.
            keep_workdir: keep intermediate chunk files for debugging.
            extra_args: per-chunk knobs (context_top_px, context_bottom_px,
                dilation_px, feather_px, chunk_seconds, allow_native_vace).
            dry_run: skip subprocess launches; still produce manifest.
        """
        in_path = Path(input_video).resolve()
        out_path = Path(output_video).resolve()
        if not in_path.is_file():
            raise FileNotFoundError(f"input video not found: {in_path}")

        opts = dict(extra_args or {})
        eff_profile = (
            resolve_profile_with_overrides(profile)
            if profile is not None
            else self._profile
        )

        if mode == "native_vace" and not opts.get("allow_native_vace"):
            raise ValueError(
                "mode='native_vace' is disabled on RTX 3060. Pass "
                "extra_args={'allow_native_vace': True} to opt in."
            )
        if mode == "proxy_720":
            raise NotImplementedError(
                "mode='proxy_720' is reserved for v2; use the default 'roi_1080'."
            )
        if mode != "roi_1080":
            raise ValueError(f"unknown mode {mode!r}; expected 'roi_1080'")

        if mask_path is not None:
            log.warning(
                "vace_subtitle: mask_path support is reserved; v1 only uses bbox."
            )

        manifest = Manifest(
            input_video=str(in_path),
            output_video=str(out_path),
            mode=mode,
            profile=eff_profile.name,
            model_name=eff_profile.model_name,
            size=eff_profile.size,
            frame_num=eff_profile.frame_num,
            sample_steps=eff_profile.sample_steps,
            offload_model=eff_profile.offload_model,
            t5_cpu=eff_profile.t5_cpu,
            prompt=prompt,
            vace_repo_dir=str(self._env.repo_dir or ""),
            vace_python_exe=str(self._env.python_exe or ""),
            model_dir=str(self._env.model_dir or ""),
        )

        try:
            if not dry_run:
                # Validate VACE env only when really running (lets dry-run work
                # on a machine without VACE installed).
                self._env.require()
            return self._run_roi_1080(
                in_path=in_path,
                out_path=out_path,
                bbox=bbox,
                prompt=prompt,
                profile=eff_profile,
                opts=opts,
                manifest=manifest,
                keep_workdir=keep_workdir,
                dry_run=dry_run,
            )
        except (VaceConfigError, FFmpegError, VaceSubprocessError, ValueError) as exc:
            manifest.status = "failed"
            manifest.errors.append(repr(exc))
            manifest.finished_at = _utcnow_iso()
            try:
                manifest.write(manifest_path_for(out_path))
            except OSError:
                log.warning("vace_subtitle: failed to persist failure manifest")
            raise

    # -----------------------------------------------------------------
    # roi_1080 implementation
    # -----------------------------------------------------------------

    def _run_roi_1080(
        self,
        *,
        in_path: Path,
        out_path: Path,
        bbox: Bbox | None,
        prompt: str,
        profile: VaceProfile,
        opts: Mapping[str, Any],
        manifest: Manifest,
        keep_workdir: bool,
        dry_run: bool,
    ) -> Path:
        # 1. probe
        info: MediaInfo = probe_media(in_path, ffprobe_path=self._env.ffprobe_path)
        if info.width <= 0 or info.height <= 0:
            raise ValueError(f"ffprobe produced empty geometry for {in_path}")
        manifest.input_width = info.width
        manifest.input_height = info.height
        manifest.input_fps = info.fps
        manifest.input_duration = info.duration

        # 2. bbox + crop plan
        eff_bbox = normalize_bbox(bbox, info.width, info.height)
        crop = compute_crop_plan(
            eff_bbox,
            info.width,
            info.height,
            context_top_px=int(opts.get("context_top_px", DEFAULT_CONTEXT_TOP_PX)),
            context_bottom_px=int(opts.get("context_bottom_px", DEFAULT_CONTEXT_BOTTOM_PX)),
        )
        scale = compute_scale_plan(
            crop,
            max_long_edge=profile.max_long_edge,
            max_short_edge=profile.max_short_edge,
        )
        manifest.bbox_original = eff_bbox
        manifest.crop_bbox_original = crop.crop_bbox

        # 3. chunk plan
        chunks = plan_chunks(
            duration_seconds=info.duration,
            fps=info.fps,
            chunk_seconds=float(opts.get("chunk_seconds", profile.chunk_seconds)),
            frame_num=profile.frame_num,
        )
        if not chunks:
            raise ValueError(f"no chunks planned for duration={info.duration}")

        workdir = self._make_workdir(in_path)
        log.info(
            "vace_subtitle: ROI plan crop=%s scale_target=%dx%d chunks=%d workdir=%s",
            crop.crop_bbox, scale.target_width, scale.target_height, len(chunks), workdir,
        )

        try:
            current_profile = profile
            for cp in chunks:
                rec = ChunkRecord(
                    index=cp.index,
                    start_seconds=cp.start_seconds,
                    duration_seconds=cp.duration_seconds,
                )
                manifest.chunks.append(rec)
                self._process_chunk(
                    in_path=in_path,
                    workdir=workdir,
                    chunk=cp,
                    crop=crop,
                    scale=scale,
                    bbox=eff_bbox,
                    profile=current_profile,
                    opts=opts,
                    prompt=prompt,
                    record=rec,
                    dry_run=dry_run,
                )

            if dry_run:
                manifest.status = "dry-run"
                manifest.finished_at = _utcnow_iso()
                manifest.write(manifest_path_for(out_path))
                return out_path

            # 4. concat composited chunks
            composited_paths = [Path(c.composited_chunk_path) for c in manifest.chunks if c.composited_chunk_path]
            concat_dst = workdir / "composited_concat.mp4"
            list_file = workdir / "concat_list.txt"
            concat_chunks(
                chunk_paths=composited_paths,
                dst=concat_dst,
                list_file_path=list_file,
                ffmpeg_path=self._env.ffmpeg_path,
            )

            # 5. mux audio from original
            mux_audio_from_source(
                video_src=concat_dst,
                audio_src=in_path,
                dst=out_path,
                ffmpeg_path=self._env.ffmpeg_path,
            )
            manifest.final_mux_command = ["mux_audio_from_source"]
            manifest.status = "done"
            manifest.finished_at = _utcnow_iso()
            manifest.write(manifest_path_for(out_path))
            return out_path
        finally:
            if not keep_workdir and not dry_run:
                shutil.rmtree(workdir, ignore_errors=True)

    def _process_chunk(
        self,
        *,
        in_path: Path,
        workdir: Path,
        chunk,
        crop,
        scale,
        bbox: Bbox,
        profile: VaceProfile,
        opts: Mapping[str, Any],
        prompt: str,
        record: ChunkRecord,
        dry_run: bool,
    ) -> None:
        chunk_dir = workdir / f"chunk_{chunk.index:04d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        original_chunk = chunk_dir / "original.mp4"
        crop_chunk_path = chunk_dir / "roi_crop.mp4"
        vace_save_dir = chunk_dir / "vace_out"
        vace_pre_dir = chunk_dir / "vace_pre"
        vace_save_file = vace_save_dir / "result.mp4"
        composited = chunk_dir / "composited.mp4"

        record.original_chunk_path = str(original_chunk)
        record.crop_chunk_path = str(crop_chunk_path)
        record.vace_input_path = str(crop_chunk_path)
        record.vace_output_path = str(vace_save_file)
        record.composited_chunk_path = str(composited)

        if dry_run:
            # Even when VACE env isn't fully configured, print a representative
            # command using placeholders so operators can copy-paste-verify.
            placeholder_env = replace(
                self._env,
                repo_dir=self._env.repo_dir or Path("<set VACE_REPO_DIR>"),
                python_exe=self._env.python_exe or Path("<set VACE_PYTHON_EXE>"),
                model_dir=self._env.model_dir or Path("<set VACE_MODEL_DIR>"),
            )
            inv = build_command(
                placeholder_env, profile,
                input_video=crop_chunk_path,
                bbox_in_vace=scale.bbox_in_vace,
                prompt=prompt,
                save_dir=vace_save_dir,
                save_file=vace_save_file,
                pre_save_dir=vace_pre_dir,
            )
            record.command = list(inv.command)
            record.status = "dry-run"
            return

        record.status = "running"
        # 3.1 cut original 1080P chunk
        cut_chunk(
            src=in_path,
            dst=original_chunk,
            start_seconds=chunk.start_seconds,
            duration_seconds=chunk.duration_seconds,
            ffmpeg_path=self._env.ffmpeg_path,
        )
        # 3.2 cut + crop + scale + pad ROI chunk
        cx1, cy1, cx2, cy2 = crop.crop_bbox
        crop_chunk(
            src=original_chunk,
            dst=crop_chunk_path,
            crop_x=cx1, crop_y=cy1, crop_w=cx2 - cx1, crop_h=cy2 - cy1,
            target_w=scale.target_width, target_h=scale.target_height,
            pad_left=scale.pad_left, pad_top=scale.pad_top,
            ffmpeg_path=self._env.ffmpeg_path,
        )

        # 3.3 invoke VACE — with one-shot OOM fallback per chunk.
        active_profile = profile
        attempts = 0
        last_err: VaceSubprocessError | None = None
        while True:
            inv = build_command(
                self._env, active_profile,
                input_video=crop_chunk_path,
                bbox_in_vace=scale.bbox_in_vace,
                prompt=prompt,
                save_dir=vace_save_dir,
                save_file=vace_save_file,
                pre_save_dir=vace_pre_dir,
            )
            record.command = list(inv.command)
            try:
                result = run_invocation(inv, timeout_sec=self._env.timeout_sec)
                record.returncode = result.returncode
                record.elapsed_seconds = result.elapsed_seconds
                if result.output_video and Path(result.output_video) != vace_save_file:
                    record.vace_output_path = str(result.output_video)
                break
            except VaceSubprocessError as exc:
                attempts += 1
                last_err = exc
                if exc.oom and attempts == 1:
                    next_p = fallback_profile(active_profile)
                    if next_p is not None:
                        log.warning(
                            "vace_subtitle: chunk %d OOM; fallback %s -> %s",
                            chunk.index, active_profile.name, next_p.name,
                        )
                        active_profile = next_p
                        continue
                record.status = "failed"
                record.error = repr(exc)
                raise

        # 3.4 composite back into original 1080P chunk
        from .composite import composite_chunk
        composite_chunk(
            original_chunk=original_chunk,
            vace_chunk=Path(record.vace_output_path),
            output_path=composited,
            crop=crop,
            scale=scale,
            bbox_in_crop=crop.bbox_in_crop,
            dilation_px=int(opts.get("dilation_px", DEFAULT_DILATION_PX)),
            feather_px=int(opts.get("feather_px", DEFAULT_FEATHER_PX)),
        )
        record.status = "done"

    # -----------------------------------------------------------------
    # helpers
    # -----------------------------------------------------------------

    def _make_workdir(self, in_path: Path) -> Path:
        base = self._env.results_dir
        prefix = f"vace_{in_path.stem}_"
        if base is not None:
            base.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(prefix=prefix, dir=str(base)))
        return Path(tempfile.mkdtemp(prefix=prefix))
