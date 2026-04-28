from types import SimpleNamespace
from unittest.mock import patch

import pytest

from appcore import llm_providers_dao


def _cfg(api_key="", base_url="", model_id=""):
    return SimpleNamespace(api_key=api_key, base_url=base_url, model_id=model_id)


def test_load_user_providers_shape():
    rows = {
        "openrouter_text": _cfg("k1", "u1", "m1"),
        "elevenlabs_tts": _cfg("k2", "u2", ""),
    }
    with patch("appcore.llm_providers_dao.get_provider_config", side_effect=lambda code: rows.get(code)):
        out = llm_providers_dao.load_user_providers(1)

    assert out["openrouter"] == {"key_value": "k1", "base_url": "u1", "model_id": "m1"}
    assert out["elevenlabs"] == {"key_value": "k2", "base_url": "u2"}
    assert out["doubao_llm"] == {"key_value": "", "base_url": "", "model_id": ""}
    assert out["gemini"] == {"key_value": "", "model_id": ""}
    assert out["gemini_cloud"] == {"key_value": "", "model_id": ""}


def test_load_user_providers_returns_empty_for_new_user():
    with patch("appcore.llm_providers_dao.get_provider_config", return_value=None):
        out = llm_providers_dao.load_user_providers(1)
    assert set(out.keys()) == {
        "openrouter", "doubao_llm", "gemini", "gemini_cloud", "elevenlabs",
    }


def test_save_user_provider_routes_to_llm_provider_configs():
    with patch("appcore.llm_providers_dao.save_provider_config") as m:
        llm_providers_dao.save_user_provider(
            1, "openrouter",
            {"key_value": "kkk", "base_url": "https://x", "model_id": "model-x"},
        )
    m.assert_called_once_with(
        "openrouter_text",
        {"api_key": "kkk", "base_url": "https://x", "model_id": "model-x"},
        updated_by=1,
    )


def test_save_user_provider_omits_unsubmitted_fields():
    with patch("appcore.llm_providers_dao.save_provider_config") as m:
        llm_providers_dao.save_user_provider(
            1, "gemini_cloud", {"key_value": "kkk"},
        )
    m.assert_called_once_with(
        "gemini_cloud_text",
        {"api_key": "kkk"},
        updated_by=1,
    )


def test_save_user_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        llm_providers_dao.save_user_provider(1, "nonexistent", {"key_value": "k"})


def test_save_global_provider_volc_asr_routes_to_doubao_asr():
    with patch("appcore.llm_providers_dao.save_provider_config") as m:
        llm_providers_dao.save_global_provider("volc_asr", {"key_value": "kkk"}, user_id=7)
    m.assert_called_once_with(
        "doubao_asr",
        {"api_key": "kkk"},
        updated_by=7,
    )


def test_save_global_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown global provider"):
        llm_providers_dao.save_global_provider("nope", {"key_value": "k"})


def test_load_global_providers_reads_llm_provider_configs():
    with patch("appcore.llm_providers_dao.get_provider_config", return_value=_cfg("global-key", "", "res")):
        out = llm_providers_dao.load_global_providers()
    assert out == {"volc_asr": {"key_value": "global-key", "model_id": "res"}}


def test_user_level_providers_definition_has_gemini_cloud():
    codes = [c for c, _, _ in llm_providers_dao.USER_LEVEL_PROVIDERS]
    assert "gemini_cloud" in codes
    assert "gemini" in codes
    assert "elevenlabs" in codes
