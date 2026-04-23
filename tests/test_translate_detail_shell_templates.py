from pathlib import Path


def test_multi_and_ja_detail_templates_include_shared_shell():
    root = Path(__file__).resolve().parents[1]
    multi = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    ja = (root / "web" / "templates" / "ja_translate_detail.html").read_text(encoding="utf-8")

    assert '{% include "_translate_detail_shell.html" %}' in multi
    assert '{% include "_translate_detail_shell.html" %}' in ja


def test_shared_shell_contains_mode_specific_layout_rules():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "detail_mode == 'multi'" in shared
    assert "detail_mode == 'ja'" in shared
    assert '{% include "_voice_selector_multi.html" %}' in shared
    assert '{% include "_task_workbench.html" %}' in shared
