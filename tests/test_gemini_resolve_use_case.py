"""Task 7: appcore.gemini.resolve_config 支持 use_case code 前置解析。

所有测试全部 mock `resolve_key`/`resolve_extra`/`_binding_lookup`，不触发真实 DB。
"""
from types import SimpleNamespace
from unittest.mock import patch

from appcore import gemini


def _cfg(api_key="db-key", model_id="", extra=None):
    return SimpleNamespace(
        api_key=api_key,
        model_id=model_id,
        extra_config=extra or {},
    )


def test_resolve_config_uses_use_case_binding_when_provider_is_gemini_aistudio(monkeypatch):
    """use_case code + binding.provider=gemini_aistudio → 覆盖 model，key 走 gemini 通道。"""
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini_aistudio",
                             "model": "gemini-custom-model",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="db-aistudio-key", model_id="db-model")):
        key, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-pro-preview",
        )
    assert key == "db-aistudio-key"
    assert model == "gemini-custom-model"


def test_resolve_config_fallback_when_no_binding():
    """无 binding（service 是老风格 'gemini'）→ 走原逻辑，default_model 生效。"""
    with patch("appcore.gemini._binding_lookup", return_value=None), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="db-aistudio-key", model_id="")):
        key, model = gemini.resolve_config(
            user_id=None, service="gemini",
            default_model="default-model",
        )
    assert key == "db-aistudio-key"
    assert model == "default-model"


def test_resolve_config_ignores_binding_of_non_gemini_provider():
    """binding 指向 openrouter 时，gemini.py 里不覆盖 model，走默认。"""
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "openrouter",
                             "model": "openai/gpt-4o",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="db-aistudio-key", model_id="")):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-default",
        )
    assert model == "gemini-default"


def test_resolve_config_supports_gemini_vertex_model():
    """resolve_config 可解析 Vertex model，但不返回 API key。"""
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="cloud-key", model_id="")):
        key, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-flash-lite-preview",
        )
    assert key == "cloud-key"
    assert model == "gemini-3.1-pro-preview"


def test_resolve_config_supports_gemini_vertex_adc():
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini_vertex_adc",
                             "model": "gemini-2.5-flash",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="ignored-key", model_id="")):
        key, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-flash-lite-preview",
        )
    assert key == ""
    assert model == "gemini-2.5-flash"


def test_binding_lookup_ignores_plain_service():
    assert gemini._binding_lookup("gemini") is None
    assert gemini._binding_lookup("gemini_video_analysis") is None
    assert gemini._binding_lookup("") is None


def test_legacy_service_path_preserves_model_id_extra():
    """老路径：service='gemini_video_analysis' + DB model_id 生效。"""
    with patch("appcore.gemini._binding_lookup", return_value=None), \
         patch("appcore.gemini.get_provider_config",
               return_value=_cfg(api_key="user-key", model_id="user-chose-model")):
        key, model = gemini.resolve_config(
            user_id=1, service="gemini_video_analysis",
            default_model="default-model",
        )
    assert key == "user-key"
    assert model == "user-chose-model"
