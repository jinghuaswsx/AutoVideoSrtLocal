from pathlib import Path


def test_mk_selection_has_fine_ai_button_and_json_renderer():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "精细AI评估" in body
    assert "mkiFineAiEvaluateFromCard" in body
    assert "mkiFineAiRenderResult" in body
    assert "/ai-evaluation" in body
    assert "frontend.cards" in body
    assert "frontend.tables.country_overview" in body
    assert "frontend.charts.country_score_bar" in body
    assert "marked.parse" not in body


def test_fine_ai_button_checks_latest_before_starting_new_run():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    start = body.index("async function mkiFineAiEvaluateFromCard")
    end = body.index("function mkiEnsureImportedStatusIcon", start)
    click_handler = body[start:end]

    assert "await mkiFineAiOpenLatestOrStart(context);" in click_handler
    assert "await mkiFineAiStartRun(context);" not in click_handler
    assert "async function mkiFineAiOpenLatestOrStart(context)" in body


def test_fine_ai_external_button_checks_archive_before_starting_new_run():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    start = body.index("async function mkiFineAiOpenLatestOrStart(context)")
    end = body.index("function mkiFineAiApplyResolvedProductLink", start)
    open_latest = body[start:end]
    load_start = body.index("async function mkiFineAiLoadLatest(context")
    load_end = body.index("async function mkiFineAiOpenLatestOrStart(context)", load_start)
    load_latest = body[load_start:load_end]

    assert "context.externalProductLink" not in open_latest.split("context.loadingLatest = true;", 1)[0]
    assert "await mkiFineAiStartRun(context);" not in open_latest.split("const loaded = await mkiFineAiLoadLatest", 1)[0]
    assert "params.set('product_link', context.externalProductLink || context.productLink || '')" in body
    assert "params.set('card_video_path', context.cardVideoPath)" in body
    assert "mkiFineAiLatestEndpoint(context)" in load_latest
    assert "error.status = resp.status" in body
    assert "context.fineAiLatestError.status !== 404" in open_latest
    assert "未自动创建新任务" in open_latest


def test_fine_ai_button_uses_current_card_video_for_external_product_link():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "const canFineAiEvaluate = Boolean(existingProductId || (productLinkRaw && videoPath));" in body
    assert "${canFineAiEvaluate ? '' : 'disabled'}" in body
    assert "商品链接和当前视频存在即可进行精细 AI 评估" in body
    assert "商品链接或视频缺失，无法进行精细 AI 评估" in body
    assert "data-mki-fine-ai-video-path" in body
    assert "data-mki-fine-ai-video-url" in body
    assert "card_video_path: context.cardVideoPath || ''" in body
    assert "card_video_object_key" not in body
    assert "externalProductLink" in body
    assert "/xuanpin/api/fine-ai-evaluation" in body


def test_fine_ai_modal_renders_backend_progress_cards_and_logs():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "mkiFineAiRenderProgressHeader" in body
    assert "mkiFineAiRenderStepCards" in body
    assert "mkiFineAiRenderExecutionLog" in body
    assert "mki-fine-ai-progress-fill" in body
    assert "mki-fine-ai-step-card is-running" in body
    assert "mkiFineAiElapsedLabel" in body


def test_fine_ai_progress_renders_waiting_state_for_country_rate_limit():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")
    template = Path("web/templates/fine_ai_evaluation_detail.html").read_text(encoding="utf-8")

    assert "waiting: '等待中'" in body
    assert "waiting: '等待中'" in script
    assert ".mki-fine-ai-step-card.is-waiting" in body
    assert ".mki-fine-ai-status-pill.is-waiting" in body
    assert ".mki-fine-ai-step-card.is-waiting" in template
    assert ".mki-fine-ai-status-pill.is-waiting" in template


def test_fine_ai_startup_progress_has_live_timer_and_running_data_preparation():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "data-fine-ai-elapsed" in body
    assert "function mkiFineAiStartElapsedTicker" in body
    assert "function mkiFineAiStartingProgress" in body
    assert "正在检测商品链接可访问性" in body
    assert "const firstStepKey = context.externalProductLink ? 'product_link_check' : 'data_preparation';" in body
    assert "step.key === firstStepKey" in body
    assert "status: 'running'" in body
    assert "mkiFineAiStartElapsedTicker(context);" in body


