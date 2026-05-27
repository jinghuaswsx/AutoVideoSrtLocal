# -*- coding: utf-8 -*-
"""
Meta热帖分析 (美国AI分析 + 欧洲AI分析) Antigravity 评估数据回填与辅助工具。
本文件作为 SKILL 执行的落脚点，完美复用了系统的底层存储方法。
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

# 确保 utf-8 编码输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 将项目根目录添加到系统路径以支持直接运行
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from appcore.meta_hot_posts import store


def show_post(post_id: int) -> None:
    """提取指定 Meta 热帖的元数据，方便 Antigravity 扮演推理引擎进行分析"""
    row = store.get_hot_post_ai_analysis_row(post_id)
    if not row:
        print(json.dumps({"error": f"Post {post_id} not found"}, ensure_ascii=False))
        sys.exit(1)

    # 提取评估需要用到的关键属性
    metadata = {
        "id": row.get("id"),
        "product_url": row.get("product_url"),
        "product_title": row.get("product_title") or row.get("product_title_zh"),
        "category": row.get("category_l1"),
        "post_url": row.get("post_url"),
        "engagement": {
            "likes": row.get("latest_likes") or row.get("likes") or 0,
            "comments": row.get("latest_comments") or row.get("comments") or 0,
            "shares": row.get("latest_shares") or row.get("shares") or 0
        },
        "post_copy": row.get("message_zh_html") or row.get("message_html") or ""
    }
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def backfill(post_id: int, us_json_path: str, eu_json_path: str) -> None:
    """完美复用 store.py，将由 Antigravity 智能推理出来的 JSON 回填到数据库"""
    print(f"[*] Starting Antigravity backfill for post {post_id}...")

    # 1. 读取并校验 JSON 文件
    try:
        with open(us_json_path, "r", encoding="utf-8") as f:
            us_data = json.load(f)
    except Exception as exc:
        print(f"[!] Failed to read US JSON file: {exc}")
        sys.exit(1)

    try:
        with open(eu_json_path, "r", encoding="utf-8") as f:
            eu_data = json.load(f)
    except Exception as exc:
        print(f"[!] Failed to read Europe JSON file: {exc}")
        sys.exit(1)

    # 2. 执行美国 AI 分析回填
    print("[*] Processing US Copyability Analysis...")
    store.ensure_video_copyability_candidate_for_post(post_id)
    us_state = store.get_video_copyability_analysis_state(post_id)
    if not us_state:
        print("[!] Video copyability state could not be created / retrieved")
        sys.exit(1)

    analysis_id = int(us_state["id"])
    store.mark_video_copyability_running(analysis_id)

    # 封装美国结果，强制填入 antigravity provider
    us_result = {
        "overall_score": us_data.get("overall_score", 0),
        "copyability_score": us_data.get("copyability_score", 0),
        "meta_us_ad_fit_score": us_data.get("meta_us_ad_fit_score", 0),
        "product_fit_score": us_data.get("product_fit_score", 0),
        "compliance_risk_score": us_data.get("compliance_risk_score", 0),
        "recommendation": us_data.get("recommendation", "adapt"),
        "summary": us_data.get("summary", ""),
        "summary_zh": us_data.get("summary_zh", ""),
        "winning_angles": us_data.get("winning_angles") or [],
        "copy_notes": us_data.get("copy_notes") or [],
        "risk_notes": us_data.get("risk_notes") or [],
        "provider": "antigravity",
        "model": "gemini-3.5-flash"
    }

    affected_us = store.finish_video_copyability_analysis(analysis_id, result=us_result)
    print(f"[+] US Copyability backfill finished. Affected rows: {affected_us}")

    # 3. 执行欧洲 AI 分析与中文翻译回填
    print("[*] Processing Europe Suitability Analysis...")
    store.ensure_europe_fit_candidate_for_post(post_id)
    store.mark_europe_fit_running(post_id)

    # 组装欧洲评估基础结果
    eu_result = {
        "suitability_score": eu_data.get("suitability_score", 0),
        "recommendation": eu_data.get("recommendation", "adapt_before_translation"),
        "direct_reuse": bool(eu_data.get("direct_reuse")),
        "translation_fit_score": eu_data.get("translation_fit_score", 0),
        "best_countries": eu_data.get("best_countries") or [],
        "country_scores": eu_data.get("country_scores") or {},
        "strengths": eu_data.get("strengths") or [],
        "risks": eu_data.get("risks") or [],
        "required_changes": eu_data.get("required_changes") or [],
        "reasoning": eu_data.get("reasoning", ""),
        "provider": "antigravity",
        "model": "gemini-3.5-flash",
        "raw_response": eu_data
    }

    # 欧洲中文翻译结果回填
    eu_translation = {
        "strengths": eu_data.get("strengths_zh") or eu_data.get("strengths") or [],
        "risks": eu_data.get("risks_zh") or eu_data.get("risks") or [],
        "required_changes": eu_data.get("required_changes_zh") or eu_data.get("required_changes") or [],
        "reasoning": eu_data.get("reasoning_zh") or eu_data.get("reasoning") or ""
    }

    affected_eu_fit = store.finish_europe_fit_assessment(post_id, status="done", result=eu_result)
    affected_eu_zh = store.finish_europe_fit_translation(post_id, translated=eu_translation, error_message=None)
    print(f"[+] Europe assessment backfill finished. Fit rows: {affected_eu_fit}, Zh rows: {affected_eu_zh}")
    print("[*] All backfills completed successfully!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Antigravity Meta Hot Posts Analysis Helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # show command
    show_parser = subparsers.add_parser("show", help="Show post metadata for analysis")
    show_parser.add_argument("post_id", type=int, help="ID of the post")

    # backfill command
    backfill_parser = subparsers.add_parser("backfill", help="Backfill analysis JSON data")
    backfill_parser.add_argument("post_id", type=int, help="ID of the post")
    backfill_parser.add_argument("us_json", type=str, help="Path to US analysis JSON file")
    backfill_parser.add_argument("eu_json", type=str, help="Path to Europe analysis JSON file")

    args = parser.parse_args()

    if args.command == "show":
        show_post(args.post_id)
    elif args.command == "backfill":
        backfill(args.post_id, args.us_json, args.eu_json)


if __name__ == "__main__":
    main()
