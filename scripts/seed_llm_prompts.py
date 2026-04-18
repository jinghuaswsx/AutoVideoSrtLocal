"""独立 seed 脚本：把 pipeline/languages/prompt_defaults.py 里所有默认 prompt 写入 DB。

用法：
    python scripts/seed_llm_prompts.py          # 只补缺失的；已有不覆盖
    python scripts/seed_llm_prompts.py --force  # 强制覆盖所有（慎用）

幂等：对每条 (slot, lang)：
  - 未找到 → 写入默认值
  - 已找到 → 跳过（除非 --force）

运行前确保：
  1. MySQL 已启动且 2026_04_18_multi_translate_schema.sql 已 apply
  2. config.py 或 .env 指向正确的数据库
"""
from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的配置（默认只补缺失）",
    )
    args = parser.parse_args()

    # 延迟导入以便测试环境可用
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

    from appcore import llm_prompt_configs as dao
    from pipeline.languages.prompt_defaults import DEFAULTS

    inserted = 0
    skipped = 0
    overwritten = 0
    total = len(DEFAULTS)

    for (slot, lang), default in DEFAULTS.items():
        key_desc = f"{slot}/{lang or 'shared'}"
        existing = dao.get_one(slot, lang)
        if existing and not args.force:
            skipped += 1
            print(f"  [skip] {key_desc} — 已存在")
            continue
        dao.upsert(
            slot, lang,
            provider=default["provider"],
            model=default["model"],
            content=default["content"],
            updated_by=None,
        )
        if existing:
            overwritten += 1
            print(f"  [overwrite] {key_desc}")
        else:
            inserted += 1
            print(f"  [insert] {key_desc}")

    print()
    print(f"总条目：{total}")
    print(f"新插入：{inserted}")
    print(f"覆盖：{overwritten}")
    print(f"跳过（已存在）：{skipped}")

    if inserted or overwritten:
        print()
        print("提示：可在 /admin/prompts 管理后台调整。")


if __name__ == "__main__":
    main()
