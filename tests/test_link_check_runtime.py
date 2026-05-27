from pathlib import Path
from uuid import uuid4

import pytest

from appcore import task_state


def _workspace_tmp() -> Path:
    base_dir = Path("scratch") / "runtime-tests" / uuid4().hex
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


@pytest.fixture(autouse=True)
def _disable_task_state_db(monkeypatch):
    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *args, **kwargs: None)


def test_create_link_check_initializes_locale_evidence():
    task_dir = _workspace_tmp()

    task = task_state.create_link_check(
        "lc-init-locale",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="German",
        reference_images=[],
    )

    assert task["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }


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

    locale_evidence = {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "warmup_attempt_2",
        "locked": False,
        "failure_reason": "locale lock failed",
        "attempts": [
            {"phase": "initial", "locked": False},
            {"phase": "warmup", "locked": False},
        ],
    }

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            exc = RuntimeError("locale lock failed")
            exc.locale_evidence = locale_evidence
            raise exc

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-1")

    saved = task_state.get("lc-1")
    assert saved["status"] == "failed"
    assert "locale lock failed" in saved["error"]
    assert saved["locale_evidence"] == locale_evidence


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
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
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
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
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

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-binary-pass")

    saved = task_state.get("lc-binary-pass")
    item = saved["items"][0]
    assert item["binary_quick_check"]["status"] == "pass"
    assert item["same_image_llm"]["answer"] == "是"
    assert item["analysis"]["decision"] == "pass"
    assert item["analysis"]["decision_source"] == "green_pass"
    assert "换图检测已换到位" in item["analysis"]["quality_reason"]
    assert saved["summary"]["binary_checked_count"] == 1
    assert saved["summary"]["same_image_llm_done_count"] == 1
    assert saved["summary"]["same_image_llm_yes_count"] == 1


def test_runtime_intercepts_unmatched_reference_without_gemini_ocr(monkeypatch):
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
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
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

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-unmatched")

    saved = task_state.get("lc-unmatched")
    item = saved["items"][0]
    assert item["binary_quick_check"]["status"] == "skipped"
    assert item["same_image_llm"]["status"] == "skipped"
    assert item["analysis"]["decision"] == "replace"
    assert item["analysis"]["decision_source"] == "no_reference_match"
    assert saved["summary"]["reference_unmatched_count"] == 1
    assert saved["summary"]["binary_checked_count"] == 0


def test_runtime_replaces_image_when_same_image_is_false(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-same-image-false",
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
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
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
            "status": "fail",
            "binary_similarity": 0.34,
            "foreground_overlap": 0.21,
            "threshold": 0.90,
            "reason": "mismatch",
        },
    )
    monkeypatch.setattr(
        "appcore.link_check_runtime.judge_same_image",
        lambda *args, **kwargs: {
            "status": "done",
            "answer": "不是",
            "channel": "cloud",
            "channel_label": "Google Cloud (Vertex AI)",
            "model": "gemini-3.1-flash-lite-preview",
            "reason": "",
        },
    )

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-same-image-false")

    saved = task_state.get("lc-same-image-false")
    item = saved["items"][0]
    assert item["analysis"]["decision"] == "replace"
    assert item["analysis"]["decision_source"] == "same_image_llm_check"
    assert "换图未换到位" in item["analysis"]["quality_reason"]


def test_runtime_persists_step_flow_and_summary_during_success(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-success-persist",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url + "?locked=1",
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "warmup_attempt_2",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [
                            {"phase": "initial", "locked": False},
                            {"phase": "warmup", "locked": True},
                        ],
                    },
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

    updates = []
    original_update = task_state.update

    def tracking_update(task_id, **kwargs):
        original_update(task_id, **kwargs)
        if task_id == "lc-success-persist":
            current = task_state.get(task_id)
            updates.append(
                {
                    "status": current["status"],
                    "steps": dict(current["steps"]),
                    "progress": dict(current["progress"]),
                    "summary": dict(current["summary"]),
                    "items_len": len(current["items"]),
                }
            )

    monkeypatch.setattr(task_state, "update", tracking_update)

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-success-persist")

    saved = task_state.get("lc-success-persist")
    assert saved["status"] == "done"
    assert saved["steps"] == {
        "lock_locale": "done",
        "download": "done",
        "analyze": "done",
        "summarize": "done",
    }
    assert saved["summary"]["pass_count"] == 1
    assert saved["summary"]["overall_decision"] == "done"
    assert saved["progress"]["downloaded"] == 1
    assert saved["progress"]["analyzed"] == 1
    assert any(
        u["steps"]["lock_locale"] == "done" and u["steps"]["download"] == "running"
        for u in updates
    )
    assert any(
        u["steps"]["download"] == "done"
        and u["steps"]["analyze"] == "running"
        and u["progress"]["downloaded"] == 1
        for u in updates
    )
    assert any(
        u["steps"]["analyze"] == "running"
        and u["items_len"] == 1
        and u["progress"]["analyzed"] == 1
        and u["summary"]["pass_count"] == 1
        for u in updates
    )
    assert any(
        u["steps"]["summarize"] == "done"
        and u["summary"]["overall_decision"] == "done"
        for u in updates
    )


