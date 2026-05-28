from pathlib import Path


def test_pushes_template_contains_mk_id_column():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert "<th>mk_id</th>" in template
    assert "<th>产品负责人</th>" in template
    assert "<th>审核信息</th>" in template
    assert 'for="f-owner"' in template
    assert 'id="f-owner"' in template


def test_pushes_template_contains_audit_result_filter():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert 'for="f-audit-result"' in template
    assert 'id="f-audit-result"' in template
    assert '<option value="适合推广">适合推广</option>' in template
    assert '<option value="部分适合推广">部分适合推广</option>' in template
    assert '<option value="不适合推广">不适合推广</option>' in template


def test_pushes_template_contains_created_at_sort_control():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert 'for="f-sort"' in template
    assert 'id="f-sort"' in template
    assert '<option value="created_at_asc">创建时间升序</option>' in template
    assert '<option value="created_at_desc" selected>创建时间降序</option>' in template


def test_pushes_script_renders_product_link_and_copy_button():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "product_page_url" in script
    assert "product_owner_name" in script
    assert "product-owner-name" in script
    assert "product-name-line" in script
    assert "product-code-row" in script
    assert "mk_id" in script
    assert "data-copy-product-code" in script
    assert "data-copy-modal-product-code" in script
    assert "data-copy-payload-tag" in script
    assert "renderTagList" in script
    assert "navigator.clipboard" in script
    assert "document.execCommand('copy')" in script
    assert "renderAuditCell" in script
    assert "listing_status" in script
    assert "ai_evaluation_result" in script
    assert "ai_evaluation_detail" in script
    assert "AI评估详情" in script
    assert "loadOwners" in script
    assert "/medias/api/users/active" in script
    assert "owner_id" in script
    assert "f-owner" in script


def test_pushes_script_sanitizes_product_page_url_href_protocols():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    render_start = script.index("function renderRow(it)")
    render_row = script[
        render_start:
        script.index("async function load", render_start)
    ]

    assert "function safeExternalHref(url)" in script
    assert "const productPageUrl = safeExternalHref(it.product_page_url);" in render_row
    assert 'href="${escapeAttr(productPageUrl)}"' in render_row
    assert 'href="${escapeAttr(it.product_page_url)}"' not in render_row


def test_pushes_legacy_row_renderer_escapes_api_fields_if_reused():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    legacy = script[
        script.index("function renderRowLegacy"):
        script.index("function renderRow(it)")
    ]

    assert 'src="${escapeAttr(thumbUrl)}"' in legacy
    assert 'data-id="${escapeAttr(it.id)}"' in legacy
    assert "escapeHtml(it.product_name || '')" in legacy
    assert "escapeHtml(it.product_code || '')" in legacy
    assert "escapeHtml(it.display_name || it.filename || '')" in legacy
    assert "${it.product_name || ''}" not in legacy
    assert "${it.product_code || ''}" not in legacy


