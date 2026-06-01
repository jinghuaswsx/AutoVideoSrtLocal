import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_medias_lang_ad_bar_harness(scenario):
    script_path = ROOT / "web" / "static" / "medias.js"
    harness = textwrap.dedent(
        r"""
        const fs = require("fs");
        const vm = require("vm");

        const script = fs.readFileSync(process.env.MEDIAS_JS, "utf8");

        function extract(start, end) {
          const startIdx = script.indexOf(start);
          const endIdx = script.indexOf(end, startIdx);
          if (startIdx < 0 || endIdx < 0) {
            throw new Error(`Unable to extract block: ${start}`);
          }
          return script.slice(startIdx, endIdx);
        }

        const scenario = JSON.parse(process.env.MEDIAS_LANG_AD_SCENARIO || "{}");
        const source = `
          let LANGUAGES = [
            { code: "en", name_zh: "英语" },
            { code: "de", name_zh: "德语" },
            { code: "fr", name_zh: "法语" }
          ];

          function escapeHtml(value) {
            return String(value == null ? "" : value)
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
          }

          ${extract("function langDisplayName(code)", "function resolveMaterialFilenameLang")}
          ${extract("function fmtAdRoas(value)", "function renderDeliveryStatus")}

          const html = renderProductLangAdBar(
            scenario.coverage || {},
            scenario.langAdSummary || {},
            scenario.adSummary || {}
          );
          html;
        `;

        const html = vm.runInNewContext(source, { scenario }, { timeout: 1000 });
        process.stdout.write(JSON.stringify({ html }));
        """
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as handle:
        handle.write(harness)
        harness_path = Path(handle.name)

    try:
        completed = subprocess.run(
            ["node", str(harness_path)],
            cwd=ROOT,
            env={
                **os.environ,
                "MEDIAS_JS": str(script_path),
                "MEDIAS_LANG_AD_SCENARIO": json.dumps(scenario, ensure_ascii=False),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    finally:
        harness_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise AssertionError(
            f"Node harness failed with code {completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)["html"]


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


def test_medias_translate_modal_subtitle_position_matches_hard_subtitle_bottom_anchor():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")

    assert "subtitleOverlay.style.transform = 'translate(-50%, -100%)';" in script
    assert "subtitleOverlay.style.transform = 'translate(-50%, -50%)';" not in script


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
    assert ">强制回填</button>" in script
    assert "将把该图片任务中已成功的图片立即回填，并忽略失败图片；当前子项会被标记为已完成" in script
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


def test_medias_list_keeps_compact_lang_coverage_rows():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "oc-lang-line" in script
    assert "oc-lang-summary" in script
    assert "oc-lang-push-zero" in script
    assert "fmtAdSpend" in script
    assert "summary.ad_spend_usd" in script
    assert "总体ROAS" in script
    assert ".oc-lang-bar {" in template
    assert "flex-direction:column;" in template
    assert ".oc-lang-line {" in template


def test_medias_product_table_names_language_ad_column():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "<th>语种和投放情况</th>" in script
    assert "<th>语种覆盖</th>" not in script


def test_medias_product_lang_ad_bar_hides_english_without_pushed_materials():
    html = _run_medias_lang_ad_bar_harness(
        {
            "coverage": {
                "en": {"items": 1},
                "de": {"items": 1},
            },
            "langAdSummary": {
                "en": {"pushed_video_count": 0},
                "de": {"pushed_video_count": 1},
            },
        }
    )

    assert "英 (EN)" not in html
    assert "德 (DE)" in html

    html = _run_medias_lang_ad_bar_harness(
        {
            "coverage": {
                "en": {"items": 1},
            },
            "langAdSummary": {
                "en": {"pushed_video_count": 2},
            },
        }
    )

    assert "英 (EN)" in html
    assert '推送 <strong class="oc-lang-push-count">2</strong>' in html


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


def test_medias_list_product_id_has_inline_copy_button():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert ".oc-product-id-cell" in template
    assert ".oc-product-id-copy" in template
    assert 'id="ic-copy"' in template
    assert "function copyProductCode(btn)" in script
    assert 'class="mono wrap oc-product-id-cell"' in script
    assert 'class="oc-product-id-main"' in script
    assert 'class="oc-btn text sm oc-product-id-copy"' in script
    assert 'data-product-code="${escapeHtml(productCode)}"' in script
    assert "${icon('copy', 12)}" in script
    assert "grid.querySelectorAll('.oc-product-id-copy').forEach" in script
    assert "copyProductCode(b)" in script
    assert "copyText(code)" in script
    assert "flashCopiedButton(btn)" in script


def test_medias_edit_items_render_translation_source_badge():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "function itemSourceLabel(it)" in script
    assert 'class="vsource"' in script
    assert ".oc-vitem .vsource" in template


def test_medias_edit_items_render_history_versions_entry_and_modal_endpoints():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'data-act="history"' in script
    assert "function edOpenItemHistory(itemId)" in script
    assert "function edDeleteItemVersion(versionId, itemId)" in script
    assert "`/medias/api/items/${itemId}/versions`" in script
    assert "`/medias/api/item-versions/${versionId}`" in script
    assert "await fetchJSON('/medias/api/items/' + itemId" in script
    assert "历史版本" in script


def test_medias_edit_item_filename_uses_validation_modal_and_two_line_layout():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert 'class="vname-text"' in script
    assert 'class="oc-input sm vname-input"' in script
    assert ".oc-vitem .vname-text" in template
    assert "min-height:calc(1.45em * 2);" in template
    assert ".oc-vitem .vname-edit-actions" in template
    assert "showFilenameErrorModal(" in script
    assert "e.error === 'filename_invalid'" in script
    assert "e.suggested_filename" in script


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


def test_medias_js_material_filename_validation_allows_supplement_slot_letters():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const LOCALIZED_SUPPLEMENT_MARKER = '-原素材-补充素材';" in script
    assert "/^[A-Ga-g]?\\(/" in script
    assert "补充素材 后只能接 A-G 字母或直接接半角括号" in script


def test_medias_js_material_filename_validation_allows_any_no_space_assignment_tail():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const LOCALIZED_ASSIGNMENT_TAIL_RE = /^(.*\\))-(\\S+)-蔡靖华\\.mp4$/;" in script
    assert "-顾倩multi补拍A-蔡靖华.mp4" in script


def test_medias_js_material_filename_validation_accepts_translated_material_pattern():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const LOCALIZED_TRANSLATED_MARKER = '-原素材-小语种翻译素材';" in script
    assert "20260401陈兆阳-蔡靖华.mp4" in script


def test_medias_js_material_filename_validation_checks_spaces_before_localized_patterns():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    validate_block = script[
        script.index("function validateMaterialFilename"):
        script.index("function assertMaterialFilenameOrAlert")
    ]

    no_space_pos = validate_block.index("const noSpaceErrors = validateFilenameNoSpaces(fn);")
    translated_pos = validate_block.index("if (fn.includes(LOCALIZED_TRANSLATED_MARKER))")
    supplement_pos = validate_block.index("const assignmentTailMatch = fn.match(LOCALIZED_ASSIGNMENT_TAIL_RE);")
    assert no_space_pos < translated_pos
    assert no_space_pos < supplement_pos


def test_medias_js_video_card_shows_task_link_under_source():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    render_start = script.index("function edRenderItems")
    render_block = script[
        render_start:
        script.index("g.querySelectorAll('[data-item]", render_start)
    ]

    assert "function itemTaskLinkHtml(it)" in script
    assert "关联任务" in script
    assert "const taskHtml = itemTaskLinkHtml(it);" in render_block
    assert render_block.index("${sourceHtml}") < render_block.index("${taskHtml}")
    assert render_block.index("${taskHtml}") < render_block.index('<div class="vtabs">')


def test_medias_search_input_runs_live_search():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function scheduleLiveSearch()" in script
    assert "window.setTimeout(runLiveSearch, 250);" in script
    assert "kwInput.addEventListener('input', scheduleLiveSearch);" in script
    assert "searchBtn.addEventListener('click', () => runSearchNow({ syncUrl: true }));" in script


def test_medias_search_enter_updates_query_url():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function syncSearchQueryToAddressBar()" in script
    assert "url.searchParams.set('q', kw);" in script
    assert "url.searchParams.delete('q');" in script
    assert "window.history.replaceState(null, '', url);" in script
    assert "runSearchNow({ syncUrl: true });" in script


def test_medias_translation_tasks_parent_meta_shows_raw_source_filenames():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "原始视频:" in script
    assert "task.raw_source_display_names" in script


def test_medias_raw_source_translate_choices_sanitize_media_src_protocols():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    choice_block = script[
        script.index("function renderTranslateRawSourceChoice"):
        script.index("function renderTranslateLanguageChoice")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const videoUrl = safeMediaSrc(it.video_url || '');" in choice_block
    assert "const posterUrl = safeMediaSrc(it.cover_url || '');" in choice_block
    assert 'src="${escapeHtml(videoUrl)}"' in choice_block
    assert 'poster="${escapeHtml(posterUrl)}"' in choice_block
    assert "const videoUrl = escapeHtml(it.video_url || '');" not in choice_block


def test_medias_translate_modal_sanitizes_raw_preview_media_src_protocols():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translate_modal.js").read_text(encoding="utf-8")
    raw_list_block = script[
        script.index("function renderRawSources"):
        script.index("function renderLanguages")
    ]
    preview_block = script[
        script.index("function applyRawPreview"):
        script.index("function collectExistingDefaults")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const coverUrl = safeMediaSrc(item.cover_url);" in raw_list_block
    assert 'src="${esc(coverUrl)}"' in raw_list_block
    assert "const videoUrl = safeMediaSrc(raw.video_url);" in preview_block
    assert "previewVideo.src = videoUrl;" in preview_block
    assert 'src="${esc(item.cover_url)}"' not in raw_list_block
    assert "previewVideo.src = raw.video_url;" not in preview_block
