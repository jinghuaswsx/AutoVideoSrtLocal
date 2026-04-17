from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from appcore import medias, tos_clients


DEFAULT_OLD_MEDIA_BUCKET = "video-save"
DEFAULT_REPORT_PATH = Path(config.OUTPUT_DIR) / "media_tos_bucket_migration_report.json"
DEFAULT_TEMP_DIR = Path(config.OUTPUT_DIR) / "media_bucket_migration_tmp"


def _normalize_bucket(value: str | None, fallback: str) -> str:
    bucket = (value or "").strip()
    if bucket:
        return bucket
    return fallback


def _head_size(head: Any) -> int | None:
    for attr in ("content_length", "object_size"):
        value = getattr(head, attr, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(results),
        "migrated": 0,
        "skipped": 0,
        "missing": 0,
        "failed": 0,
    }
    for row in results:
        status = str(row.get("status") or "").strip().lower()
        if status in summary and status != "total":
            summary[status] += 1
    return summary


def run_dry_run() -> dict[str, Any]:
    refs = medias.collect_media_object_references()
    results = [
        {
            "object_key": ref["object_key"],
            "status": "skipped",
            "reason": "dry-run",
            "sources": ref.get("sources") or [],
        }
        for ref in refs
    ]
    return {
        "mode": "dry-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "summary": summarize_results(results),
    }


def copy_object_between_buckets(
    object_key: str,
    temp_dir: Path,
    old_bucket: str,
    new_bucket: str,
) -> dict[str, Any]:
    key = (object_key or "").strip()
    if not key:
        return {"object_key": key, "status": "failed", "reason": "empty object key"}

    try:
        old_head = tos_clients.head_media_object(key, bucket=old_bucket)
    except Exception:
        old_head = None

    try:
        new_head = tos_clients.head_media_object(key, bucket=new_bucket)
    except Exception:
        new_head = None

    if new_head is not None:
        old_size = _head_size(old_head)
        new_size = _head_size(new_head)
        if old_head is None or old_size is None or new_size is None or old_size == new_size:
            return {"object_key": key, "status": "skipped", "reason": "already exists in target bucket"}

    if old_head is None:
        return {"object_key": key, "status": "missing", "reason": "source object not found"}

    local_path = temp_dir / Path(key)
    try:
        tos_clients.download_media_file(key, local_path, bucket=old_bucket)
        content_type = mimetypes.guess_type(key)[0]
        tos_clients.upload_media_object(
            key,
            local_path.read_bytes(),
            content_type=content_type,
            bucket=new_bucket,
        )
        uploaded_head = tos_clients.head_media_object(key, bucket=new_bucket)
    except Exception as exc:
        return {"object_key": key, "status": "failed", "reason": str(exc)}

    old_size = _head_size(old_head)
    uploaded_size = _head_size(uploaded_head)
    if old_size is not None and uploaded_size is not None and old_size != uploaded_size:
        return {"object_key": key, "status": "failed", "reason": "size mismatch after upload"}
    return {"object_key": key, "status": "migrated", "reason": ""}


def run_apply(old_bucket: str, new_bucket: str, temp_dir: Path) -> dict[str, Any]:
    refs = medias.collect_media_object_references()
    results: list[dict[str, Any]] = []
    for ref in refs:
        result = copy_object_between_buckets(
            str(ref["object_key"]),
            temp_dir,
            old_bucket,
            new_bucket,
        )
        result["sources"] = ref.get("sources") or []
        results.append(result)
    return {
        "mode": "apply",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "old_bucket": old_bucket,
        "new_bucket": new_bucket,
        "results": results,
        "summary": summarize_results(results),
    }


def cleanup_remote_objects(report: dict[str, Any], old_bucket: str) -> int:
    removed = 0
    for row in report.get("results") or []:
        if str(row.get("status") or "").strip().lower() != "migrated":
            continue
        tos_clients.delete_media_object(str(row.get("object_key") or "").strip(), bucket=old_bucket)
        removed += 1
    return removed


def _remove_tree(root: Path) -> int:
    if not root.exists():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
            removed += 1
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    if root.is_dir():
        try:
            root.rmdir()
        except OSError:
            pass
    return removed


def cleanup_local_paths(cache_root: Path, temp_root: Path) -> int:
    return _remove_tree(cache_root) + _remove_tree(temp_root)


def load_report(report_path: Path) -> dict[str, Any]:
    return json.loads(report_path.read_text(encoding="utf-8"))


def save_report(report: dict[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def configure_cors(new_bucket: str, origins: list[str]) -> dict[str, Any]:
    tos_clients.configure_media_bucket_cors(origins=origins, bucket=new_bucket)
    return {
        "mode": "configure-cors",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_bucket": new_bucket,
        "origins": list(origins),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate media objects to the dedicated TOS bucket.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", dest="dry_run")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--cleanup-remote", action="store_true", dest="cleanup_remote")
    modes.add_argument("--cleanup-local", action="store_true", dest="cleanup_local")
    modes.add_argument("--configure-cors", action="store_true", dest="configure_cors")
    parser.add_argument("--old-bucket", default=DEFAULT_OLD_MEDIA_BUCKET)
    parser.add_argument("--new-bucket", default=config.TOS_MEDIA_BUCKET)
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--temp-dir", default=str(DEFAULT_TEMP_DIR))
    parser.add_argument("--output-dir", default=config.OUTPUT_DIR)
    parser.add_argument(
        "--origin",
        dest="origins",
        action="append",
        default=None,
        help="允许的 CORS Origin，可重复（默认 http(s)://14.103.220.208:8888）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    report_path = Path(args.report_path)
    old_bucket = _normalize_bucket(args.old_bucket, DEFAULT_OLD_MEDIA_BUCKET)
    new_bucket = _normalize_bucket(args.new_bucket, config.TOS_MEDIA_BUCKET)
    temp_dir = Path(args.temp_dir)
    output_dir = Path(args.output_dir)

    if args.dry_run:
        report = run_dry_run()
        report["old_bucket"] = old_bucket
        report["new_bucket"] = new_bucket
        save_report(report, report_path)
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0

    if args.apply:
        if old_bucket == new_bucket:
            parser.error("old bucket and new bucket must be different")
        report = run_apply(old_bucket=old_bucket, new_bucket=new_bucket, temp_dir=temp_dir)
        save_report(report, report_path)
        print(json.dumps(report["summary"], ensure_ascii=False))
        return 0 if report["summary"]["failed"] == 0 else 1

    if args.configure_cors:
        origins = args.origins or [
            "http://14.103.220.208:8888",
            "https://14.103.220.208:8888",
        ]
        report = configure_cors(new_bucket=new_bucket, origins=origins)
        print(json.dumps({"new_bucket": report["new_bucket"], "origins": report["origins"]}, ensure_ascii=False))
        return 0

    if not report_path.exists():
        parser.error(f"report file not found: {report_path}")

    report = load_report(report_path)
    if args.cleanup_remote:
        removed = cleanup_remote_objects(report, old_bucket=old_bucket)
        print(json.dumps({"removed": removed}, ensure_ascii=False))
        return 0

    removed = cleanup_local_paths(
        cache_root=output_dir / "media_thumbs",
        temp_root=temp_dir,
    )
    print(json.dumps({"removed": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
