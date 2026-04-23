"""Task 6 / 覆盖 7: pipeline/translate.py 支持 use_case code 前置解析。

保证：
  - 老风格 provider 字符串（"openrouter" / "doubao" / "vertex_*" / "gemini_31_flash"）原样透传
  - UseCase code（含 '.'）查 bindings，映射为老式 provider 名，业务函数的 vertex_* 分流不变
"""
from unittest.mock import patch

from pipeline import translate
from pipeline.translate import (
    _binding_lookup_for_use_case,
    _resolve_use_case_provider,
)


def test_non_use_case_string_passthrough():
    assert _resolve_use_case_provider("openrouter") == "openrouter"
    assert _resolve_use_case_provider("doubao") == "doubao"
    assert _resolve_use_case_provider("gemini_31_flash") == "gemini_31_flash"
    assert _resolve_use_case_provider("vertex_gemini_31_flash_lite") == "vertex_gemini_31_flash_lite"


def test_binding_lookup_ignores_non_use_case_string():
    assert _binding_lookup_for_use_case("openrouter") is None
    assert _binding_lookup_for_use_case("") is None
    assert _binding_lookup_for_use_case(None) is None  # type: ignore[arg-type]


def test_resolves_vertex_binding_to_known_pref_model():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "vertex_gemini_31_pro"


def test_resolves_vertex_binding_to_custom_model():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-experimental-xyz",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "vertex_custom"
    assert translate._VERTEX_PREF_MODELS["vertex_custom"] == "gemini-experimental-xyz"


def test_resolves_aistudio_binding_falls_back_to_openrouter():
    """gemini_aistudio 在 translate.py 内无通路，回退到 OpenRouter + google/ 前缀。"""
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_aistudio",
                             "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "_gemini_aistudio_fallback"
    assert translate._OPENROUTER_PREF_MODELS["_gemini_aistudio_fallback"] == "google/gemini-3.1-pro-preview"


def test_resolves_aistudio_binding_keeps_existing_google_prefix():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "gemini_aistudio",
                             "model": "google/gemini-custom",
                             "extra": {}, "source": "db"}):
        _resolve_use_case_provider("video_translate.localize")
    assert translate._OPENROUTER_PREF_MODELS["_gemini_aistudio_fallback"] == "google/gemini-custom"


def test_resolves_openrouter_binding_passthrough():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "openrouter",
                             "model": "openai/gpt-4o",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("copywriting.generate")
    assert p == "openrouter"


def test_resolves_doubao_binding_passthrough():
    with patch("pipeline.translate._binding_lookup_for_use_case",
               return_value={"provider": "doubao",
                             "model": "doubao-seed-2-0-pro",
                             "extra": {}, "source": "db"}):
        p = _resolve_use_case_provider("video_translate.localize")
    assert p == "doubao"


def test_get_model_display_name_supports_openrouter_gpt_5_mini():
    with patch.object(translate, "OPENROUTER_API_KEY", "test-openrouter-key"):
        assert translate.get_model_display_name("gpt_5_mini") == "openai/gpt-5-mini"
