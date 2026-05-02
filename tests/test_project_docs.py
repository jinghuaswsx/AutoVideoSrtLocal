from pathlib import Path

import importlib


PRODUCTION_OPENAPI_KEY = "autovideosrt-materials-openapi"
HARDCODED_GOOGLE_AI_PREFIX = "AIzaSy"
HARDCODED_ADMIN_PASSWORD = "709709@"


def test_repository_does_not_embed_production_api_keys():
    scanned_roots = [
        Path("AutoPush"),
        Path("docs"),
        Path("link_check_desktop"),
        Path("tools/shopify_image_localizer"),
    ]
    suffixes = {".md", ".py", ".json", ".js", ".html"}

    offenders: list[str] = []
    for root in scanned_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in suffixes or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            if PRODUCTION_OPENAPI_KEY in content:
                offenders.append(str(path))

    root_config = Path("shopify_image_localizer_config.json")
    if root_config.is_file() and PRODUCTION_OPENAPI_KEY in root_config.read_text(encoding="utf-8"):
        offenders.append(str(root_config))

    assert offenders == []


def test_repository_does_not_embed_smoke_admin_password():
    scanned_roots = [
        Path("docs"),
        Path("scripts"),
        Path("tests/manual"),
    ]
    suffixes = {".md", ".py"}

    offenders: list[str] = []
    for root in scanned_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in suffixes or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            if HARDCODED_ADMIN_PASSWORD in content:
                offenders.append(str(path))

    assert offenders == []


def test_link_check_desktop_does_not_embed_google_ai_key():
    content = Path("link_check_desktop/settings.py").read_text(encoding="utf-8")

    assert HARDCODED_GOOGLE_AI_PREFIX not in content


def test_desktop_openapi_keys_default_empty_and_support_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LINK_CHECK_DESKTOP_API_KEY", raising=False)
    monkeypatch.delenv("LINK_CHECK_DESKTOP_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_IMAGE_LOCALIZER_API_KEY", raising=False)

    import link_check_desktop.settings as link_settings
    import tools.shopify_image_localizer.settings as shopify_settings

    link_settings = importlib.reload(link_settings)
    shopify_settings = importlib.reload(shopify_settings)

    assert link_settings.DEFAULT_API_KEY == ""
    assert link_settings.GEMINI_API_KEY == ""
    assert shopify_settings.DEFAULT_API_KEY == ""

    monkeypatch.setenv("LINK_CHECK_DESKTOP_API_KEY", "link-env-key")
    monkeypatch.setenv("LINK_CHECK_DESKTOP_GEMINI_API_KEY", "gemini-env-key")
    monkeypatch.setenv("SHOPIFY_IMAGE_LOCALIZER_API_KEY", "shopify-env-key")

    link_settings = importlib.reload(link_settings)
    shopify_settings = importlib.reload(shopify_settings)

    assert link_settings.load_runtime_config(tmp_path / "link")["api_key"] == "link-env-key"
    assert link_settings.GEMINI_API_KEY == "gemini-env-key"
    assert shopify_settings.load_runtime_config(tmp_path / "shopify")["api_key"] == "shopify-env-key"


def test_env_example_uses_runtime_variable_names():
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "TOS_ACCESS_KEY=" in content
    assert "TOS_SECRET_KEY=" in content
    assert "VOD_ACCESS_KEY=" in content
    assert "llm_provider_configs" in content
    assert "OPENROUTER_API_KEY=" not in content
    assert "OPENAPI_MEDIA_API_KEY=" not in content
    assert "ELEVENLABS_API_KEY=" not in content
    assert "APIMART_IMAGE_API_KEY=" not in content


def test_readme_codex_exists_with_key_operating_rules():
    content = Path("readme_codex.md").read_text(encoding="utf-8")

    assert "不要把真实 key 写回配置默认值" in content
    assert "timeline_manifest.json" in content
    assert "CapCut" in content
