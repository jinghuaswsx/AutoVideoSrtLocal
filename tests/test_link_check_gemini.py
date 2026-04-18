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

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
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

    monkeypatch.setattr(lcg.gemini, "generate", fake_generate)

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["decision"] == "pass"
    assert captured["kwargs"]["media"] == [Path(image_path)]
    assert captured["kwargs"]["response_schema"]["type"] == "object"
    assert captured["kwargs"]["service"] == "gemini"
    assert captured["kwargs"]["default_model"] == "gemini-2.5-flash"


def test_analyze_image_normalizes_missing_keys(monkeypatch):
    from appcore import link_check_gemini as lcg

    image_path = _make_workspace_tmp() / "sample.jpg"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        lcg.gemini,
        "generate",
        lambda *args, **kwargs: {"decision": "replace"},
    )

    result = lcg.analyze_image(
        image_path,
        target_language="de",
        target_language_name="德语",
    )

    assert result["needs_replacement"] is True
    assert result["detected_language"] == ""
    assert result["quality_score"] == 0
