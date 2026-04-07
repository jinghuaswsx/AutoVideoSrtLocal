from appcore.api_keys import (
    DEFAULT_JIANYING_PROJECT_ROOT,
    get_translate_provider_preference,
    resolve_asr_key,
    resolve_jianying_project_root,
)


def test_resolve_jianying_project_root_defaults_when_user_has_no_setting(monkeypatch):
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda user_id, service: {})

    assert resolve_jianying_project_root(1) == DEFAULT_JIANYING_PROJECT_ROOT


def test_resolve_jianying_project_root_prefers_saved_user_setting(monkeypatch):
    custom_root = r"D:\JianyingDrafts"
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda user_id, service: {"project_root": custom_root},
    )

    assert resolve_jianying_project_root(1) == custom_root


def test_resolve_asr_key_prefers_saved_doubao_asr_key(monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "env-volc-key")
    monkeypatch.setattr(
        "appcore.api_keys.get_key",
        lambda user_id, service: "saved-doubao-key" if service == "doubao_asr" else None,
    )

    assert resolve_asr_key(1) == "saved-doubao-key"


def test_resolve_asr_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("VOLC_API_KEY", "env-volc-key")
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: None)

    assert resolve_asr_key(1) == "env-volc-key"


def test_get_translate_provider_preference_prefers_saved_key(monkeypatch):
    monkeypatch.setattr(
        "appcore.api_keys.get_key",
        lambda user_id, service: "doubao" if service == "translate_pref" else None,
    )
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda user_id, service: {})

    assert get_translate_provider_preference(1) == "doubao"


def test_get_translate_provider_preference_supports_legacy_extra(monkeypatch):
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: None)
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda user_id, service: {"provider": "doubao"} if service == "translate_preference" else {},
    )

    assert get_translate_provider_preference(1) == "doubao"


def test_get_translate_provider_preference_rejects_unknown_value(monkeypatch):
    monkeypatch.setattr(
        "appcore.api_keys.get_key",
        lambda user_id, service: "not-a-provider" if service == "translate_pref" else None,
    )
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda user_id, service: {})

    assert get_translate_provider_preference(1) == "openrouter"
