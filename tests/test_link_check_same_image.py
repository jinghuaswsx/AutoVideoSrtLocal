from pathlib import Path

from PIL import Image


def _make_image(path: Path, color: str) -> Path:
    Image.new("RGB", (64, 64), color).save(path)
    return path


def test_same_image_judgment_uses_image_translate_channel(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")

    monkeypatch.setattr(module, "_resolve_channel", lambda: "cloud")
    monkeypatch.setattr(
        module,
        "_call_same_image_model",
        lambda **kwargs: {
            "text": "是",
            "channel": kwargs["channel"],
            "model": kwargs["model"],
        },
    )

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == "是"
    assert result["channel"] == "cloud"
    assert result["channel_label"] == "Google Cloud (Vertex AI)"
    assert result["model"] == "gemini-3.1-flash-lite-preview"


def test_same_image_judgment_normalizes_negative_answer(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "black")

    monkeypatch.setattr(module, "_resolve_channel", lambda: "openrouter")
    monkeypatch.setattr(
        module,
        "_call_same_image_model",
        lambda **kwargs: {
            "text": "不是",
            "channel": kwargs["channel"],
            "model": kwargs["model"],
        },
    )

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == "不是"
    assert result["channel"] == "openrouter"
    assert result["channel_label"] == "OpenRouter"
    assert result["model"] == "google/gemini-3.1-flash-lite-preview"


def test_same_image_judgment_returns_error_without_crashing(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")

    monkeypatch.setattr(module, "_resolve_channel", lambda: "aistudio")

    def _boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(module, "_call_same_image_model", _boom)

    result = module.judge_same_image(site, ref)

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert result["channel"] == "aistudio"
    assert result["channel_label"] == "Google AI Studio"
    assert result["model"] == "gemini-3.1-flash-lite-preview"
    assert "provider down" in result["reason"]
