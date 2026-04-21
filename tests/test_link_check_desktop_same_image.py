from pathlib import Path

from PIL import Image


def _make_image(path: Path, color: str) -> Path:
    Image.new("RGB", (64, 64), color).save(path)
    return path


def test_desktop_same_image_uses_fixed_model_and_channel(monkeypatch, tmp_path):
    from link_check_desktop import same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")
    captured = {}

    def fake_generate_json(*, model, prompt, media, response_schema, temperature):
        captured["model"] = model
        captured["media"] = media
        captured["response_schema"] = response_schema
        captured["temperature"] = temperature
        return {"answer": "是"}

    monkeypatch.setattr(module.gemini_client, "generate_json", fake_generate_json)

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == "是"
    assert result["channel"] == "aistudio"
    assert result["channel_label"] == "Google AI Studio"
    assert result["model"] == captured["model"]
    assert captured["media"] == [Path(site), Path(ref)]
    assert captured["response_schema"]["type"] == "object"
    assert captured["temperature"] == 0


def test_desktop_same_image_returns_error_for_empty_or_unparseable_response(monkeypatch, tmp_path):
    from link_check_desktop import same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")

    monkeypatch.setattr(module.gemini_client, "generate_json", lambda **kwargs: {})

    result = module.judge_same_image(site, ref)

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert result["reason"]


def test_desktop_same_image_prompt_requires_text_match_before_same_image_check():
    from link_check_desktop import same_image as module

    prompt = module._build_prompt()

    assert "先分别提取两张图片中的全部可见文字" in prompt
    assert "只要任意一张图能提取出文字且两张图文字不一致" in prompt
    assert "如果两张图都没有可识别文字，继续判断它们是否属于同一张基础图片" in prompt
    assert "只返回“是”或“不是”" in prompt
