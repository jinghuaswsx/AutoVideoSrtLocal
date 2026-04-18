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
