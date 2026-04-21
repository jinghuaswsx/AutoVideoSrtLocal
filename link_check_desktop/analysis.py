from __future__ import annotations

from typing import Any

from link_check_desktop.image_analyzer import analyze_image
from link_check_desktop.image_compare import find_best_reference, run_binary_quick_check
from link_check_desktop.same_image import judge_same_image


def _skipped_binary(reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "binary_similarity": 0.0,
        "foreground_overlap": 0.0,
        "threshold": 0.90,
        "reason": reason,
    }


def _skipped_same_image(reason: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "answer": "",
        "channel": "",
        "channel_label": "",
        "model": "",
        "reason": reason,
    }


def _build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "pass_count": 0,
        "no_text_count": 0,
        "replace_count": 0,
        "review_count": 0,
        "reference_unmatched_count": 0,
        "reference_matched_count": 0,
        "binary_checked_count": 0,
        "binary_direct_pass_count": 0,
        "binary_direct_replace_count": 0,
        "same_image_llm_done_count": 0,
        "same_image_llm_yes_count": 0,
        "overall_decision": "done",
    }

    for item in items:
        if item.get("status") == "failed":
            summary["review_count"] += 1
            summary["overall_decision"] = "unfinished"
            continue

        decision = item.get("analysis", {}).get("decision")
        if decision == "pass":
            summary["pass_count"] += 1
        elif decision == "no_text":
            summary["no_text_count"] += 1
        elif decision == "replace":
            summary["replace_count"] += 1
            summary["overall_decision"] = "unfinished"
        else:
            summary["review_count"] += 1
            summary["overall_decision"] = "unfinished"

        reference_status = item.get("reference_match", {}).get("status")
        if reference_status == "matched":
            summary["reference_matched_count"] += 1
        elif reference_status == "not_matched":
            summary["reference_unmatched_count"] += 1

        binary_status = item.get("binary_quick_check", {}).get("status")
        if binary_status in {"pass", "fail"}:
            summary["binary_checked_count"] += 1
            if binary_status == "pass":
                summary["binary_direct_pass_count"] += 1
            else:
                summary["binary_direct_replace_count"] += 1

        same_image = item.get("same_image_llm", {})
        if same_image.get("status") == "done":
            summary["same_image_llm_done_count"] += 1
            if same_image.get("answer") == "是":
                summary["same_image_llm_yes_count"] += 1

    return summary


def analyze_downloaded_images(
    *,
    downloaded_images: list[dict[str, Any]],
    reference_images: list[dict[str, Any]],
    target_language: str,
    target_language_name: str,
) -> dict[str, Any]:
    reference_paths = [item["local_path"] for item in reference_images if item.get("local_path")]
    reference_index = {item["local_path"]: item for item in reference_images if item.get("local_path")}

    output_items: list[dict[str, Any]] = []

    for item in downloaded_images:
        result = {
            **item,
            "reference_match": {"status": "not_provided", "score": 0.0, "reference_path": ""},
            "binary_quick_check": _skipped_binary("等待参考图匹配结果"),
            "same_image_llm": _skipped_same_image("等待参考图匹配结果"),
            "analysis": {},
            "status": "running",
            "error": "",
        }
        try:
            if reference_paths:
                best_reference = find_best_reference(item["local_path"], reference_paths)
                reference_meta = reference_index.get(best_reference.get("reference_path", ""), {})
                result["reference_match"] = {
                    **best_reference,
                    "reference_id": reference_meta.get("id", ""),
                    "reference_filename": reference_meta.get("filename", ""),
                }

            if result["reference_match"].get("status") == "matched":
                reference_path = result["reference_match"].get("reference_path", "")
                result["binary_quick_check"] = run_binary_quick_check(item["local_path"], reference_path)
                result["same_image_llm"] = judge_same_image(item["local_path"], reference_path)

                if result["binary_quick_check"].get("status") == "pass":
                    result["analysis"] = {
                        "decision": "pass",
                        "decision_source": "binary_quick_check",
                        "has_text": True,
                        "detected_language": target_language,
                        "language_match": True,
                        "text_summary": "",
                        "quality_score": 100,
                        "quality_reason": "参考图已匹配且二值快检通过，直接判定通过",
                        "needs_replacement": False,
                    }
                elif result["binary_quick_check"].get("status") == "fail":
                    result["analysis"] = {
                        "decision": "replace",
                        "decision_source": "binary_quick_check",
                        "has_text": True,
                        "detected_language": "",
                        "language_match": False,
                        "text_summary": "",
                        "quality_score": 0,
                        "quality_reason": "参考图已匹配，但二值快检未通过，判定需要替换",
                        "needs_replacement": True,
                    }
                else:
                    result["analysis"] = analyze_image(
                        item["local_path"],
                        target_language=target_language,
                        target_language_name=target_language_name,
                    )
                    result["analysis"]["decision_source"] = "gemini_language_check"
            else:
                if reference_paths:
                    result["binary_quick_check"] = _skipped_binary("未匹配到参考图，跳过二值快检")
                    result["same_image_llm"] = _skipped_same_image("未匹配到参考图，跳过同图判断")
                else:
                    result["binary_quick_check"] = _skipped_binary("未提供参考图，跳过二值快检")
                    result["same_image_llm"] = _skipped_same_image("未提供参考图，跳过同图判断")

                result["analysis"] = analyze_image(
                    item["local_path"],
                    target_language=target_language,
                    target_language_name=target_language_name,
                )
                result["analysis"]["decision_source"] = "gemini_language_check"

            result["status"] = "done"
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)

        output_items.append(result)

    summary = _build_summary(output_items)
    return {"summary": summary, "items": output_items}
