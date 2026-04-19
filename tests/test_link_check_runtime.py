from pathlib import Path
from uuid import uuid4

from appcore import task_state


def _workspace_tmp() -> Path:
    base_dir = Path("scratch") / "runtime-tests" / uuid4().hex
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def test_runtime_marks_locale_failure(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    task_state.create_link_check(
        "lc-1",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            raise RuntimeError("locale lock failed")

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-1")

    saved = task_state.get("lc-1")
    assert saved["status"] == "failed"
    assert "locale lock failed" in saved["error"]


def test_runtime_records_best_reference_match(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-2",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[{"id": "ref-1", "filename": "ref.jpg", "local_path": str(ref_path)}],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "local_path": str(site_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    monkeypatch.setattr(
        "appcore.link_check_runtime.find_best_reference",
        lambda *args, **kwargs: {
            "status": "matched",
            "score": 0.91,
            "reference_path": str(ref_path),
        },
    )
    monkeypatch.setattr(
        "appcore.link_check_runtime.analyze_image",
        lambda *args, **kwargs: {
            "decision": "pass",
            "has_text": True,
            "detected_language": "de",
            "language_match": True,
            "text_summary": "Hallo",
            "quality_score": 95,
            "quality_reason": "ok",
            "needs_replacement": False,
        },
    )

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-2")

    saved = task_state.get("lc-2")
    assert saved["items"][0]["reference_match"]["status"] == "matched"
    assert saved["items"][0]["reference_match"]["reference_id"] == "ref-1"
    assert saved["summary"]["overall_decision"] == "done"


def test_runtime_uses_binary_pass_for_matched_reference(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-binary-pass",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[{"id": "ref-1", "filename": "ref.jpg", "local_path": str(ref_path)}],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "local_path": str(site_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    monkeypatch.setattr(
        "appcore.link_check_runtime.find_best_reference",
        lambda *args, **kwargs: {
            "status": "matched",
            "score": 0.93,
            "reference_path": str(ref_path),
        },
    )
    monkeypatch.setattr(
        "appcore.link_check_runtime.run_binary_quick_check",
        lambda *args, **kwargs: {
            "status": "pass",
            "binary_similarity": 0.94,
            "foreground_overlap": 0.91,
            "threshold": 0.90,
            "reason": "ok",
        },
    )
    monkeypatch.setattr(
        "appcore.link_check_runtime.judge_same_image",
        lambda *args, **kwargs: {
            "status": "done",
            "answer": "是",
            "channel": "cloud",
            "channel_label": "Google Cloud (Vertex AI)",
            "model": "gemini-3.1-flash-lite-preview",
            "reason": "",
        },
    )

    analyze_calls = []

    def _unexpected_analyze(*args, **kwargs):
        analyze_calls.append((args, kwargs))
        return {"decision": "replace"}

    monkeypatch.setattr("appcore.link_check_runtime.analyze_image", _unexpected_analyze)

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-binary-pass")

    saved = task_state.get("lc-binary-pass")
    item = saved["items"][0]
    assert analyze_calls == []
    assert item["binary_quick_check"]["status"] == "pass"
    assert item["same_image_llm"]["answer"] == "是"
    assert item["analysis"]["decision"] == "pass"
    assert item["analysis"]["decision_source"] == "binary_quick_check"
    assert saved["summary"]["binary_checked_count"] == 1
    assert saved["summary"]["same_image_llm_done_count"] == 1
    assert saved["summary"]["same_image_llm_yes_count"] == 1


def test_runtime_falls_back_to_language_gemini_for_unmatched_reference(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-unmatched",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[{"id": "ref-1", "filename": "ref.jpg", "local_path": str(ref_path)}],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "local_path": str(site_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    monkeypatch.setattr(
        "appcore.link_check_runtime.find_best_reference",
        lambda *args, **kwargs: {
            "status": "not_matched",
            "score": 0.42,
            "reference_path": "",
        },
    )
    monkeypatch.setattr(
        "appcore.link_check_runtime.analyze_image",
        lambda *args, **kwargs: {
            "decision": "replace",
            "has_text": True,
            "detected_language": "en",
            "language_match": False,
            "text_summary": "English text",
            "quality_score": 12,
            "quality_reason": "wrong language",
            "needs_replacement": True,
        },
    )

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-unmatched")

    saved = task_state.get("lc-unmatched")
    item = saved["items"][0]
    assert item["binary_quick_check"]["status"] == "skipped"
    assert item["same_image_llm"]["status"] == "skipped"
    assert item["analysis"]["decision"] == "replace"
    assert item["analysis"]["decision_source"] == "gemini_language_check"
    assert saved["summary"]["reference_unmatched_count"] == 1
    assert saved["summary"]["binary_checked_count"] == 0
