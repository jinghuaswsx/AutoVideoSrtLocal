from __future__ import annotations

from appcore import task_state
from appcore.link_check_compare import find_best_reference, run_binary_quick_check, is_same_shopify_image_url
from appcore.link_check_fetcher import LinkCheckFetcher


def _default_locale_evidence(task: dict) -> dict:
    return {
        "target_language": task.get("target_language", ""),
        "requested_url": task.get("link_url", ""),
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }


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
        "replaced_count": 0,
        "not_replaced_count": 0,
        "total_count": len(items),
    }

    for item in items:
        if item.get("status") == "failed":
            summary["review_count"] += 1
            summary["overall_decision"] = "unfinished"
            continue

        is_replaced = item.get("is_replaced")
        if is_replaced is True:
            summary["replaced_count"] += 1
        elif is_replaced is False:
            summary["not_replaced_count"] += 1

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

    def _merge_locale_evidence(self, task: dict, evidence: dict | None = None) -> dict:
        merged = _default_locale_evidence(task)
        merged.update(dict(task.get("locale_evidence") or {}))
        if evidence is not None:
            merged.update(dict(evidence or {}))
        return merged

    def start(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task or task.get("type") != "link_check":
            return

        task["error"] = ""
        task["locale_evidence"] = self._merge_locale_evidence(task)
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
            task["locale_evidence"] = self._merge_locale_evidence(
                task, getattr(page, "locale_evidence", None)
            )

            if not task["locale_evidence"].get("locked"):
                raise RuntimeError(
                    task["locale_evidence"].get("failure_reason")
                    or "target page was not locked before download"
                )

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

            existing_items = {
                it["id"]: it for it in (task.get("items") or [])
                if it.get("status") in {"done", "failed"}
            }

            analyzed_count = sum(1 for it in existing_items.values() if it.get("status") == "done")
            failed_count = sum(1 for it in existing_items.values() if it.get("status") == "failed")
            compared_count = 0
            binary_checked_count = 0
            same_image_llm_done_count = 0
            for it in existing_items.values():
                if it.get("reference_match", {}).get("status") in {"matched", "not_matched"}:
                    compared_count += 1
                if it.get("binary_quick_check", {}).get("status") in {"pass", "fail"}:
                    binary_checked_count += 1
                if it.get("same_image_llm", {}).get("status") == "done":
                    same_image_llm_done_count += 1

            task["progress"] = {
                "total": len(downloaded),
                "downloaded": len(downloaded),
                "analyzed": analyzed_count,
                "compared": compared_count,
                "binary_checked": binary_checked_count,
                "same_image_llm_done": same_image_llm_done_count,
                "failed": failed_count,
            }
            task["items"] = []
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

            originals = task.get("original_images") or []
            original_paths = [orig["local_path"] for orig in originals]
            original_index = {orig["local_path"]: orig for orig in originals}

            # 1. 预先构建所有图片的结果槽并初始化，保持原本的列表顺序，方便前端渲染一致性
            results_map = {}
            new_items = []
            for item in downloaded:
                item_id = item["id"]
                if item_id in existing_items:
                    result = existing_items[item_id]
                else:
                    result = self._build_item_result(item, reference_paths, original_paths)
                results_map[item_id] = result
                new_items.append(result)

            task["items"] = new_items
            task["summary"] = _build_summary(task["items"])
            self._persist(task_id, task)

            analyze_failed = False
            to_analyze = []
            for item in downloaded:
                item_id = item["id"]
                if item_id in existing_items:
                    res = existing_items[item_id]
                    if res.get("status") == "failed":
                        analyze_failed = True
                    if res.get("status") in {"done", "failed"}:
                        continue
                to_analyze.append(item)

            # 2. 启用并发多线程审计（最大并发数为 10）
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            max_workers = min(10, len(to_analyze)) if to_analyze else 1
            progress_lock = threading.Lock()

            def process_single_item(item):
                item_id = item["id"]
                res = results_map[item_id]
                try:
                    self._analyze_one(
                        task,
                        res,
                        item,
                        reference_paths,
                        reference_index,
                        original_paths,
                        original_index,
                        lock=progress_lock,
                    )
                    res["status"] = "done"
                    return item_id, True, None
                except Exception as exc:
                    res["status"] = "failed"
                    res["error"] = str(exc)
                    return item_id, False, str(exc)

            if to_analyze:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(process_single_item, item): item for item in to_analyze}
                    for future in as_completed(futures):
                        item_id, success, err_msg = future.result()
                        
                        with progress_lock:
                            if success:
                                task["progress"]["analyzed"] += 1
                            else:
                                task["progress"]["failed"] += 1
                                analyze_failed = True
                            
                            # 每次并发线程完成一张图，即时刷新数据库状态，提供流畅进展刷新
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
            locale_evidence = getattr(exc, "locale_evidence", None)
            if locale_evidence is not None:
                task["locale_evidence"] = self._merge_locale_evidence(task, locale_evidence)
            self._fail_current_step(task_id, task, str(exc))
            task_state.set_expires_at(task_id, "link_check")

    def _build_item_result(self, item: dict, reference_paths: list[str], original_paths: list[str]) -> dict:
        waiting_binary = "等待参考图匹配结果" if reference_paths else "未提供参考图，跳过二值快检"
        waiting_same = "等待参考图匹配结果" if reference_paths else "未提供参考图，跳过同图判断"
        return {
            "id": item["id"],
            "kind": item["kind"],
            "source_url": item["source_url"],
            "_local_path": item["local_path"],
            "download_evidence": dict(item.get("download_evidence") or {}),
            "analysis": {},
            "reference_match": {"status": "not_provided", "score": 0.0},
            "original_match": {"status": "not_provided", "score": 0.0},
            "binary_quick_check": _skipped_binary(waiting_binary),
            "same_image_llm": _skipped_same_image(waiting_same),
            "is_replaced": None,
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
        original_paths: list[str],
        original_index: dict[str, dict],
        lock: threading.Lock | None = None,
    ) -> None:
        def incr_progress(key: str):
            if lock:
                with lock:
                    task["progress"][key] += 1
            else:
                task["progress"][key] += 1

        # 1. 尺寸免检校验：如果是尺寸极小的边角小图（如支付图标、小挂件等），直接绿色放行，避免误报
        try:
            from PIL import Image
            with Image.open(item["local_path"]) as img:
                w, h = img.size
                if w <= 120 or h <= 120:
                    result["analysis"] = {
                        "decision": "pass",
                        "decision_source": "size_threshold_bypass",
                        "has_text": False,
                        "detected_language": "",
                        "language_match": True,
                        "text_summary": "免检小图：图片尺寸过小，判定为图标或非卖点宣传杂图，直接予以通过。",
                        "quality_score": 100,
                        "quality_reason": f"图片尺寸为 {w}x{h}，判定为网页边角挂饰或小图标，直接放行。",
                        "needs_replacement": False,
                    }
                    result["is_replaced"] = None
                    return
        except Exception:
            pass

        # 2. Shopify CDN URL 精确匹配短路（Green Pass）
        matched_ref = None
        if reference_index:
            for ref in reference_index.values():
                ref_cdn = ref.get("shopify_cdn_url")
                if ref_cdn and is_same_shopify_image_url(item["source_url"], ref_cdn):
                    matched_ref = ref
                    break

        if matched_ref:
            result["reference_match"] = {
                "status": "matched",
                "score": 1.0,
                "reference_path": matched_ref["local_path"],
                "reference_id": matched_ref.get("id", ""),
                "reference_filename": matched_ref.get("filename", ""),
            }
            incr_progress("compared")
            result["binary_quick_check"] = {
                "status": "pass",
                "binary_similarity": 1.0,
                "foreground_overlap": 1.0,
                "threshold": 0.90,
                "reason": "Shopify CDN URL 精确匹配，自动通过二值快检",
            }
            incr_progress("binary_checked")
            result["same_image_llm"] = {
                "status": "done",
                "answer": "是",
                "channel": "shopify_cdn_url_match",
                "channel_label": "Shopify CDN URL 匹配",
                "model": "short_circuit",
                "reason": "Shopify CDN URL 精确匹配，自动判断为相同图片",
            }
            incr_progress("same_image_llm_done")
            result["is_replaced"] = True

            # 【绿色免检通道】：Shopify CDN 成功吻合，免除多模态大模型二次审计
            result["analysis"] = {
                "decision": "pass",
                "decision_source": "green_pass",
                "has_text": True,
                "detected_language": task["target_language"],
                "language_match": True,
                "text_summary": "绿色免检通道：当前网页图片 URL 与后台黄金参考图 100% 吻合，自动放行。",
                "quality_score": 100,
                "quality_reason": f"换图检测已通过（Shopify CDN URL 匹配，ID: {matched_ref.get('id', '')}，文件名: {matched_ref.get('filename', '')}），绿色通道免检放行。",
                "needs_replacement": False,
            }
            return

        # 3. 正常参考图计算与比对
        if reference_paths:
            best_reference = find_best_reference(item["local_path"], reference_paths)
            reference_meta = reference_index.get(best_reference.get("reference_path", ""), {})
            result["reference_match"] = {
                **best_reference,
                "reference_id": reference_meta.get("id", ""),
                "reference_filename": reference_meta.get("filename", ""),
            }
            incr_progress("compared")

        if original_paths:
            best_original = find_best_reference(item["local_path"], original_paths)
            original_meta = original_index.get(best_original.get("reference_path", ""), {})
            result["original_match"] = {
                **best_original,
                "original_id": original_meta.get("id", ""),
                "original_filename": original_meta.get("filename", ""),
            }

        reference_status = result["reference_match"].get("status")
        if reference_status == "matched":
            reference_path = result["reference_match"].get("reference_path", "")
            result["binary_quick_check"] = run_binary_quick_check(item["local_path"], reference_path)
            incr_progress("binary_checked")
            result["same_image_llm"] = _skipped_same_image("极速比对模式已启用，跳过同图大模型分析")

            binary_status = result["binary_quick_check"].get("status")

            # 1. 换图检测部分（Part 1）：判断真实页面的图跟后台翻译结果图是不是一张图（有没有换到位）
            is_replaced = (binary_status == "pass")

            # 双参考图判定 heuristic
            s_target = result["reference_match"].get("score", 0.0)
            s_en = result["original_match"].get("score", 0.0) if "original_match" in result else 0.0
            if original_paths and s_en > s_target and s_en >= 0.95:
                is_replaced = False

            result["is_replaced"] = is_replaced

            if not is_replaced:
                # 换图未换到位（与后台翻译参考图不一致，或是没有被翻译的原图/错误图）
                reason = "检测到页面图片与后台翻译的参考图不一致，二值快检不匹配，判定替换未到位"
                if original_paths and s_en > s_target and s_en >= 0.95:
                    reason = f"检测到页面实际图与英语原图视觉相似度极高 ({s_en:.3f} > {s_target:.3f})，确认尚未替换为翻译后的参考图"
                result["analysis"] = {
                    "decision": "replace",
                    "decision_source": "binary_quick_check",
                    "has_text": True,
                    "detected_language": "",
                    "language_match": False,
                    "text_summary": "",
                    "quality_score": 0,
                    "quality_reason": reason,
                    "needs_replacement": True,
                }
                return

            # 2. 绿色免检通道（Part 2）：已经换到位了，不再使用大模型进行重复审查，直接放行
            ref_id = reference_meta.get("id", "")
            ref_filename = reference_meta.get("filename", "")
            result["analysis"] = {
                "decision": "pass",
                "decision_source": "green_pass",
                "has_text": True,
                "detected_language": task["target_language"],
                "language_match": True,
                "text_summary": "绿色免检通道：当前图片与后台审核通过的黄金参考图完全吻合，自动继承合格判定，免除大模型二次审计。",
                "quality_score": 100,
                "quality_reason": f"换图检测已换到位。当前图片与后台审核合格的参考图片（ID: {ref_id}, 文件名: {ref_filename}）完全一致，绿色通道直接予以通过。",
                "needs_replacement": False,
            }
            return

        if reference_paths:
            result["binary_quick_check"] = _skipped_binary("未匹配到参考图，跳过二值快检")
            result["same_image_llm"] = _skipped_same_image("未匹配到参考图，跳过同图判断")
            result["is_replaced"] = False  # references existed but couldn't match this page image, so not replaced
            result["analysis"] = {
                "decision": "replace",
                "decision_source": "no_reference_match",
                "has_text": True,
                "detected_language": "",
                "language_match": False,
                "text_summary": "",
                "quality_score": 0,
                "quality_reason": "线上图片未匹配到任何后台已翻译的参考图，判定替换未到位。",
                "needs_replacement": True,
            }
        else:
            result["binary_quick_check"] = _skipped_binary("未提供参考图，跳过二值快检")
            result["same_image_llm"] = _skipped_same_image("未提供参考图，跳过同图判断")
            result["is_replaced"] = None
            result["analysis"] = {
                "decision": "pass",
                "decision_source": "no_references_provided",
                "has_text": False,
                "detected_language": "",
                "language_match": True,
                "text_summary": "未提供任何后台翻译参考图，自动跳过检查。",
                "quality_score": 100,
                "quality_reason": "后台未配置任何用于比对的翻译参考图，自动跳过该图替换把关。",
                "needs_replacement": False,
            }

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
            locale_evidence=dict(task.get("locale_evidence") or {}),
            steps=task["steps"],
            step_messages=task["step_messages"],
            progress=task["progress"],
            items=task["items"],
            summary=task["summary"],
            error=task.get("error", ""),
        )
