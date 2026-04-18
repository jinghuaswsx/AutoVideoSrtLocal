from __future__ import annotations

from appcore import task_state
from appcore.link_check_compare import find_best_reference
from appcore.link_check_fetcher import LinkCheckFetcher
from appcore.link_check_gemini import analyze_image


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

                    result["analysis"] = analyze_image(
                        item["local_path"],
                        target_language=task["target_language"],
                        target_language_name=task["target_language_name"],
                    )
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

            if item["reference_match"].get("status") == "not_matched":
                summary["reference_unmatched_count"] += 1
                summary["overall_decision"] = "unfinished"

        task["summary"] = summary
        task["status"] = "done" if summary["overall_decision"] == "done" else "review_ready"
