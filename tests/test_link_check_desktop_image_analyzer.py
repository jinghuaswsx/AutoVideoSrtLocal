from pathlib import Path


def test_desktop_analyze_image_passes_media_and_schema(monkeypatch, tmp_path):
    from link_check_desktop import image_analyzer as module

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")
    captured = {}

    def fake_generate_json(*, model, prompt, media, response_schema, temperature):
        captured["model"] = model
        captured["prompt"] = prompt
        captured["media"] = media
        captured["response_schema"] = response_schema
        captured["temperature"] = temperature
        return {
            "has_text": True,
            "detected_language": "de",
            "language_match": True,
            "text_summary": "Hallo Welt",
            "quality_score": 95,
            "quality_reason": "ok",
            "needs_replacement": False,
            "decision": "pass",
        }

    monkeypatch.setattr(module.gemini_client, "generate_json", fake_generate_json)

    result = module.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["decision"] == "pass"
    assert captured["media"] == [Path(image_path)]
    assert captured["response_schema"]["type"] == "object"
    assert captured["temperature"] == 0


def test_desktop_analyze_image_folds_no_text_to_pass(monkeypatch, tmp_path):
    from link_check_desktop import image_analyzer as module

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        module.gemini_client,
        "generate_json",
        lambda **kwargs: {
            "decision": "no_text",
            "needs_replacement": False,
        },
    )

    result = module.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["decision"] == "pass"
    assert result["needs_replacement"] is False


def test_desktop_analyze_image_uses_numeric_fallback_for_quality_score(monkeypatch, tmp_path):
    from link_check_desktop import image_analyzer as module

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        module.gemini_client,
        "generate_json",
        lambda **kwargs: {
            "decision": "review",
            "quality_score": "not-a-number",
        },
    )

    result = module.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["quality_score"] == 0
