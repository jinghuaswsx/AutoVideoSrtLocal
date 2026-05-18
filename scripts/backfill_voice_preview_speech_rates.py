"""Backfill cached speech-rate metadata for ElevenLabs preview audio."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.voice_library_sync import backfill_missing_preview_speech_rates  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill voice_preview_speech_rate from stored preview_url rows.",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(ROOT / "uploads" / "voice_preview_cache"),
        help="Directory used to cache downloaded preview audio.",
    )
    parser.add_argument("--language", default=None, help="Optional language filter, e.g. en.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum missing rows to process.")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent preview download/transcribe workers. Default: 1.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only count missing rows.")
    args = parser.parse_args(argv)

    def _on_progress(done: int, total: int, voice_id: str, ok: bool) -> None:
        if done == 1 or done == total or done % 10 == 0 or not ok:
            status = "ok" if ok else "failed"
            print(
                f"[preview-rate] {done}/{total} {status} voice={voice_id}",
                file=sys.stderr,
                flush=True,
            )

    kwargs = {
        "language": args.language,
        "limit": args.limit,
        "dry_run": bool(args.dry_run),
        "workers": args.workers,
    }
    if not args.dry_run:
        kwargs["on_progress"] = _on_progress
    result = backfill_missing_preview_speech_rates(args.cache_dir, **kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
