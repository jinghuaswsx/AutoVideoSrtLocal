from __future__ import annotations

import argparse
import json
import sys

from appcore import feishu_alerts


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a Feishu alert test message.")
    parser.add_argument(
        "--message",
        default="AutoVideoSrt 飞书告警测试：scheduled_task_runs 失败通知已接入。",
        help="Text message to send.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = feishu_alerts.send_test_alert(args.message)
    except feishu_alerts.FeishuAlertError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
