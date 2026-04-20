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


def _build_summary(items: list[dict]) -> dict:
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


class LinkCheckRuntime:
    def __init__(self, *, fetcher: LinkCheckFetcher | None = None) -> None:
        self.fetcher = fetcher or LinkCheckFetcher()

    def start(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task or task.get("type") != "link_check":
            return

        task["error"] = ""
        task["summary"] = _build_summary(task.get("items") or [])

        try:
            self._enter_step(
                task_id,
                task,
                step="lock_locale",
                status="locking_locale",
                message="正在锁定页面语言",
            )
            page = self.fetcher.fetch_page(task["link_url"], task["target_language"])
            task["resolved_url"] = page.resolved_url
            task["page_language"] = page.page_language

            self._transition_to_step(
                task_id,
                task,
                completed_step="lock_locale",
                next_step="download",
                status="downloading",
                done_message="页面语言锁定完成",
                running_message="正在下载页面图片",
            )
            downloaded = self.fetcher.download_images(page.images, task["task_dir"])

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
            task["summary"] = _build_summary(task["items"])

            self._transition_to_step(
                task_id,
                task,
                completed_step="download",
                next_step="analyze",
                status="analyzing",
                done_message=f"图片下载完成，共 {len(downloaded)} 张",
                running_message="正在分析图片",
            )

            references = task.get("reference_images") or []
            reference_paths = [ref["local_path"] for ref in references]
            reference_index = {ref["local_path"]: ref for ref in references}

            analyze_failed = False
            for item in downloaded:
                result = self._build_item_result(item, reference_paths)
                try:
                    self._analyze_one(task, result, item, reference_paths, reference_index)
                    task["progress"]["analyzed"] += 1
                    result["status"] = "done"
                except Exception as exc:
                    task["progress"]["failed"] += 1
                    result["status"] = "failed"
                    result["error"] = str(exc)
                    analyze_failed = True

                task["items"].append(result)
                task["summary"] = _build_summary(task["items"])
                self._persist(task_id, task)

            if analyze_failed:
                task["status"] = "failed"
                task["steps"]["analyze"] = "error"
                task["step_messages"]["analyze"] = "分析阶段存在失败项，已继续完成全部图片处理"
            else:
                task["steps"]["analyze"] = "done"
                task["step_messages"]["analyze"] = "图片分析完成"

            self._start_summarize(task_id, task)
            task["summary"] = _build_summary(task["items"])
            task["steps"]["summarize"] = "done"
            task["step_messages"]["summarize"] = "结果汇总完成"

            if not analyze_failed:
                task["status"] = (
                    "done" if task["summary"]["overall_decision"] == "done" else "review_ready"
                )
            self._persist(task_id, task)
            task_state.set_expires_at(task_id, "link_check")
        except Exception as exc:
            self._fail_current_step(task_id, task, str(exc))
            task_state.set_expires_at(task_id, "link_check")

    def _build_item_result(self, item: dict, reference_paths: list[str]) -> dict:
        waiting_binary = "等待参考图匹配结果" if reference_paths else "未提供参考图，跳过二值快检"
        waiting_same = "等待参考图匹配结果" if reference_paths else "未提供参考图，跳过同图判断"
        return {
            "id": item["id"],
            "kind": item["kind"],
            "source_url": item["source_url"],
            "_local_path": item["local_path"],
            "analysis": {},
            "reference_match": {"status": "not_provided", "score": 0.0},
            "binary_quick_check": _skipped_binary(waiting_binary),
            "same_image_llm": _skipped_same_image(waiting_same),
            "status": "running",
            "error": "",
        }

    def _analyze_one(
        self,
        task: dict,
        result: dict,
        item: dict,
        reference_paths: list[str],
        reference_index: dict[str, dict],
    ) -> None:
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
            result["binary_quick_check"] = run_binary_quick_check(item["local_path"], reference_path)
            task["progress"]["binary_checked"] += 1
            result["same_image_llm"] = judge_same_image(item["local_path"], reference_path)
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
                return
            if binary_status == "fail":
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
                return

            result["analysis"] = analyze_image(
                item["local_path"],
                target_language=task["target_language"],
                target_language_name=task["target_language_name"],
            )
            result["analysis"]["decision_source"] = "gemini_language_check"
            return

        if reference_paths:
            result["binary_quick_check"] = _skipped_binary("未匹配到参考图，跳过二值快检")
            result["same_image_llm"] = _skipped_same_image("未匹配到参考图，跳过同图判断")
        else:
            result["binary_quick_check"] = _skipped_binary("未提供参考图，跳过二值快检")
            result["same_image_llm"] = _skipped_same_image("未提供参考图，跳过同图判断")

        result["analysis"] = analyze_image(
            item["local_path"],
            target_language=task["target_language"],
            target_language_name=task["target_language_name"],
        )
        result["analysis"]["decision_source"] = "gemini_language_check"

    def _enter_step(self, task_id: str, task: dict, *, step: str, status: str, message: str) -> None:
        task["status"] = status
        task["steps"][step] = "running"
        task["step_messages"][step] = message
        self._persist(task_id, task)

    def _transition_to_step(
        self,
        task_id: str,
        task: dict,
        *,
        completed_step: str,
        next_step: str,
        status: str,
        done_message: str,
        running_message: str,
    ) -> None:
        task["steps"][completed_step] = "done"
        task["step_messages"][completed_step] = done_message
        task["status"] = status
        task["steps"][next_step] = "running"
        task["step_messages"][next_step] = running_message
        self._persist(task_id, task)

    def _start_summarize(self, task_id: str, task: dict) -> None:
        if task.get("status") != "failed":
            task["status"] = "summarizing"
        task["steps"]["summarize"] = "running"
        task["step_messages"]["summarize"] = "正在汇总结果"
        self._persist(task_id, task)

    def _fail_current_step(self, task_id: str, task: dict, error_message: str) -> None:
        task["status"] = "failed"
        task["error"] = error_message
        for step in ("lock_locale", "download", "analyze", "summarize"):
            if task["steps"].get(step) == "running":
                task["steps"][step] = "error"
                task["step_messages"][step] = f"步骤失败: {error_message}"
                break
        task["summary"] = _build_summary(task.get("items") or [])
        self._persist(task_id, task)

    def _persist(self, task_id: str, task: dict) -> None:
        task_state.update(
            task_id,
            status=task["status"],
            resolved_url=task.get("resolved_url", ""),
            page_language=task.get("page_language", ""),
            steps=task["steps"],
            step_messages=task["step_messages"],
            progress=task["progress"],
            items=task["items"],
            summary=task["summary"],
            error=task.get("error", ""),
        )
