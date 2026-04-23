"""Volcengine VOD subtitle-erasure smoke helper.

This script accepts explicit HTTP(S) source URLs only. TOS object-key signing
was removed from the helper as part of the local-storage migration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore.vod_erase_provider import (  # noqa: E402
    VodEraseError,
    get_execution,
    get_play_info,
    query_upload_task_info,
    start_erase_execution,
    upload_media_by_url,
    wait_for_execution,
    wait_for_upload,
)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _source_url(raw: str) -> str:
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    raise VodEraseError("source must be an HTTP(S) URL")


def cmd_upload(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[upload] source_url={url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"[upload] JobId={job_id}")


def cmd_upload_status(args: list[str]) -> None:
    print(_dump(query_upload_task_info(args[0])))


def cmd_upload_wait(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[upload] source_url={url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"[upload] JobId={job_id}, waiting...")
    print(f"[upload] Vid={wait_for_upload(job_id)}")


def cmd_erase(args: list[str]) -> None:
    print(f"[erase] RunId={start_erase_execution(vid=args[0])}")


def cmd_status(args: list[str]) -> None:
    print(_dump(get_execution(args[0])))


def cmd_wait(args: list[str]) -> None:
    result = wait_for_execution(args[0], on_progress=lambda row: print(f"  status={row.get('Status')}"))
    print(_dump(result))


def cmd_play(args: list[str]) -> None:
    print(_dump(get_play_info(args[0])))


def cmd_e2e(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[1/4] upload_media_by_url: {url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"      JobId={job_id}")

    print("[2/4] waiting for upload...")
    vid = wait_for_upload(job_id)
    print(f"      Vid={vid}")

    print("[3/4] start_erase_execution...")
    run_id = start_erase_execution(vid=vid)
    print(f"      RunId={run_id}")

    print("[4/4] waiting for erase...")
    result = wait_for_execution(run_id, on_progress=lambda row: print(f"      status={row.get('Status')}"))
    output = (((result.get("Output") or {}).get("Task") or {}).get("Erase") or {})
    print(_dump(output))


COMMANDS = {
    "upload": cmd_upload,
    "upload-status": cmd_upload_status,
    "upload-wait": cmd_upload_wait,
    "erase": cmd_erase,
    "status": cmd_status,
    "wait": cmd_wait,
    "play": cmd_play,
    "e2e": cmd_e2e,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Commands: " + ", ".join(sorted(COMMANDS)))
        sys.exit(1)
    cmd = sys.argv[1]
    try:
        COMMANDS[cmd](sys.argv[2:])
    except IndexError:
        print(f"missing argument for command: {cmd}")
        sys.exit(2)
    except VodEraseError as exc:
        print(f"[error] {exc}")
        sys.exit(3)


if __name__ == "__main__":
    main()
