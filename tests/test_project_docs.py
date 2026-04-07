from pathlib import Path


def test_env_example_uses_runtime_variable_names():
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "VOLC_API_KEY=" in content
    assert "TOS_ACCESS_KEY=" in content
    assert "TOS_SECRET_KEY=" in content
    assert "OPENROUTER_API_KEY=" in content
    assert "ELEVENLABS_API_KEY=" in content


def test_env_example_does_not_ship_placeholder_flask_secret():
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "FLASK_SECRET_KEY=" in content
    assert "change-me-in-production" not in content


def test_readme_codex_exists_with_key_operating_rules():
    content = Path("readme_codex.md").read_text(encoding="utf-8")

    assert "不要把真实 key 写回配置默认值" in content
    assert "timeline_manifest.json" in content
    assert "CapCut" in content


def test_server_runbook_does_not_store_live_passwords():
    content = Path("server.md").read_text(encoding="utf-8")

    assert "SSH password" not in content
    assert "Do not store production passwords" in content
