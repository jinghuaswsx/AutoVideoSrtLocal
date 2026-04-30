from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _make_image(path: Path, color: str) -> Path:
    Image.new("RGB", (64, 64), color).save(path)
    return path


def _fake_same_image_payload(text=None, json_payload=None):
    payload = {
        "text": text,
        "json": json_payload,
        "raw": None,
        "usage": {"input_tokens": None, "output_tokens": None},
    }
    return payload


@pytest.mark.parametrize(
    ("provider", "expected_channel", "expected_label"),
    [
        ("gemini_aistudio", "aistudio", "Google AI Studio"),
        ("gemini_vertex", "cloud", "Google Cloud (Vertex AI)"),
        ("gemini_vertex_adc", "cloud_adc", "Google Vertex AI (ADC)"),
        ("openrouter", "openrouter", "OpenRouter"),
        ("doubao", "doubao", "豆包"),
    ],
)
def test_same_image_judgment_reflects_binding_provider(
    monkeypatch, tmp_path, provider, expected_channel, expected_label
):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")

    monkeypatch.setattr(
        module,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {"provider": provider, "model": "binding-model"}),
        raising=False,
    )
    monkeypatch.setattr(
        module.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: _fake_same_image_payload(text="是"),
    )

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["channel"] == expected_channel
    assert result["channel_label"] == expected_label
    assert result["model"] == "binding-model"


@pytest.mark.parametrize(
    ("json_payload", "expected_answer"),
    [
        ({"answer": "是"}, "是"),
        ({"text": "不是"}, "不是"),
    ],
)
def test_same_image_judgment_extracts_json_answer_and_text(
    monkeypatch, tmp_path, json_payload, expected_answer
):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "black")

    monkeypatch.setattr(
        module,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {"provider": "gemini_aistudio", "model": "binding-model"}),
        raising=False,
    )
    monkeypatch.setattr(module.llm_client, "invoke_generate", lambda *args, **kwargs: _fake_same_image_payload(json_payload=json_payload))

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == expected_answer


@pytest.mark.parametrize(
    "payload",
    [
        _fake_same_image_payload(text=""),
        _fake_same_image_payload(json_payload={}),
        None,
        "nope",
    ],
)
def test_same_image_judgment_returns_error_for_empty_or_unparseable_response(monkeypatch, tmp_path, payload):
    from appcore import link_check_same_image as module

    site = _make_image(tmp_path / "site.jpg", "white")
    ref = _make_image(tmp_path / "ref.jpg", "white")

    monkeypatch.setattr(
        module,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {"provider": "gemini_aistudio", "model": "binding-model"}),
        raising=False,
    )
    monkeypatch.setattr(module.llm_client, "invoke_generate", lambda *args, **kwargs: payload)

    result = module.judge_same_image(site, ref)

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert result["reason"]


def test_same_image_prompt_requires_text_match_before_same_image_check():
    from appcore import link_check_same_image as module

    prompt = module._build_prompt()

    assert "先分别提取两张图片中的全部可见文字" in prompt
    assert "只要任意一张图能提取出文字且两张图文字不一致" in prompt
    assert "如果两张图都没有可识别文字，继续判断它们是否属于同一张基础图片" in prompt
    assert "只返回“是”或“不是”" in prompt
