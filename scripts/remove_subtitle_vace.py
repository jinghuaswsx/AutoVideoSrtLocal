"""Standalone CLI for the VACE Windows subtitle backend.

Why standalone? The project's ``main.py`` is a Flask + APScheduler launcher;
embedding a CLI subcommand there triggers DB migrations and config validation
that's irrelevant to a one-shot video edit. This script imports only the
:mod:`appcore.vace_subtitle` package, which is import-safe (no torch / DB).

Examples (PowerShell):

    python scripts\\remove_subtitle_vace.py `
        --input "D:\\videos\\input_1080p.mp4" `
        --output "D:\\videos\\output_1080p_vace.mp4" `
        --bbox "0,780,1920,1025" `
        --mode "roi_1080" `
        --profile "rtx3060_safe"

    python scripts\\remove_subtitle_vace.py --dry-run --input ... --output ...
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow ``python scripts/remove_subtitle_vace.py`` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from appcore.vace_subtitle.bbox import parse_bbox_arg                       # noqa: E402
from appcore.vace_subtitle.config import (                                  # noqa: E402
    DEFAULT_PROMPT,
    DEFAULT_PROFILE_NAME,
    PROFILES,
    VaceConfigError,
)
from appcore.vace_subtitle.remover import VaceWindowsSubtitleRemover         # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="remove_subtitle_vace",
        description="Remove hardcoded subtitles via VACE (Windows + RTX 3060 friendly).",
    )
    p.add_argument("--input", required=True, help="Input video path.")
    p.add_argument("--output", required=True, help="Output video path.")
    p.add_argument("--bbox", default=None,
                   help="Subtitle bbox 'x1,y1,x2,y2' in original coords. "
                        "Omit for auto bottom-strip default.")
    p.add_argument("--mask", default=None,
                   help="(Reserved) per-frame mask path. Not used in v1.")
    p.add_argument("--mode", default="roi_1080",
                   choices=["roi_1080", "proxy_720", "native_vace"],
                   help="roi_1080 = preserve resolution & feather-blend (default).")
    p.add_argument("--profile", default=DEFAULT_PROFILE_NAME,
                   choices=sorted(PROFILES),
                   help=f"Tuning profile (default {DEFAULT_PROFILE_NAME}).")
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help="VACE inpainting prompt.")
    p.add_argument("--chunk-seconds", type=float, default=None,
                   dest="chunk_seconds")
    p.add_argument("--context-top-px", type=int, default=None, dest="context_top_px")
    p.add_argument("--context-bottom-px", type=int, default=None, dest="context_bottom_px")
    p.add_argument("--dilation-px", type=int, default=None, dest="dilation_px")
    p.add_argument("--feather-px", type=int, default=None, dest="feather_px")
    p.add_argument("--keep-workdir", action="store_true", dest="keep_workdir")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Plan everything, build commands, but do NOT launch VACE.")
    p.add_argument("--allow-native-vace", action="store_true",
                   dest="allow_native_vace",
                   help="Required for --mode native_vace.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    bbox = parse_bbox_arg(args.bbox)

    extra_args: dict = {}
    for key in (
        "chunk_seconds", "context_top_px", "context_bottom_px",
        "dilation_px", "feather_px",
    ):
        val = getattr(args, key)
        if val is not None:
            extra_args[key] = val
    if args.allow_native_vace:
        extra_args["allow_native_vace"] = True

    try:
        remover = VaceWindowsSubtitleRemover(profile=args.profile)
    except VaceConfigError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        out_path = remover.remove_subtitles(
            input_video=args.input,
            output_video=args.output,
            bbox=bbox,
            mask_path=args.mask,
            prompt=args.prompt,
            mode=args.mode,
            keep_workdir=args.keep_workdir,
            extra_args=extra_args,
            dry_run=args.dry_run,
        )
    except (VaceConfigError, FileNotFoundError, ValueError, NotImplementedError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                                       # noqa: BLE001
        print(f"[error] unexpected: {exc!r}", file=sys.stderr)
        return 3

    summary = {
        "output": str(out_path),
        "manifest": str(Path(out_path).with_suffix(Path(out_path).suffix + ".vace.json")),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
