from unittest.mock import patch

import pytest

from appcore import llm_providers_dao


def test_load_user_providers_shape():
    fake = {
        "openrouter": {"key_value": "k1", "extra": {"base_url": "u1"}},
        "elevenlabs": {"key_value": "k2", "extra": {}},
    }
    with patch("appcore.llm_providers_dao.get_all", return_value=fake):
        out = llm_providers_dao.load_user_providers(1)
    assert out["openrouter"] == {"key_value": "k1", "base_url": "u1"}
    assert out["elevenlabs"] == {"key_value": "k2"}
    assert out["doubao_llm"] == {"key_value": "", "base_url": ""}
    assert out["gemini"] == {"key_value": ""}
    assert out["gemini_cloud"] == {"key_value": ""}


def test_load_user_providers_returns_empty_for_new_user():
    with patch("appcore.llm_providers_dao.get_all", return_value={}):
        out = llm_providers_dao.load_user_providers(1)
    assert set(out.keys()) == {
        "openrouter", "doubao_llm", "gemini", "gemini_cloud", "elevenlabs",
    }


def test_save_user_provider_routes_extra_fields():
    with patch("appcore.llm_providers_dao.set_key") as m:
        llm_providers_dao.save_user_provider(
            1, "openrouter",
            {"key_value": "kkk", "base_url": "https://x"}
        )
    m.assert_called_once_with(1, "openrouter", "kkk", {"base_url": "https://x"})


def test_save_user_provider_omits_empty_extra():
    with patch("appcore.llm_providers_dao.set_key") as m:
        llm_providers_dao.save_user_provider(
            1, "gemini_cloud", {"key_value": "kkk"},
        )
    m.assert_called_once_with(1, "gemini_cloud", "kkk", None)


def test_save_user_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        llm_providers_dao.save_user_provider(1, "nonexistent", {"key_value": "k"})


def test_save_global_provider_volc_asr():
    with patch("appcore.llm_providers_dao.set_setting") as m:
        llm_providers_dao.save_global_provider("volc_asr", {"api_key": "kkk"})
    m.assert_called_once_with("provider.volc_asr.api_key", "kkk")


def test_save_global_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown global provider"):
        llm_providers_dao.save_global_provider("nope", {"api_key": "k"})


def test_load_global_providers_reads_system_settings():
    with patch("appcore.llm_providers_dao.get_setting", return_value="global-key"):
        out = llm_providers_dao.load_global_providers()
    assert out == {"volc_asr": {"api_key": "global-key"}}


def test_user_level_providers_definition_has_gemini_cloud():
    """回归：确保 Vertex 独立 provider 注册（按勘误表覆盖 8）。"""
    codes = [c for c, _, _ in llm_providers_dao.USER_LEVEL_PROVIDERS]
    assert "gemini_cloud" in codes
    assert "gemini" in codes
    assert "elevenlabs" in codes
