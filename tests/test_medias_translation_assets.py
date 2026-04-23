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


def test_medias_translation_tasks_parent_title_shows_started_time():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "function fmtTaskStartTime(value)" in script
    assert 'class="mtt-card__start"' in script
    assert "启动 ${esc(fmtTaskStartTime(task.created_at))}" in script
    assert '<h3 class="mtt-card__title">批量翻译任务</h3>' in script


def test_medias_translation_tasks_action_labels_are_explicit():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert ">整个任务重新启动</button>" in script
    assert "重新启动整个批量任务" in script
    assert ">重跑失败项</button>" in script
    assert "只重跑失败或中断的子项" in script
    assert ">重跑此项</button>" in script
    assert "如果这一项是图片翻译，只会补跑其中失败或中断的图片" in script
    assert "单个重新启动" not in script


def test_medias_translation_tasks_polls_every_five_seconds_until_progress_complete():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "const POLL_INTERVAL_MS = 5000;" in script
    assert "function hasUnfinishedTasks(items)" in script
    assert "function isTaskComplete(task)" in script
    assert "Number(progress.done || 0) + Number(progress.skipped || 0)" in script
    assert "return (items || []).some((task) => !isTaskComplete(task));" in script
    assert "window.setInterval(refresh, POLL_INTERVAL_MS)" in script
    assert "window.clearInterval(timer)" in script


def test_medias_translate_modal_matches_shared_subtitle_size_options():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")

    for size in ("8", "10", "12", "14", "16", "18", "20", "22", "24", "28"):
        assert f'data-size="{size}"' in template

    assert 'data-size="10" class="active"' in template
    assert "videoSize: 10" in script
    assert "state.videoSize = 10;" in script
    assert "Number(button.dataset.size) || 10" in script


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


def test_medias_list_missing_english_cover_warning_has_no_red_row_edge():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "tr.oc-row-warn { box-shadow" not in template
    assert "inset 3px 0 0 0 var(--oc-danger)" not in template


def test_medias_js_copy_translate_uses_validation_message():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function edValidateCopyTranslateSource(rawText)" in script
    assert "alert(sourceValidation.message);" in script


def test_medias_edit_items_render_translation_source_badge():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "function itemSourceLabel(it)" in script
    assert 'class="vsource"' in script
    assert ".oc-vitem .vsource" in template


def test_medias_translate_modal_marks_completed_raw_language_pairs():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")

    assert "function rawHasTranslation(raw, langCode)" in script
    assert "function selectedRawTranslationStats(langCode)" in script
    assert "mt-choice--done" in script
    assert "input.disabled" in script


def test_medias_translate_modal_requires_manual_target_language_selection():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")

    assert "默认不勾选，请手动选择需要翻译的语种" in template
    assert "state.selectedLangs = new Set();" in script
    assert "state.selectedLangs = new Set(missing);" not in script


def test_medias_js_material_filename_validation_detects_localized_language():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function resolveMaterialFilenameLang(filename, fallbackLang)" in script
    assert "fn.includes('补充素材')" in script
    assert "return fn.includes(item.name_zh);" in script
    assert "await ensureLanguages();" in script
    assert "body: JSON.stringify({ filename: file.name, lang })" in script


def test_medias_search_input_runs_live_search():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function scheduleLiveSearch()" in script
    assert "window.setTimeout(runLiveSearch, 250);" in script
    assert "kwInput.addEventListener('input', scheduleLiveSearch);" in script
    assert "searchBtn.addEventListener('click', runSearchNow);" in script


def test_medias_translation_tasks_parent_meta_shows_raw_source_filenames():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "原始视频:" in script
    assert "task.raw_source_display_names" in script
