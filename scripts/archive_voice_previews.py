"""Backfill local voice preview audio archives and ASR transcripts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from appcore.voice_preview_archive import archive_missing_voice_previews  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive preview audio, duration, and ASR transcript for voice-library rows.",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(ROOT / "uploads" / "voice_preview_archive"),
        help="Directory used to store downloaded preview audio.",
    )
    parser.add_argument("--language", default=None, help="Optional language filter, e.g. en.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum missing rows to process.")
    parser.add_argument("--dry-run", action="store_true", help="Only count missing rows.")
    args = parser.parse_args(argv)

    def _on_progress(done: int, total: int, voice_id: str, ok: bool) -> None:
        if done == 1 or done == total or done % 10 == 0 or not ok:
            status = "ok" if ok else "failed"
            print(
                f"[voice-preview-archive] {done}/{total} {status} voice={voice_id}",
                file=sys.stderr,
                flush=True,
            )

    kwargs = {
        "archive_dir": args.archive_dir,
        "language": args.language,
        "limit": args.limit,
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        kwargs["on_progress"] = _on_progress
    result = archive_missing_voice_previews(**kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
