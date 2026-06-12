# -*- coding: utf-8 -*-
"""
Batch backfill script for generic Antigravity Meta Hot Posts evaluation results.
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

# Ensure utf-8 encoding output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from appcore.meta_hot_posts import store

def execute_backfill(post_id: int, us_data: dict, eu_data: dict) -> None:
    print(f"[*] Processing Post {post_id}...")

    # 1. US Copyability
    store.ensure_video_copyability_candidate_for_post(post_id)
    us_state = store.get_video_copyability_analysis_state(post_id)
    if not us_state:
        raise RuntimeError(f"Could not prepare US candidate for post {post_id}")
    
    analysis_id = int(us_state["id"])
    store.mark_video_copyability_running(analysis_id)
    
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
    print(f"  [+] US backfilled. Affected: {affected_us}")

    # 2. Europe Fit & Translation
    store.ensure_europe_fit_candidate_for_post(post_id)
    store.mark_europe_fit_running(post_id)
    
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
    
    eu_translation = {
        "strengths": eu_data.get("strengths_zh") or eu_data.get("strengths") or [],
        "risks": eu_data.get("risks_zh") or eu_data.get("risks") or [],
        "required_changes": eu_data.get("required_changes_zh") or eu_data.get("required_changes") or [],
        "reasoning": eu_data.get("reasoning_zh") or eu_data.get("reasoning") or ""
    }
    
    affected_eu_fit = store.finish_europe_fit_assessment(post_id, status="done", result=eu_result)
    affected_eu_zh = store.finish_europe_fit_translation(post_id, translated=eu_translation, error_message=None)
    print(f"  [+] Europe backfilled. Fit affected: {affected_eu_fit}, Zh affected: {affected_eu_zh}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python antigravity_batch_backfill_generic.py <path_to_json>")
        sys.exit(1)
        
    json_path = sys.argv[1]
    print(f"=== Starting Antigravity Batch Serial Backfill for {json_path} ===")
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            evaluations = json.load(f)
    except Exception as e:
        print(f"[!] Failed to read JSON file {json_path}: {e}")
        sys.exit(1)
        
    success_count = 0
    total = len(evaluations)
    
    for post_id_str, data in evaluations.items():
        post_id = int(post_id_str)
        try:
            execute_backfill(post_id, data["us"], data["eu"])
            success_count += 1
        except Exception as e:
            print(f"  [!] Failed to backfill post {post_id}: {e}", file=sys.stderr)
            
    print(f"\n=== Batch Complete. Success: {success_count}/{total} ===")
    if success_count == total:
        print("[*] All evaluations successfully saved to database.")
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
