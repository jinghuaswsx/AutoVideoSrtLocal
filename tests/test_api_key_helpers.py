from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, resolve_jianying_project_root


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
