from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mk_selection_xiao_languages_use_language_code_values():
    source = (ROOT / "web" / "templates" / "mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiXiaoLangCode" in source
    assert "var code = mkiXiaoLangCode(l);" in source
    assert 'value="\' + escapeHtml(code) + \'"' in source


def test_mk_selection_xiao_modal_uses_large_capsule_language_picker():
    source = (ROOT / "web" / "templates" / "mk_selection.html").read_text(encoding="utf-8")

    assert "mki-xiao-dialog" in source
    assert "width: min(720px, 94vw)" in source
    assert "mki-xiao-lang-pills" in source
    assert "mki-xiao-lang-pill" in source
    assert "mki-xiao-lang-check:checked + span" in source
    assert "background: var(--oc-accent)" in source
    assert '<input class="mki-xiao-lang-check" type="checkbox"' in source


def test_medias_task_bridge_opens_translate_modal_for_translate_action():
    source = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "action === 'translate'" in source
    assert ".js-translate[data-pid=" in source


def test_translate_modal_payload_carries_task_center_child_id():
    source = (ROOT / "web" / "static" / "medias_translate_modal.js").read_text(
        encoding="utf-8"
    )

    assert "MEDIAS_TASK_BRIDGE_TASK_ID" in source
    assert "task_center_task_id" in source
