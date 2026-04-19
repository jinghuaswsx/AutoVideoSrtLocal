from __future__ import annotations

from appcore import task_state
from appcore.link_check_compare import find_best_reference, run_binary_quick_check
from appcore.link_check_fetcher import LinkCheckFetcher
from appcore.link_check_gemini import analyze_image
from appcore.link_check_same_image import judge_same_image


def _skipped_binary(reason: str) -> dict:
    return {
        "status": "skipped",
        "binary_similarity": 0.0,
        "foreground_overlap": 0.0,
        "threshold": 0.90,
        "reason": reason,
    }


def _skipped_same_image(reason: str) -> dict:
    return {
        "status": "skipped",
        "answer": "",
        "channel": "",
        "channel_label": "",
        "model": "",
        "reason": reason,
    }


class LinkCheckRuntime:
    def __init__(self, *, fetcher: LinkCheckFetcher | None = None) -> None:
        self.fetcher = fetcher or LinkCheckFetcher()

    def start(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task or task.get("type") != "link_check":
            return

        try:
            task_state.update(task_id, status="locking_locale", error="")
            page = self.fetcher.fetch_page(task["link_url"], task["target_language"])

            task_state.update(
                task_id,
                status="downloading",
                resolved_url=page.resolved_url,
                page_language=page.page_language,
            )
            downloaded = self.fetcher.download_images(page.images, task["task_dir"])

            task = task_state.get(task_id) or task
            task["items"] = []
            task["progress"] = {
                "total": len(downloaded),
                "downloaded": len(downloaded),
                "analyzed": 0,
                "compared": 0,
                "binary_checked": 0,
                "same_image_llm_done": 0,
                "failed": 0,
            }
            task_state.update(
                task_id,
                status="analyzing",
                items=task["items"],
                progress=task["progress"],
            )

            references = task.get("reference_images") or []
            reference_paths = [ref["local_path"] for ref in references]
            reference_index = {ref["local_path"]: ref for ref in references}

            for item in downloaded:
                result = {
                    "id": item["id"],
                    "kind": item["kind"],
                    "source_url": item["source_url"],
                    "_local_path": item["local_path"],
                    "analysis": {},
                    "reference_match": {"status": "not_provided", "score": 0.0},
                    "binary_quick_check": _skipped_binary("未提供参考图，跳过二值快检"),
                    "same_image_llm": _skipped_same_image("未提供参考图，跳过同图判断"),
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
                        task["progress"]["compared"] += 1
                    reference_status = result["reference_match"].get("status")
                    if reference_status == "matched":
                        reference_path = result["reference_match"].get("reference_path", "")
                        result["binary_quick_check"] = run_binary_quick_check(
                            item["local_path"],
                            reference_path,
                        )
                        task["progress"]["binary_checked"] += 1
                        result["same_image_llm"] = judge_same_image(
                            item["local_path"],
                            reference_path,
                        )
                        if result["same_image_llm"].get("status") == "done":
                            task["progress"]["same_image_llm_done"] += 1

                        binary_status = result["binary_quick_check"].get("status")
                        if binary_status == "pass":
                            result["analysis"] = {
                                "decision": "pass",
                                "decision_source": "binary_quick_check",
                                "has_text": True,
                                "detected_language": task["target_language"],
                                "language_match": True,
                                "text_summary": "",
                                "quality_score": 100,
                                "quality_reason": "参考图已匹配且二值快检通过，跳过语言模型",
                                "needs_replacement": False,
                            }
                        elif binary_status == "fail":
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
                                target_language=task["target_language"],
                                target_language_name=task["target_language_name"],
                            )
                            result["analysis"]["decision_source"] = "gemini_language_check"
                    else:
                        reason = "未匹配到参考图，跳过二值快检" if reference_paths else "未提供参考图，跳过二值快检"
                        llm_reason = "未匹配到参考图，跳过同图判断" if reference_paths else "未提供参考图，跳过同图判断"
                        result["binary_quick_check"] = _skipped_binary(reason)
                        result["same_image_llm"] = _skipped_same_image(llm_reason)
                        result["analysis"] = analyze_image(
                            item["local_path"],
                            target_language=task["target_language"],
                            target_language_name=task["target_language_name"],
                        )
                        result["analysis"]["decision_source"] = "gemini_language_check"

                    task["progress"]["analyzed"] += 1
                    result["status"] = "done"
                except Exception as exc:
                    task["progress"]["failed"] += 1
                    result["status"] = "failed"
                    result["error"] = str(exc)
                task["items"].append(result)
                task_state.update(task_id, items=task["items"], progress=task["progress"])

            self._finalize(task)
            task_state.update(
                task_id,
                status=task["status"],
                items=task["items"],
                progress=task["progress"],
                summary=task["summary"],
            )
        except Exception as exc:
            task_state.update(task_id, status="failed", error=str(exc))

    def _finalize(self, task: dict) -> None:
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

        for item in task["items"]:
            if item.get("status") == "failed":
                summary["review_count"] += 1
                summary["overall_decision"] = "unfinished"
                continue

            decision = item["analysis"].get("decision")
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

            reference_status = item["reference_match"].get("status")
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

        task["summary"] = summary
        task["status"] = "done" if summary["overall_decision"] == "done" else "review_ready"
