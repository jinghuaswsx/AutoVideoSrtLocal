"""对比 / 重 seed llm_prompt_configs 与代码出厂默认。

用法：
  python3 scripts/reseed_prompt_defaults.py                 # dry-run 列 SAME/DIFF/MISSING
  python3 scripts/reseed_prompt_defaults.py --apply --yes   # 全量覆盖（慎用）
  python3 scripts/reseed_prompt_defaults.py --apply --yes --slot base_translation --lang it

退出码：dry-run 存在 DIFF 或 MISSING → 1，否则 0。
         --apply 成功 → 0，--apply 无 --yes → 2。

Block1 说明：
  - 此工具专为"prompt 改动双写一致性"设计。改了 pipeline/languages/prompt_defaults.py
    后，必须同步更新 DB 里的 llm_prompt_configs 行，否则运行时仍读旧 prompt。
  - 建议上线前先 dry-run 确认 DIFF/MISSING，人工审查 unified-diff 后再 --apply --yes。
  - --slot / --lang 可缩小范围，只覆盖特定 slot 或语种。
"""
from __future__ import annotations
import argparse
import difflib
import sys

from appcore.llm_prompt_configs import get_one, upsert
from pipeline.languages.prompt_defaults import DEFAULTS


def diff_defaults(slot: str | None = None, lang: str | None = None) -> list[dict]:
    """对比 DEFAULTS 与 DB，返回每行的状态 dict（slot/lang/status/diff）。"""
    rows = []
    for (s, l), d in sorted(DEFAULTS.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        if slot and s != slot:
            continue
        if lang is not None and l != lang:
            continue
        db = get_one(s, l)
        if db is None:
            rows.append({"slot": s, "lang": l, "status": "MISSING", "diff": ""})
            continue
        if (db.get("content") or "") == d["content"]:
            rows.append({"slot": s, "lang": l, "status": "SAME", "diff": ""})
        else:
            diff = "\n".join(difflib.unified_diff(
                (db.get("content") or "").splitlines(),
                d["content"].splitlines(),
                fromfile="db",
                tofile="default",
                lineterm="",
                n=1,
            ))
            rows.append({"slot": s, "lang": l, "status": "DIFF", "diff": diff})
    return rows


def apply_defaults(slot: str | None = None, lang: str | None = None) -> int:
    """用出厂默认 upsert 覆盖 DB，返回实际写入行数。"""
    n = 0
    for (s, l), d in DEFAULTS.items():
        if slot and s != slot:
            continue
        if lang is not None and l != lang:
            continue
        upsert(
            s, l,
            provider=d["provider"],
            model=d["model"],
            content=d["content"],
            updated_by=None,
        )
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="对比 / 重 seed llm_prompt_configs 与代码出厂默认（Block1 工具）",
    )
    ap.add_argument("--apply", action="store_true", help="写入 DB（需配合 --yes）")
    ap.add_argument("--yes", action="store_true", help="确认执行 --apply")
    ap.add_argument("--slot", default=None, help="只处理指定 slot")
    ap.add_argument("--lang", default=None, help="只处理指定语言代码")
    args = ap.parse_args()

    if args.apply:
        if not args.yes:
            print("拒绝执行：--apply 必须配合 --yes 一起使用")
            return 2
        n = apply_defaults(args.slot, args.lang)
        print(f"已覆盖 {n} 行")
        return 0

    rows = diff_defaults(args.slot, args.lang)
    dirty = 0
    for r in rows:
        print(f"[{r['status']}] {r['slot']} / {r['lang']!r}")
        if r["status"] != "SAME":
            dirty += 1
            if r["diff"]:
                print(r["diff"])
    print(f"\n共 {len(rows)} 行，{dirty} 行需关注（DIFF 或 MISSING）")
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())
