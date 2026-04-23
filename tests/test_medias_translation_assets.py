from pathlib import Path


def test_medias_list_template_contains_new_translation_modal():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert 'id="mtTranslateMask"' in template
    assert 'id="mtTabCreate"' in template
    assert 'id="mtTabTasks"' in template
    assert 'id="mtPreviewFrame"' in template
    assert 'id="mtSubtitleOverlay"' in template
    assert "medias_translation_tasks.js" in template
    assert "medias_translate_modal.js" in template


def test_medias_translate_modal_script_exposes_new_orchestration_ui():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")

    assert "js-translation-tasks" in script
    assert "翻译任务管理" in script
    assert "window.MediasTranslationTasks.mount" in script
    assert "mtPreviewVideo" in script
    assert "mtSubtitleOverlay" in script
    assert "subtitle_position_y" in script


def test_medias_list_keeps_two_row_lang_coverage_layout():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const midpoint = Math.ceil(chips.length / 2);" in script
    assert 'class="oc-lang-row"' in script
    assert ".oc-lang-bar {" in template
    assert "flex-direction:column;" in template
    assert ".oc-lang-row {" in template
    assert "flex-wrap:nowrap;" in template


def test_medias_js_copy_translate_uses_validation_message():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function edValidateCopyTranslateSource(rawText)" in script
    assert "alert(sourceValidation.message);" in script


def test_medias_js_material_filename_validation_detects_localized_language():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function resolveMaterialFilenameLang(filename, fallbackLang)" in script
    assert "fn.includes('补充素材')" in script
    assert "return fn.includes(item.name_zh);" in script
    assert "await ensureLanguages();" in script
    assert "body: JSON.stringify({ filename: file.name, lang })" in script