def test_fine_ai_external_link_check_is_first_step_and_updates_card_link():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "product_link_check" in body
    assert "mkiFineAiApplyResolvedProductLink" in body
    assert "mkiFineAiApplyResolvedProductLink(context, resp.data && resp.data.link_check)" in body
    assert "mkiFineAiRenderLinkCheckFailure" in body
    assert "error.details = err.details" in body
    assert "mk_product_id: context.mkProductId || ''" in body
    assert "data-mki-mk-id" in body
    assert "progress.current_step = firstStepKey" in body
    assert "data-fine-ai-step" in body


def test_fine_ai_modal_has_standalone_page_button_and_status_mapping():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiFineAiDetailUrl" in body
    assert "function mkiFineAiOpenStandalonePage" in body
    assert "打开独立页" in body
    assert "mkiFineAiOpenStandalonePage(context)" in body
    assert "context.detailButton" in body
    assert "mkiFineAiRefreshDetailButton(context)" in body
    assert "function mkiFineAiEffectiveStatus" in body
    assert "completed_steps > 0" in body


def test_fine_ai_modal_context_copy_panel_shows_product_code_video_and_link():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiFineAiRenderContextCopyPanel" in body
    assert "function mkiFineAiContextRows" in body
    assert "data-mki-fine-ai-context-copy" in body
    assert "mkiFineAiRenderContextCopyPanel(context)" in body
    assert "mkiFineAiRenderResult(result, context)" in body
    assert "mki-fine-ai-context-row" in body
    assert "copyMkText(this.dataset.copyText, this)" in body


def test_fine_ai_standalone_context_copy_panel_uses_status_and_result_snapshot():
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")
    template = Path("web/templates/fine_ai_evaluation_detail.html").read_text(encoding="utf-8")

    assert "function renderContextCopyPanel" in script
    assert "function contextRows" in script
    assert "data-fine-ai-context-copy" in script
    assert "productSnapshot.product_code" in script
    assert "metadata.external_card_video" in script
    assert "renderContextCopyPanel(status)" in script
    assert "renderContextCopyPanel(result)" in script
    assert ".fine-ai-context-panel" in template
    assert ".fine-ai-context-copy-btn" in template


def test_fine_ai_standalone_failed_step_cards_can_confirm_and_rerun_country():
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")
    template = Path("web/templates/fine_ai_evaluation_detail.html").read_text(encoding="utf-8")

    assert "function canRerunStep" in script
    assert "data-fine-ai-step-rerun" in script
    assert "window.confirm" in script
    assert "markCountryStepRunning(code)" in script
    assert "正在重新请求 AI 评估" in script
    assert "config.rerun_url_template" in script
    assert "mki-fine-ai-step-actions" in template
    assert "mki-fine-ai-step-rerun" in template


def test_fine_ai_standalone_failed_country_rerun_is_not_blocked_by_run_status():
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")

    start = script.index("function canRerunStep")
    end = script.index("function renderStepRerunButton", start)
    block = script[start:end]

    assert "countryCodeFromStep(step)" in block
    assert "String(stepStatus || '').toLowerCase() === 'failed'" in block
    assert "terminalStatuses.includes" not in block
    assert "runStatus" not in block


def test_fine_ai_standalone_summary_groups_country_decisions_and_failed_reruns():
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")
    template = Path("web/templates/fine_ai_evaluation_detail.html").read_text(encoding="utf-8")

    assert "function renderCountryDecisionSummary" in script
    assert "fine-ai-decision-summary" in script
    assert "fine-ai-decision-group is-go" in script
    assert "fine-ai-decision-group is-test" in script
    assert "fine-ai-decision-group is-hold" in script
    assert "建议做" in script
    assert "先测试 / 需要考虑" in script
    assert "暂不做 / 需重跑或补数据" in script
    assert "data-fine-ai-summary-rerun" in script
    assert "renderSummaryRerunButton" in script
    assert ".fine-ai-decision-summary" in template
    assert ".fine-ai-decision-group" in template


def test_fine_ai_standalone_result_inlines_country_summary_into_summary_step():
    script = Path("web/static/js/fine_ai_evaluation_detail.js").read_text(encoding="utf-8")

    assert "renderProgress(result.progress || {}, result.status || '', result)" in script
    assert "resultForSummary" in script
    assert "step.key || '') === 'summary'" in script
    assert "renderCountryDecisionSummary(resultForSummary)" in script
