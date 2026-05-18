from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_layout_contains_english_redub_menu_entry():
    layout = (ROOT / "web/templates/layout.html").read_text(encoding="utf-8")

    assert "has_permission('english_redub')" in layout
    assert "/english-redub" in layout
    assert "英语视频重新配音" in layout


def test_list_template_configures_english_redub_module():
    html = (ROOT / "web/templates/english_redub_list.html").read_text(encoding="utf-8")
    shared = (ROOT / "web/templates/multi_translate_list.html").read_text(encoding="utf-8")

    assert "英语视频重新配音" in html
    assert "module_kind = 'english_redub'" in html
    assert 'name="script_mode"' in shared
    assert 'value="original"' in shared
    assert 'value="rewrite"' in shared


def test_detail_shell_knows_english_redub_back_link():
    shell = (ROOT / "web/templates/_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "pipeline_kind|default('') == 'english_redub'" in shell
    assert "/english-redub" in shell


def test_permissions_register_english_redub():
    source = (ROOT / "appcore/permissions.py").read_text(encoding="utf-8")

    assert '"english_redub"' in source
    assert '"/english-redub"' in source
