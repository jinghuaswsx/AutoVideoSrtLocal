from pathlib import Path
from uuid import uuid4


def _make_workspace_tmp() -> Path:
    base_dir = Path("output") / "pytest-link-check" / uuid4().hex
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def test_analyze_image_passes_media_and_schema(monkeypatch):
    from appcore import link_check_gemini as lcg

    image_path = _make_workspace_tmp() / "sample.jpg"
    image_path.write_bytes(b"fake")
    captured = {}

    def fake_invoke_generate(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "has_text": True,
                "detected_language": "de",
                "language_match": True,
                "text_summary": "Hallo Welt",
                "quality_score": 95,
                "quality_reason": "ok",
                "needs_replacement": False,
                "decision": "pass",
            },
            "text": None,
            "raw": None,
            "usage": {"input_tokens": None, "output_tokens": None},
        }

    monkeypatch.setattr(lcg.llm_client, "invoke_generate", fake_invoke_generate)

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["decision"] == "pass"
    assert captured["use_case_code"] == "link_check.analyze"
    assert captured["kwargs"]["media"] == [Path(image_path)]
    assert captured["kwargs"]["response_schema"]["type"] == "object"
    assert captured["kwargs"]["temperature"] == 0


def test_analyze_image_accepts_legacy_top_level_payload(monkeypatch):
    from appcore import link_check_gemini as lcg

    image_path = _make_workspace_tmp() / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        lcg.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {
            "has_text": True,
            "detected_language": "de",
            "language_match": True,
            "text_summary": "Hallo Welt",
            "quality_score": 88,
            "quality_reason": "ok",
            "needs_replacement": False,
            "decision": "pass",
        },
    )

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["has_text"] is True
    assert result["text_summary"] == "Hallo Welt"
    assert result["quality_score"] == 88


def test_analyze_image_folds_no_text_to_pass(monkeypatch):
    from appcore import link_check_gemini as lcg

    image_path = _make_workspace_tmp() / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        lcg.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {
            "json": {
                "decision": "no_text",
                "needs_replacement": False,
            },
            "text": None,
            "raw": None,
            "usage": {"input_tokens": None, "output_tokens": None},
        },
    )

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["decision"] == "pass"
    assert result["needs_replacement"] is False


def test_analyze_image_uses_numeric_fallback_for_quality_score(monkeypatch):
    from appcore import link_check_gemini as lcg

    image_path = _make_workspace_tmp() / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        lcg.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {
            "json": {
                "decision": "review",
                "quality_score": "not-a-number",
            },
            "text": None,
            "raw": None,
            "usage": {"input_tokens": None, "output_tokens": None},
        },
    )

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["quality_score"] == 0


def test_analyze_image_prompt_uses_similarity_not_generation_wording():
    from appcore import link_check_gemini as lcg

    prompt = lcg._build_prompt(target_language="de", target_language_name="德语")

    assert "生硬程度" in prompt
    assert "生成程度" not in prompt
