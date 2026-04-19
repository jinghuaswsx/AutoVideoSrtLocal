"""火山 VOD 字幕擦除冒烟脚本。

用于在不改前端 UI 的前提下，手动验证 VOD OpenAPI 能否打通：
  - UploadMediaByUrl / QueryUploadTaskInfo（拉外链上传到空间）
  - StartExecution（字幕擦除）
  - GetExecution（轮询任务）
  - GetPlayInfo（取播放 URL）

前置：
  - worktree 根目录的 .env 里配置了 VOD_SPACE_NAME、VOD_REGION、VOD_ACCESS_KEY/SK（或复用 TOS_*）
  - 源视频已有公网可访问的直链（可通过 generate_signed_download_url 拿 TOS 签名 URL）

用法：
  python scripts/vod_erase_smoke.py upload <source_url>
  python scripts/vod_erase_smoke.py upload-status <job_id>
  python scripts/vod_erase_smoke.py upload-wait <source_url>
  python scripts/vod_erase_smoke.py erase <vid>
  python scripts/vod_erase_smoke.py status <run_id>
  python scripts/vod_erase_smoke.py wait <run_id>
  python scripts/vod_erase_smoke.py play <vid>
  python scripts/vod_erase_smoke.py tos-url <tos_object_key>
  python scripts/vod_erase_smoke.py e2e <source_url_or_tos_key>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本作为独立入口运行时能找到包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import vod_erase_provider
from appcore.vod_erase_provider import (
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
    """允许直接传 TOS object key，内部生成签名 URL。"""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    from appcore import tos_clients

    return tos_clients.generate_signed_download_url(raw, expires=86400)


def cmd_upload(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[upload] source_url={url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"[upload] JobId={job_id}")


def cmd_upload_status(args: list[str]) -> None:
    info = query_upload_task_info(args[0])
    print(_dump(info))


def cmd_upload_wait(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[upload] source_url={url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"[upload] JobId={job_id}, waiting...")
    vid = wait_for_upload(job_id)
    print(f"[upload] Vid={vid}")


def cmd_erase(args: list[str]) -> None:
    vid = args[0]
    run_id = start_erase_execution(vid=vid)
    print(f"[erase] RunId={run_id}")


def cmd_status(args: list[str]) -> None:
    result = get_execution(args[0])
    print(_dump(result))


def cmd_wait(args: list[str]) -> None:
    result = wait_for_execution(args[0], on_progress=lambda r: print(f"  status={r.get('Status')}"))
    print(_dump(result))


def cmd_play(args: list[str]) -> None:
    result = get_play_info(args[0])
    print(_dump(result))


def cmd_tos_url(args: list[str]) -> None:
    from appcore import tos_clients

    url = tos_clients.generate_signed_download_url(args[0], expires=86400)
    print(url)


def cmd_e2e(args: list[str]) -> None:
    url = _source_url(args[0])
    print(f"[1/4] upload_media_by_url: {url[:120]}...")
    job_id = upload_media_by_url(source_url=url)
    print(f"       JobId={job_id}")

    print(f"[2/4] waiting for upload...")
    vid = wait_for_upload(job_id)
    print(f"       Vid={vid}")

    print(f"[3/4] start_erase_execution (Auto/Subtitle)...")
    run_id = start_erase_execution(vid=vid)
    print(f"       RunId={run_id}")

    print(f"[4/4] waiting for erase...")
    result = wait_for_execution(run_id, on_progress=lambda r: print(f"       status={r.get('Status')}"))

    output = (((result.get("Output") or {}).get("Task") or {}).get("Erase") or {})
    file_info = (output.get("File") or {})
    new_vid = file_info.get("Vid")
    file_name = file_info.get("FileName")
    print(f"\n[done] erase result:")
    print(_dump(output))

    if new_vid:
        print(f"\n[play] fetching GetPlayInfo for Vid={new_vid}...")
        try:
            play = get_play_info(new_vid)
            print(_dump(play))
        except VodEraseError as exc:
            print(f"[play] failed (加速域名未配置？): {exc}")
    elif file_name:
        print(f"\n[play] NewVid 未返回，仅有 FileName={file_name}")


COMMANDS = {
    "upload": cmd_upload,
    "upload-status": cmd_upload_status,
    "upload-wait": cmd_upload_wait,
    "erase": cmd_erase,
    "status": cmd_status,
    "wait": cmd_wait,
    "play": cmd_play,
    "tos-url": cmd_tos_url,
    "e2e": cmd_e2e,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    try:
        COMMANDS[cmd](args)
    except IndexError:
        print(f"missing argument for command: {cmd}")
        print(__doc__)
        sys.exit(2)
    except VodEraseError as exc:
        print(f"[error] {exc}")
        sys.exit(3)


if __name__ == "__main__":
    main()
