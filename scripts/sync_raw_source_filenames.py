from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import raw_source_filename_sync as syncer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync raw source filenames to the oldest English video filename per product.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply database and storage renames instead of only generating a dry-run report.",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path to save the full report as JSON.",
    )
    return parser


def _build_counts(report: dict, applied: list[dict], apply_errors: list[dict]) -> dict:
    return {
        "syncable": len(report.get("syncable") or []),
        "already_aligned": len(report.get("already_aligned") or []),
        "problems": len(report.get("problems") or []),
        "applied": len(applied),
        "apply_errors": len(apply_errors),
    }


def _write_json(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_summary(payload: dict) -> None:
    counts = payload["counts"]
    print(
        "raw-source-filename-sync: "
        f"syncable={counts['syncable']} "
        f"already_aligned={counts['already_aligned']} "
        f"problems={counts['problems']} "
        f"applied={counts['applied']} "
        f"apply_errors={counts['apply_errors']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = syncer.collect_sync_report()
    applied: list[dict] = []
    apply_errors: list[dict] = []

    if args.apply:
        for candidate in report.get("syncable") or []:
            try:
                applied.append(syncer.apply_sync(candidate))
            except Exception as exc:  # noqa: BLE001
                apply_errors.append(
                    {
                        "product_id": candidate.get("product_id"),
                        "raw_source_id": candidate.get("raw_source_id"),
                        "raw_source_name": candidate.get("raw_source_name"),
                        "target_filename": candidate.get("target_filename"),
                        "error": str(exc),
                    }
                )

    payload = {
        **report,
        "applied": applied,
        "apply_errors": apply_errors,
        "counts": _build_counts(report, applied, apply_errors),
    }
    if args.json_out:
        _write_json(args.json_out, payload)
    _print_summary(payload)
    return 1 if apply_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
