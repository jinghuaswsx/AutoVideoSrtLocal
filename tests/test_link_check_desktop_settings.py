from __future__ import annotations


def test_load_runtime_config_returns_defaults_when_config_file_missing(tmp_path):
    from link_check_desktop import settings

    config = settings.load_runtime_config(root=tmp_path)

    assert config == {
        "base_url": settings.DEFAULT_BASE_URL,
        "api_key": settings.DEFAULT_API_KEY,
    }


def test_save_runtime_config_persists_values_next_to_executable_root(tmp_path):
    from link_check_desktop import settings

    settings.save_runtime_config(
        base_url="http://127.0.0.1:8891",
        api_key="demo-key",
        root=tmp_path,
    )

    loaded = settings.load_runtime_config(root=tmp_path)

    assert loaded == {
        "base_url": "http://127.0.0.1:8891",
        "api_key": "demo-key",
    }
