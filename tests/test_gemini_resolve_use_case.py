"""Task 7: appcore.gemini.resolve_config 支持 use_case code 前置解析。

所有测试全部 mock `resolve_key`/`resolve_extra`/`_binding_lookup`，不触发真实 DB。
"""
from unittest.mock import patch

from appcore import gemini


def test_resolve_config_uses_use_case_binding_when_provider_is_gemini_aistudio(monkeypatch):
    """use_case code + binding.provider=gemini_aistudio → 覆盖 model，key 走 gemini 通道。"""
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini_aistudio",
                             "model": "gemini-custom-model",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.resolve_key", return_value="env-key"):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-pro-preview",
        )
    assert model == "gemini-custom-model"


def test_resolve_config_fallback_when_no_binding():
    """无 binding（service 是老风格 'gemini'）→ 走原逻辑，default_model 生效。"""
    with patch("appcore.gemini._binding_lookup", return_value=None), \
         patch("appcore.gemini.GEMINI_API_KEY", "env-key"):
        _, model = gemini.resolve_config(
            user_id=None, service="gemini",
            default_model="default-model",
        )
    assert model == "default-model"


def test_resolve_config_ignores_binding_of_non_gemini_provider():
    """binding 指向 openrouter 时，gemini.py 里不覆盖 model，走默认。"""
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "openrouter",
                             "model": "openai/gpt-4o",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.resolve_key", return_value="user-key"), \
         patch("appcore.gemini.resolve_extra", return_value={}):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-default",
        )
    assert model == "gemini-default"


def test_resolve_config_ignores_binding_of_gemini_vertex():
    """binding 指向 gemini_vertex 时，gemini.py（AI Studio 通道）不应覆盖——Vertex 另走 adapter。"""
    with patch("appcore.gemini._binding_lookup",
               return_value={"provider": "gemini_vertex",
                             "model": "gemini-3.1-pro-preview",
                             "extra": {}, "source": "db"}), \
         patch("appcore.gemini.resolve_key", return_value="user-key"), \
         patch("appcore.gemini.resolve_extra", return_value={}):
        _, model = gemini.resolve_config(
            user_id=42, service="video_score.run",
            default_model="gemini-3.1-flash-lite-preview",
        )
    # gemini_vertex 走不同路径，这里 model 保持 default
    assert model == "gemini-3.1-flash-lite-preview"


def test_binding_lookup_ignores_plain_service():
    assert gemini._binding_lookup("gemini") is None
    assert gemini._binding_lookup("gemini_video_analysis") is None
    assert gemini._binding_lookup("") is None


def test_legacy_service_path_preserves_model_id_extra():
    """老路径：service='gemini_video_analysis' + extra.model_id 生效。"""
    with patch("appcore.gemini.resolve_key", return_value="user-key"), \
         patch("appcore.gemini.resolve_extra",
               return_value={"model_id": "user-chose-model"}), \
         patch("appcore.gemini._binding_lookup", return_value=None):
        _, model = gemini.resolve_config(
            user_id=1, service="gemini_video_analysis",
            default_model="default-model",
        )
    assert model == "user-chose-model"