def test_runtime_sets_expires_at_when_finished(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    expires = []
    task_state.create_link_check(
        "lc-expire",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="寰疯",
        reference_images=[],
    )
    monkeypatch.setattr(task_state, "set_expires_at", lambda task_id, project_type: expires.append((task_id, project_type)))

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
                    "images": [],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-expire")

    saved = task_state.get("lc-expire")
    assert saved["status"] == "done"
    assert expires == [("lc-expire", "link_check")]


def test_runtime_persists_locale_evidence_and_download_evidence(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-evidence",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="寰疯",
        reference_images=[],
    )

    page_locale_evidence = {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "alternate_locale",
        "locked": True,
        "failure_reason": "",
        "attempts": [
            {"phase": "initial", "locked": False},
            {"phase": "alternate_locale", "locked": True},
        ],
    }
    download_evidence = {
        "requested_url": "https://img/site.jpg",
        "resolved_url": "https://cdn.example.com/site.jpg",
        "redirected": True,
        "preserved_asset": True,
    }

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url + "?locked=1",
                    "page_language": "de",
                    "locale_evidence": page_locale_evidence,
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
            return [{**images[0], "download_evidence": download_evidence}]

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-evidence")

    saved = task_state.get("lc-evidence")
    assert saved["status"] == "done"
    assert saved["locale_evidence"] == page_locale_evidence
    assert saved["items"][0]["download_evidence"] == download_evidence


def test_runtime_fails_before_download_when_page_not_locked(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    download_calls = []

    task_state.create_link_check(
        "lc-not-locked",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="寰疯",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "en",
                    "locale_evidence": {
                        "locked": False,
                        "failure_reason": "locale lock still pending",
                        "attempts": [{"phase": "initial", "locked": False}],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "local_path": str(task_dir / "site.jpg"),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            download_calls.append((images, task_dir))
            return images

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-not-locked")

    saved = task_state.get("lc-not-locked")
    assert saved["status"] == "failed"
    assert saved["error"] == "locale lock still pending"
    assert saved["resolved_url"] == "https://shop.example.com/de/products/demo"
    assert saved["page_language"] == "en"
    assert saved["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "locale lock still pending",
        "attempts": [{"phase": "initial", "locked": False}],
    }
    assert saved["items"] == []
    assert saved["progress"]["downloaded"] == 0
    assert download_calls == []


def test_runtime_continues_after_item_failure_and_persists_full_results(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    first_path = task_dir / "site-1.jpg"
    second_path = task_dir / "site-2.jpg"
    first_path.write_bytes(b"site-1")
    second_path.write_bytes(b"site-2")

    task_state.create_link_check(
        "lc-failure-persist",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url + "?locked=1",
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "warmup_attempt_2",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [
                            {"phase": "initial", "locked": False},
                            {"phase": "warmup", "locked": True},
                        ],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site-1.jpg",
                            "local_path": str(first_path),
                        },
                        {
                            "id": "site-2",
                            "kind": "detail",
                            "source_url": "https://img/site-2.jpg",
                            "local_path": str(second_path),
                        },
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    original_analyze_one = LinkCheckRuntime._analyze_one
    def fake_analyze_one(self, task, result, item, *args, **kwargs):
        if item["local_path"] == str(first_path):
            raise RuntimeError("gemini exploded")
        original_analyze_one(self, task, result, item, *args, **kwargs)

    monkeypatch.setattr(LinkCheckRuntime, "_analyze_one", fake_analyze_one)

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-failure-persist")

    saved = task_state.get("lc-failure-persist")
    assert saved["status"] == "failed"
    assert saved["steps"] == {
        "lock_locale": "done",
        "download": "done",
        "analyze": "error",
        "summarize": "done",
    }
    assert saved["progress"]["downloaded"] == 2
    assert saved["progress"]["analyzed"] == 1
    assert saved["progress"]["failed"] == 1
    assert saved["progress"]["total"] == 2
    assert len(saved["items"]) == saved["progress"]["total"] == 2
    assert saved["items"][0]["status"] == "failed"
    assert "gemini exploded" in saved["items"][0]["error"]
    assert saved["items"][1]["status"] == "done"
    assert saved["items"][1]["analysis"]["decision"] == "pass"
    assert saved["summary"]["pass_count"] == 1
    assert saved["summary"]["review_count"] == 1
    assert saved["summary"]["overall_decision"] == "unfinished"
    assert "失败项" in saved["step_messages"]["analyze"]


def test_runtime_sets_is_replaced_correctly_when_matched(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-replaced-matched",
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
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
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
            "channel_label": "Google Cloud",
            "model": "gemini-3.1-flash",
            "reason": "",
        },
    )
    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-replaced-matched")

    saved = task_state.get("lc-replaced-matched")
    item = saved["items"][0]
    assert item["is_replaced"] is True
    assert saved["summary"]["replaced_count"] == 1
    assert saved["summary"]["not_replaced_count"] == 0
    assert saved["summary"]["total_count"] == 1


def test_runtime_sets_is_replaced_correctly_when_not_matched_and_no_reference(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-replaced-not-matched",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "local_path": str(site_path),
                        },
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-replaced-not-matched")

    saved = task_state.get("lc-replaced-not-matched")
    item = saved["items"][0]
    assert item["is_replaced"] is None
    assert saved["summary"]["replaced_count"] == 0
    assert saved["summary"]["not_replaced_count"] == 0
    assert saved["summary"]["total_count"] == 1


def test_runtime_short_circuits_on_shopify_cdn_url_match(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    ref_path = task_dir / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-cdn-match",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[{
            "id": "ref-1",
            "filename": "ref.jpg",
            "local_path": str(ref_path),
            "shopify_cdn_url": "https://cdn.shopify.com/s/files/1/0000/0000/files/localized_detail.png?v=123"
        }],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://cdn.shopify.com/s/files/1/0000/0000/files/localized_detail_master.png?v=456",
                            "local_path": str(site_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    # Note: we do NOT mock find_best_reference, run_binary_quick_check, or judge_same_image
    # to guarantee they are completely bypassed when the URL matches!

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-cdn-match")

    saved = task_state.get("lc-cdn-match")
    item = saved["items"][0]
    assert item["is_replaced"] is True
    assert item["reference_match"]["status"] == "matched"
    assert item["reference_match"]["reference_id"] == "ref-1"
    assert item["binary_quick_check"]["status"] == "pass"
    assert "精确匹配" in item["binary_quick_check"]["reason"]
    assert item["same_image_llm"]["answer"] == "是"
    assert item["same_image_llm"]["channel"] == "shopify_cdn_url_match"
    assert item["analysis"]["decision"] == "pass"
    assert item["analysis"]["decision_source"] == "green_pass"
    assert "Shopify CDN URL 匹配" in item["analysis"]["quality_reason"]
    assert saved["summary"]["replaced_count"] == 1
    assert saved["summary"]["pass_count"] == 1


def test_runtime_bypasses_small_images_by_size(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime
    from PIL import Image

    task_dir = _workspace_tmp()
    # 创建一个极小的 50x50 图片来模拟小 Icon 杂图
    site_path = task_dir / "small_icon.jpg"
    img = Image.new("RGB", (50, 50), "white")
    img.save(site_path)

    task_state.create_link_check(
        "lc-small-size",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/small_icon.jpg",
                            "local_path": str(site_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-small-size")

    saved = task_state.get("lc-small-size")
    item = saved["items"][0]
    assert item["is_replaced"] is None
    assert item["analysis"]["decision"] == "pass"
    assert item["analysis"]["decision_source"] == "size_threshold_bypass"
    assert "图片尺寸过小" in item["analysis"]["text_summary"]


def test_link_check_resumption(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    first_path = task_dir / "site-1.jpg"
    first_path.write_bytes(b"first")
    second_path = task_dir / "site-2.jpg"
    second_path.write_bytes(b"second")

    task = task_state.create_link_check(
        "lc-resumption",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    # Pre-populate items: one already completed ("done"), one not started.
    task["items"] = [
        {
            "id": "site-1",
            "kind": "carousel",
            "source_url": "https://img/site-1.jpg",
            "_local_path": str(first_path),
            "download_evidence": {},
            "analysis": {
                "decision": "pass",
                "decision_source": "gemini_quality_audit",
                "quality_score": 90,
                "quality_reason": "already done previously",
            },
            "reference_match": {"status": "matched", "reference_path": "some-ref-path"},
            "original_match": {"status": "not_provided", "score": 0.0},
            "binary_quick_check": {"status": "pass", "reason": "already done"},
            "same_image_llm": {"status": "done", "answer": "是"},
            "is_replaced": True,
            "status": "done",
            "error": "",
        }
    ]

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "html_lang",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [],
                    },
                    "images": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site-1.jpg",
                            "local_path": str(first_path),
                        },
                        {
                            "id": "site-2",
                            "kind": "carousel",
                            "source_url": "https://img/site-2.jpg",
                            "local_path": str(second_path),
                        }
                    ],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-resumption")

    saved = task_state.get("lc-resumption")
    assert saved["status"] == "done"
    assert len(saved["items"]) == 2

    # Check that first item is preserved exactly
    item1 = saved["items"][0]
    assert item1["id"] == "site-1"
    assert item1["status"] == "done"
    assert item1["analysis"]["quality_reason"] == "already done previously"

    # Check that second item is processed
    item2 = saved["items"][1]
    assert item2["id"] == "site-2"
    assert item2["status"] == "done"
    assert item2["analysis"]["decision_source"] == "no_references_provided"
    assert "自动跳过" in item2["analysis"]["quality_reason"]

    # Check progress stats (accumulated)
    assert saved["progress"]["total"] == 2
    assert saved["progress"]["downloaded"] == 2
    assert saved["progress"]["analyzed"] == 2
    assert saved["progress"]["compared"] == 1  # 1 from pre-populated matched reference
    assert saved["progress"]["binary_checked"] == 1
    assert saved["progress"]["same_image_llm_done"] == 1
