from __future__ import annotations


def test_write_report_renders_summary_and_relative_image_paths(tmp_path):
    from link_check_desktop import report

    workspace_root = tmp_path / "img" / "402-20260421170000"
    site_dir = workspace_root / "site"
    reference_dir = workspace_root / "reference"
    site_dir.mkdir(parents=True)
    reference_dir.mkdir(parents=True)

    site_file = site_dir / "site-001.jpg"
    reference_file = reference_dir / "ref-001.jpg"
    site_file.write_bytes(b"site")
    reference_file.write_bytes(b"reference")

    result = {
        "workspace_root": str(workspace_root),
        "product": {"id": 402, "name": "Demo Product"},
        "target_language": "fr",
        "target_language_name": "法语",
        "normalized_url": "https://newjoyloo.com/fr/products/demo",
        "analysis": {
            "summary": {
                "overall_decision": "review",
                "pass_count": 1,
                "replace_count": 1,
                "review_count": 0,
            },
            "items": [
                {
                    "id": "site-001",
                    "kind": "detail",
                    "source_url": "https://cdn.example.com/site-001.jpg",
                    "local_path": str(site_file),
                    "reference_match": {
                        "status": "matched",
                        "reference_path": str(reference_file),
                        "reference_filename": "ref-001.jpg",
                    },
                    "binary_quick_check": {
                        "status": "pass",
                        "binary_similarity": 0.98,
                        "foreground_overlap": 0.97,
                        "threshold": 0.90,
                        "reason": "binary ok",
                    },
                    "same_image_llm": {
                        "status": "done",
                        "answer": "是",
                        "channel_label": "Google AI Studio",
                        "model": "gemini-demo",
                        "reason": "same image",
                    },
                    "analysis": {
                        "decision": "pass",
                        "detected_language": "fr",
                        "quality_score": 92,
                        "quality_reason": "quality ok",
                    },
                    "download_evidence": {
                        "requested_url": "https://cdn.example.com/site-001.jpg",
                        "resolved_url": "https://cdn.example.com/site-001.jpg",
                        "preserved_asset": True,
                        "content_type": "image/jpeg",
                    },
                    "status": "done",
                    "error": "",
                }
            ],
        },
    }

    report_path = report.write_report(result)

    html = report_path.read_text(encoding="utf-8")
    assert report_path.name == "report.html"
    assert "Demo Product" in html
    assert "site/site-001.jpg" in html
    assert "reference/ref-001.jpg" in html
    assert "200px" in html
    assert "最终判定" in html
    assert "Google AI Studio" in html


def test_write_report_leaves_reference_slot_empty_when_not_matched(tmp_path):
    from link_check_desktop import report

    workspace_root = tmp_path / "img" / "403-20260421170500"
    site_dir = workspace_root / "site"
    reference_dir = workspace_root / "reference"
    site_dir.mkdir(parents=True)
    reference_dir.mkdir(parents=True)

    site_file = site_dir / "site-001.jpg"
    site_file.write_bytes(b"site")

    result = {
        "workspace_root": str(workspace_root),
        "product": {"id": 403, "name": "No Reference Demo"},
        "target_language": "de",
        "target_language_name": "德语",
        "normalized_url": "https://newjoyloo.com/de/products/demo",
        "analysis": {
            "summary": {
                "overall_decision": "review",
                "pass_count": 0,
                "replace_count": 0,
                "review_count": 1,
            },
            "items": [
                {
                    "id": "site-001",
                    "kind": "carousel",
                    "source_url": "https://cdn.example.com/site-001.jpg",
                    "local_path": str(site_file),
                    "reference_match": {
                        "status": "not_matched",
                        "reference_path": "",
                        "reference_filename": "",
                    },
                    "binary_quick_check": {
                        "status": "skipped",
                        "binary_similarity": 0.0,
                        "foreground_overlap": 0.0,
                        "threshold": 0.90,
                        "reason": "未匹配到参考图",
                    },
                    "same_image_llm": {
                        "status": "skipped",
                        "answer": "",
                        "channel_label": "",
                        "model": "",
                        "reason": "未匹配到参考图",
                    },
                    "analysis": {
                        "decision": "review",
                        "detected_language": "de",
                        "quality_score": 51,
                        "quality_reason": "need review",
                    },
                    "download_evidence": {},
                    "status": "done",
                    "error": "",
                }
            ],
        },
    }

    report_path = report.write_report(result)

    html = report_path.read_text(encoding="utf-8")
    assert "无参考图" in html
    assert "carousel" in html or "轮播图" in html
