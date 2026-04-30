from __future__ import annotations

import argparse
import json
from typing import Any

from appcore import push_quality_checks


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="重新触发已经跑过的推送前质量检查，让旧结果按当前中文提示词重算。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多扫描多少条历史质检记录；默认扫描全部历史记录。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将要重跑的 item_id，不实际调用大模型。",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="临时指定本次重跑使用的 llm provider，例如 gemini_aistudio；默认使用代码内配置。",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="临时指定本次重跑使用的模型；默认使用代码内配置。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="按 JSON 输出汇总，方便脚本或日志采集。",
    )
    return parser


def _print_human(summary: dict[str, Any]) -> None:
    print("推送前质量检查重跑完成")
    print(f"扫描历史记录：{summary.get('scanned', 0)}")
    print(f"实际重跑：{summary.get('evaluated', 0)}")
    print(f"跳过重复 item：{summary.get('skipped_duplicate', 0)}")
    print(f"错误数：{summary.get('errors', 0)}")
    if summary.get("dry_run"):
        print("当前为 dry-run，未调用大模型。")
    if summary.get("provider") or summary.get("model"):
        print(f"本次 provider/model：{summary.get('provider') or '-'} / {summary.get('model') or '-'}")
    item_ids = summary.get("item_ids") or []
    if item_ids:
        print("涉及 item_id：" + ", ".join(str(item_id) for item_id in item_ids[:50]))
        if len(item_ids) > 50:
            print(f"... 另有 {len(item_ids) - 50} 个 item_id")
    error_items = summary.get("error_items") or []
    if error_items:
        print("错误明细：")
        for item in error_items[:20]:
            print(f"- item_id={item.get('item_id')}: {item.get('error')}")


def main() -> int:
    args = _build_parser().parse_args()
    if args.provider:
        push_quality_checks.PROVIDER = args.provider.strip()
    if args.model:
        push_quality_checks.MODEL = args.model.strip()
    summary = push_quality_checks.rerun_existing_checked_items(
        limit=args.limit,
        dry_run=args.dry_run,
    )
    summary["provider"] = push_quality_checks.PROVIDER
    summary["model"] = push_quality_checks.MODEL
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human(summary)
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
