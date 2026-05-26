from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw
from appcore.link_check_runtime import LinkCheckRuntime


def _make_image(path: Path, text: str) -> Path:
    # Generate identical layout with only different textual elements
    image = Image.new("RGB", (600, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 550, 550), outline="blue", width=10)
    draw.ellipse((200, 200, 400, 400), outline="green", width=5)
    draw.text((100, 100), text, fill="black")
    image.save(path, quality=95)
    return path


def test_dual_reference_match_heuristic_detects_non_replaced(tmp_path, monkeypatch):
    # 1. Prepare three dimensions of images
    # O (Original English)
    path_orig = _make_image(tmp_path / "original_en.jpg", "Original English Text")
    # T (Translated target)
    path_ref = _make_image(tmp_path / "translated_fr.jpg", "Texte Traduit en Francais")
    # C (Crawled page image - still original English)
    path_crawled_en = _make_image(tmp_path / "crawled_storefront.jpg", "Original English Text")

    # Mock judge_same_image and analyze_image to avoid network calls
    monkeypatch.setattr("appcore.link_check_runtime.judge_same_image", lambda a, b: {"status": "done", "answer": "否"})
    monkeypatch.setattr(
        "appcore.link_check_runtime.analyze_image",
        lambda a, **kwargs: {"decision": "pass", "quality_score": 100, "quality_reason": "Audit OK"},
    )

    # 2. Mock Task State & Result Item
    task = {
        "target_language": "fr",
        "target_language_name": "法语",
        "reference_images": [
            {
                "id": "ref-1",
                "filename": "translated_fr.jpg",
                "local_path": str(path_ref),
            }
        ],
        "original_images": [
            {
                "id": "orig-1",
                "filename": "original_en.jpg",
                "local_path": str(path_orig),
            }
        ],
        "progress": {
            "compared": 0,
            "binary_checked": 0,
            "same_image_llm_done": 0,
        },
    }

    result = {
        "reference_match": {"status": "not_provided", "score": 0.0},
        "original_match": {"status": "not_provided", "score": 0.0},
        "binary_quick_check": {},
        "same_image_llm": {},
        "is_replaced": None,
        "analysis": {},
    }

    item = {
        "id": "item-1",
        "kind": "detail",
        "source_url": "https://storefront.test/img1.jpg",
        "local_path": str(path_crawled_en),
    }

    # 3. Trigger analyze_one
    runtime = LinkCheckRuntime()
    reference_paths = [ref["local_path"] for ref in task["reference_images"]]
    reference_index = {ref["local_path"]: ref for ref in task["reference_images"]}
    original_paths = [orig["local_path"] for orig in task["original_images"]]
    original_index = {orig["local_path"]: orig for orig in task["original_images"]}

    runtime._analyze_one(
        task,
        result,
        item,
        reference_paths,
        reference_index,
        original_paths,
        original_index,
    )

    # 4. Verify Dual-Reference Match Heuristics
    # Visual comparison should report that the crawled storefront matches the Original English
    # more closely than the Translated Target (S_en > S_target and S_en >= 0.95),
    # meaning is_replaced is correctly forced to False ("未替换").
    assert result["is_replaced"] is False
    assert result["analysis"]["decision"] == "replace"
    assert "英语原图视觉相似度极高" in result["analysis"]["quality_reason"]
