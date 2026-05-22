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