def test_pushes_row_renderers_sanitize_cover_media_src_protocols():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    legacy = script[
        script.index("function renderRowLegacy"):
        script.index("function renderRow(it)")
    ]
    current = script[
        script.index("function renderRow(it)"):
        script.index("async function load", script.index("function renderRow(it)"))
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const thumbUrl = safeMediaSrc(it.cover_url);" in legacy
    assert "const thumbUrl = safeMediaSrc(it.cover_url);" in current
    assert 'src="${escapeAttr(thumbUrl)}"' in legacy
    assert 'src="${escapeAttr(thumbUrl)}"' in current
    assert 'src="${escapeAttr(it.cover_url)}"' not in legacy
    assert 'src="${escapeAttr(it.cover_url)}"' not in current


def test_pushes_payload_and_quality_previews_sanitize_media_src_protocols():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    payload_view = script[
        script.index("function renderPayloadView"):
        script.index("function renderTagList")
    ]
    quality_cover = script[
        script.index("function renderQualityCoverPreview"):
        script.index("function renderQualityVideoPreview")
    ]
    quality_video = script[
        script.index("function renderQualityVideoPreview"):
        script.index("function renderQualitySidePanel")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "function previewMediaSrc(url)" in script
    assert "const src = safeMediaSrc(url);" in script
    assert "const coverSrc = previewMediaSrc(previewCoverUrl || v.image_url || null);" in payload_view
    assert "const videoSrc = previewMediaSrc(v.url);" in payload_view
    assert "src: coverSrc" in payload_view
    assert "src: videoSrc" in payload_view
    assert "poster: coverSrc" in payload_view
    assert "const coverSrc = previewMediaSrc(previewCoverUrl || (video && video.image_url) || '');" in quality_cover
    assert "const videoSrc = previewMediaSrc(video && video.url);" in quality_video
    assert "const posterSrc = previewMediaSrc(previewCoverUrl || (video && video.image_url) || '');" in quality_video
    assert "src: videoSrc" in quality_video
    assert "poster: posterSrc" in quality_video
    assert "src: video.url" not in quality_video


def test_pushes_script_persists_filters_pagination_and_sort_in_url():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "function applyUrlToFilters" in script
    assert "function syncUrlFromFilters" in script
    assert "window.addEventListener('popstate'" in script
    assert "params.set('status', statusSel.value);" in script
    assert "params.set('lang', langSel.value);" in script
    assert "params.set('product', product);" in script
    assert "params.set('keyword', keyword);" in script
    assert "params.set('owner_id', ownerSel ? ownerSel.value : '');" in script
    assert "params.set('audit_result', auditResultSel ? auditResultSel.value : '');" in script
    assert "params.set('date_from', df);" in script
    assert "params.set('date_to', dt);" in script
    assert "params.set('sort', sortSel.value || 'created_at_desc');" in script
    assert "params.set('page', String(state.page));" in script
    assert "history.replaceState" in script
    assert "history.pushState" in script


def test_pushes_css_styles_product_link_and_copy_button():
    css = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert ".product-link" in css
    assert ".product-name-line" in css
    assert ".product-copy-btn" in css
    assert ".product-code-row" in css
    assert ".pm-inline-copy-row" in css
    assert ".pm-tag-list" in css
    assert ".pm-copy-btn" in css
    assert ".audit-cell" in css
    assert ".audit-detail-pre" in css


def test_pushes_template_has_secondary_screen_table_shell_and_columns():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert 'class="push-table-shell"' in template
    assert 'class="push-table mobile-no-scroll"' in template
    assert 'class="push-col-thumb"' in template
    assert 'class="push-col-product"' in template
    assert 'class="push-col-ready"' in template
    assert 'class="push-col-action"' in template


def test_pushes_row_renderer_marks_cells_for_compact_layout():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    current = script[
        script.index("function renderRow(it)"):
        script.index("async function load", script.index("function renderRow(it)"))
    ]

    assert 'class="push-thumb-cell"' in current
    assert 'class="push-product-cell"' in current
    assert 'class="push-owner-cell"' in current
    assert 'class="push-item-cell"' in current
    assert 'class="push-lang-cell"' in current
    assert 'class="ready-cell push-ready-cell"' in current
    assert 'class="push-status-cell"' in current
    assert 'class="time push-time-cell"' in current
    assert 'class="push-action-cell"' in current


def test_pushes_css_has_secondary_screen_portrait_layout():
    css = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "docs/superpowers/specs/2026-05-20-xuanpin-secondary-screen-columns-design.md" in css
    assert "@media (min-width: 769px) and (orientation: portrait)" in css
    assert ".sidebar { transform: translateX(-100%); }" in css
    assert ".main-wrap { margin-left: 0; max-width: 100vw; }" in css
    assert "--push-table-min-w: 1100px;" in css
    assert ".push-table-shell { overflow-x: auto; }" in css
    assert ".ready-row { flex-wrap: wrap;" in css


def test_pushes_css_expands_ai_evaluation_detail_modal():
    css = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    assert ".ect-modal-overlay" in css
    assert "--oc-bg:            oklch(99%  0.004 230);" in css
    assert "--oc-border:        oklch(91%  0.012 230);" in css
    assert "--oc-fg:            oklch(22%  0.020 235);" in css
    assert "calc(100vh - 24px)" in css
    assert "min(1760px, calc(100vw - 48px))" in css
    assert ".ect-modal-body" in css
    assert "flex: 1 1 auto" in css
    assert "max-height: none" in css


def test_eval_country_table_expands_risk_section_but_keeps_meta_collapsed():
    script = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    extra_start = script.index("function extraSectionHtml")
    meta_start = script.index("function metaSectionHtml")
    render_start = script.index("function render")
    extra_section = script[extra_start:meta_start]
    meta_section = script[meta_start:render_start]

    assert 'return `<details class="ect-collapsible" open>' in extra_section
    assert 'return `<details class="ect-collapsible">' in meta_section
    assert 'return `<details class="ect-collapsible" open>' not in meta_section


def test_eval_country_table_sanitizes_product_url_href_protocols():
    script = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")
    product_link_start = script.index("function productLinkHtml")
    summary_start = script.index("function summaryHtml", product_link_start)
    product_link = script[product_link_start:summary_start]
    meta_start = script.index("function metaSectionHtml")
    modal_start = script.index("function render", meta_start)
    meta_section = script[meta_start:modal_start]

    assert "function safeExternalHref(url)" in script
    assert "const safeUrl = safeExternalHref(url);" in product_link
    assert "if (!safeUrl) return '';" in product_link
    assert 'href="${escapeHtml(safeUrl)}"' in product_link
    assert "const productUrl = safeExternalHref(detail.product_url);" in meta_section
    assert 'href="${escapeHtml(productUrl)}"' in meta_section

    assert 'href="${escapeHtml(url)}"' not in product_link
    assert "const productUrl = String(detail.product_url || '').trim();" not in meta_section


def test_eval_country_table_has_compact_push_detail_table_renderer():
    script = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    assert "function renderCompact" in script
    assert "function compactTableHtml" in script
    assert "slice(0, 8)" in script
    assert ".ect-compact-table" in script
    assert ".ect-compact-status" in script
    assert ".ect-compact-reason" in script
    assert "renderCompact: renderCompact" in script


def test_eval_country_table_prioritizes_and_highlights_current_language():
    script = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    assert "function movePrimaryCountryFirst" in script
    assert "opts.primaryLang" in script
    assert "ect-compact-primary" in script
    assert ".ect-compact-primary" in script
    assert "width: 272px" in script
    assert "color: var(--oc-accent)" in script


def test_push_modal_uses_compact_ai_evaluation_detail_table():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "function renderAuditDetailNode" in script
    assert "window.EvalCountryTable.renderCompact" in script
    assert "{ primaryLang: item.lang }" in script
    assert "audit-country-table-value" in script


def test_push_log_drawer_escapes_log_text_fields():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    start = script.index("async function viewLogs")
    end = script.index("// ---------- 绑定 ----------", start)
    view_logs = script[start:end]

    assert "escapeHtml(l.created_at)" in view_logs
    assert "escapeHtml(l.error_message)" in view_logs
    assert "escapeHtml(String(l.response_body || '').slice(0, 500))" in view_logs
    assert "${l.created_at}" not in view_logs
    assert "${l.error_message}" not in view_logs
    assert "${l.response_body.slice(0, 500)}" not in view_logs


def test_pushes_load_error_escapes_exception_message():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert 'tbody.innerHTML = `<tr><td colspan="${colspan}">加载失败: ${escapeHtml(e.message)}</td></tr>`;' in script
    assert 'tbody.innerHTML = `<tr><td colspan="${colspan}">加载失败: ${e.message}</td></tr>`;' not in script


def test_push_modal_can_manually_confirm_product_link_probe_failure():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    modal = script[
        script.index("function openPushModal"):
        script.index("// ---------- 历史抽屉 & 重置 ----------")
    ]

    assert "let manualLinkConfirmed = false;" in modal
    assert "人工确认链接正常" in modal
    assert "manual_link_confirmed=1" in modal
    assert "retryPayloadWithManualLinkConfirmation" in modal
    assert "manualLinkConfirmed = true;" in modal
    assert "JSON.stringify({ manual_link_confirmed: manualLinkConfirmed })" in modal
    assert "pm-link-confirm" in modal


def test_pushes_and_medias_use_shared_ai_evaluation_detail_modal():
    shared = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")
    pushes = Path("web/static/pushes.js").read_text(encoding="utf-8")
    medias = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "openModal:" in shared
    assert "function openAiEvaluationDetailModal" in shared
    assert "displayReason(c)" in shared
    assert "模型返回的原因不是中文" in shared
    assert "window.EvalCountryTable.openModal(" in pushes
    assert "window.EvalCountryTable.openModal(" in medias
    assert "function openAuditDetailModal" not in pushes
    assert "function openAiEvaluationDetail(product)" not in medias
    assert "aiEvalDetailMask" not in medias


def test_push_modal_can_rerun_material_ai_evaluation_in_place():
    pushes = Path("web/static/pushes.js").read_text(encoding="utf-8")
    open_modal_start = pushes.index("function openPushModal")

    modal = pushes[
        open_modal_start:
        pushes.index("function showResponse", open_modal_start)
    ]

    assert "'data-action': 'ai-reevaluate'" in modal
    assert "AI重评" in modal
    assert "retryMaterialAiEvaluation" in modal
    assert "updateAuditPanel" in modal
    assert "loadAiEvaluationRequestPreview(modalState, productId)" in modal
    assert "`/medias/api/products/${productId}/evaluate`" in modal
    assert "`/medias/api/products/${productId}`" in modal
    assert "setAiEvaluationModalResult(modalState, freshProduct || finalData.result || finalData)" in modal
    assert "state.items = state.items.map" in modal


def test_push_ai_evaluation_modal_polls_country_progress():
    pushes = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "const AI_EVAL_STATUS_ENDPOINT" in pushes
    assert "function pollAiEvaluationStatus(modalState, pid, runId, onComplete)" in pushes
    assert "function renderAiEvaluationCountryProgress(modalState)" in pushes
    assert "data-ai-country-progress" in pushes
    assert "ect-ai-country-card" in pushes
    assert "排队中" in pushes
    assert "进行中" in pushes
    assert "已完成" in pushes
    assert "报错" in pushes
    assert "function aiEvaluationResultDetail(data)" in pushes
    assert "function aiEvaluationNumberScore(rawScore)" in pushes
    assert "function aiEvaluationScoreTone(score)" in pushes
    assert "function aiEvaluationScorePercent(score)" in pushes
    assert "function renderAiEvaluationInlineSummary(modalState)" in pushes
    assert "JSON.parse(detail)" in pushes
    assert "data-ai-eval-summary" in pushes
    assert "评估摘要" in pushes
    assert "rawScore === null || rawScore === undefined || rawScore === ''" in pushes
    assert "const overallTone = aiEvaluationScoreTone(score);" in pushes
    assert "ect-ai-inline-score-card is-${overallTone}" in pushes
    assert "ect-ai-inline-score-fill" in pushes
    assert "const countryScore = aiEvaluationNumberScore(country.score);" in pushes
    assert "const countryTone = aiEvaluationScoreTone(countryScore);" in pushes
    assert "ect-ai-inline-country-summary is-${countryTone}" in pushes

    result_block = pushes[
        pushes.index("function setAiEvaluationModalResult"):
        pushes.index("function setAiEvaluationModalLoading")
    ]
    assert "const detail = aiEvaluationResultDetail(data);" in result_block
    assert "modalState.evaluationDetail = detail;" in result_block
    assert "renderAiEvaluationCountryProgress(modalState)" in result_block
