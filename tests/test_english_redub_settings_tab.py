from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_template_has_english_redub_voice_match_control():
    html = (ROOT / "web/templates/settings.html").read_text(encoding="utf-8")

    assert "english_redub_voice_match_strategy" in html
    assert "旧音色匹配" in html
    assert "音色 + 语速匹配" in html


def test_settings_route_handles_omni_preset_post_for_english_redub():
    py = (ROOT / "web/routes/settings.py").read_text(encoding="utf-8")

    assert "_handle_omni_preset_post()" in py
    assert "english_redub_settings" in py
    assert "english_redub_voice_match_strategy" in py
