from __future__ import annotations

from types import SimpleNamespace


def test_run_link_check_builds_workspace_and_result(monkeypatch, tmp_path):
    from link_check_desktop import controller

    workspace_root = tmp_path / "img" / "123-20260420230518"
    workspace = SimpleNamespace(
        root=workspace_root,
        reference_dir=workspace_root / "reference",
        site_dir=workspace_root / "site",
        compare_dir=workspace_root / "compare",
    )

    written = []

    monkeypatch.setattr(controller.storage, "create_workspace", lambda product_id, now=None: workspace)
    monkeypatch.setattr(controller.storage, "write_json", lambda path, payload: written.append((path, payload)))
    monkeypatch.setattr(controller.bootstrap_api, "fetch_bootstrap", lambda *args, **kwargs: {
        "product": {"id": 123, "product_code": "demo", "name": "Demo"},
        "target_language": "de",
        "target_language_name": "德语",
        "reference_images": [],
        "matched_by": "product_code",
        "normalized_url": "https://newjoyloo.com/de/products/demo-rjc",
    })
    monkeypatch.setattr(controller, "_download_references", lambda *args, **kwargs: [])
    monkeypatch.setattr(controller.browser_worker, "capture_page", lambda **kwargs: {
        "requested_url": "https://newjoyloo.com/de/products/demo-rjc",
        "final_url": "https://newjoyloo.com/de/products/demo-rjc",
        "html_lang": "de",
        "locked": True,
        "downloaded_images": [],
        "image_urls": [],
    })
    monkeypatch.setattr(controller.analysis, "analyze_downloaded_images", lambda **kwargs: {
        "summary": {"pass_count": 0, "replace_count": 0, "review_count": 0},
        "items": [],
    })
    monkeypatch.setattr(controller.result_schema, "build_task_manifest", lambda target_url, bootstrap, ws: {
        "target_url": target_url,
        "product_id": bootstrap["product"]["id"],
        "workspace": str(ws.root),
    })
    monkeypatch.setattr(controller.report, "write_report", lambda payload: workspace_root / "report.html")

    result = controller.run_link_check(
        base_url="http://127.0.0.1:5000",
        api_key="demo-key",
        target_url="https://newjoyloo.com/de/products/demo-rjc",
        status_cb=lambda message: None,
    )

    assert result["product"]["id"] == 123
    assert result["page"]["locked"] is True
    assert result["analysis"]["summary"]["pass_count"] == 0
    assert result["report_html_path"] == str(workspace_root / "report.html")
    assert [path.name for path, _payload in written] == ["task.json", "page_info.json", "result.json"]
