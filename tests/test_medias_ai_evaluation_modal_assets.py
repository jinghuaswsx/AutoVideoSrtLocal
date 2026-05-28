from pathlib import Path


def test_medias_js_ai_evaluation_modal_shows_timer_result_and_timeout():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const AI_EVALUATION_TIMEOUT_MS = 5 * 60 * 1000;" in script
    assert "function openAiEvaluationRequestModal(product)" in script
    assert "function setAiEvaluationModalLoading(modalState)" in script
    assert "function setAiEvaluationModalResult(modalState, data)" in script
    assert "function setAiEvaluationModalFailure(modalState, reason)" in script
    assert "function aiEvaluationErrorMessage(err)" in script
    assert "Unexpected end of JSON input" in script
    assert "window.setInterval(updateElapsed, 1000)" in script
    assert "window.setTimeout(() => {" in script
    assert "EvalCountryTable.render(detail)" in script
    assert "openAiEvaluationRequestModal(product || { id: pid })" in script

    request_modal = script[
        script.index("function openAiEvaluationRequestModal"):
        script.index("function setAiEvaluationModalResult")
    ]
    assert "window.EvalCountryTable.openModal('', { title: titleText })" in request_modal
    assert "modalState.modal.classList.add('ect-modal--ai-evaluating')" in request_modal
    assert "oc-modal-mask" not in request_modal


def test_medias_js_ai_evaluation_modal_has_request_and_result_tabs():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "function renderAiEvaluationShell(modalState)" in script
    assert "function switchAiEvaluationTab(modalState, tab)" in script
    assert "data-ai-eval-tab=\"request\"" in script
    assert "data-ai-eval-tab=\"result\"" in script
    assert "data-ai-eval-panel=\"request\"" in script
    assert "data-ai-eval-panel=\"result\"" in script
    assert "AI_EVAL_REQUEST_PREVIEW_ENDPOINT" in script
    assert "function loadAiEvaluationRequestPreview(modalState, pid)" in script
    assert "function renderAiEvaluationRequestPreview(modalState)" in script
    assert "function renderAiEvaluationPromptSections(preview)" in script
    assert "function openAiEvaluationPayloadDetail(modalState)" in script
    assert "function copyAiEvaluationPayload(payload)" in script
    assert "request-payload" in script
    assert "navigator.clipboard.writeText" in script
    assert "<video controls" in script
    assert "<img src=\"" in script
    assert "fullPayloadUrl" in script


def test_medias_js_ai_evaluation_polls_country_progress():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "const AI_EVAL_STATUS_ENDPOINT" in script
    assert "function pollAiEvaluationStatus(modalState, pid, runId, onComplete)" in script
    assert "function renderAiEvaluationCountryProgress(modalState)" in script
    assert "data-ai-country-progress" in script
    assert "ect-ai-country-card" in script
    assert "排队中" in script
    assert "进行中" in script
    assert "已完成" in script
    assert "报错" in script


def test_medias_js_ai_evaluation_shows_summary_below_country_cards():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "function aiEvaluationResultDetail(data)" in script
    assert "function aiEvaluationNumberScore(rawScore)" in script
    assert "function aiEvaluationScoreTone(score)" in script
    assert "function aiEvaluationScorePercent(score)" in script
    assert "function renderAiEvaluationInlineSummary(modalState)" in script
    assert "JSON.parse(detail)" in script

    progress_block = script[
        script.index("function renderAiEvaluationCountryProgress"):
        script.index("function pollAiEvaluationStatus")
    ]
    assert "${renderAiEvaluationInlineSummary(modalState)}" in progress_block

    summary_block = script[
        script.index("function renderAiEvaluationInlineSummary"):
        script.index("function renderAiEvaluationCountryProgress")
    ]
    assert "data-ai-eval-summary" in summary_block
    assert "评估摘要" in summary_block
    assert "rawScore === null || rawScore === undefined || rawScore === ''" in summary_block
    assert "const overallTone = aiEvaluationScoreTone(score);" in summary_block
    assert "ect-ai-inline-score-card is-${overallTone}" in summary_block
    assert "ect-ai-inline-score-fill" in summary_block
    assert "const countryScore = aiEvaluationNumberScore(country.score);" in summary_block
    assert "const countryTone = aiEvaluationScoreTone(countryScore);" in summary_block
    assert "ect-ai-inline-country-summary is-${countryTone}" in summary_block

    result_block = script[
        script.index("function setAiEvaluationModalResult"):
        script.index("function setAiEvaluationModalLoading")
    ]
    assert "const detail = aiEvaluationResultDetail(data);" in result_block
    assert "modalState.evaluationDetail = detail;" in result_block
    assert "renderAiEvaluationCountryProgress(modalState)" in result_block


def test_medias_js_ai_evaluation_preview_uses_fixed_media_sizes():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert ".ect-ai-cover { width:180px; height:180px;" in script
    assert ".ect-ai-video { width:180px; height:320px;" in script
    assert ".ect-ai-video-name" in script
    assert "-webkit-line-clamp:3" in script


def test_medias_js_ai_evaluation_modal_panels_fill_available_height():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert ".ect-modal--ai-evaluating .ect-modal-body { display:flex; flex-direction:column; min-height:0;" in script
    assert ".ect-ai-panels { flex:1 1 auto; min-height:0; overflow:auto;" in script
    assert "height:calc(min(820px, 100vh - 48px) - 220px)" not in script
    assert "video.submitted_filename || video.filename || video.object_key || ''" in script


def test_medias_js_ai_evaluation_stops_timer_when_request_finishes():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "function stopAiEvaluationTimers(modalState)" in script
    assert "window.clearInterval(modalState.timer)" in script
    assert "modalState.timer = null" in script
    assert "window.clearTimeout(modalState.timeoutTimer)" in script
    assert "modalState.timeoutTimer = null" in script
    assert "if (modalState.done) return;" in script

    result_block = script[
        script.index("function setAiEvaluationModalResult"):
        script.index("function setAiEvaluationModalLoading")
    ]
    failure_block = script[
        script.index("function setAiEvaluationModalFailure"):
        script.index("function listingStatus")
    ]
    assert "stopAiEvaluationTimers(modalState)" in result_block
    assert "stopAiEvaluationTimers(modalState)" in failure_block


def test_medias_js_ai_evaluation_preview_sanitizes_product_url_href_protocols():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")

    preview_block = script[
        script.index("function renderAiEvaluationRequestPreviewToPanel"):
        script.index("function renderAiEvaluationPromptSections")
    ]

    assert "function safeExternalHref(url)" in script
    assert "const productUrl = safeExternalHref(product.product_url);" in preview_block
    assert 'href="${escapeHtml(productUrl)}"' in preview_block
    assert 'href="${escapeHtml(product.product_url)}"' not in preview_block


def test_medias_js_ai_evaluation_preview_sanitizes_media_src_protocols():
    script = Path("web/static/medias.js").read_text(encoding="utf-8")
    preview_block = script[
        script.index("function renderAiEvaluationRequestPreviewToPanel"):
        script.index("function renderAiEvaluationPromptSections")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const coverPreviewUrl = safeMediaSrc(cover.preview_url);" in preview_block
    assert "const videoPreviewUrl = safeMediaSrc(video.preview_url);" in preview_block
    assert 'src="${escapeHtml(coverPreviewUrl)}"' in preview_block
    assert 'src="${escapeHtml(videoPreviewUrl)}"' in preview_block
    assert 'src="${escapeHtml(cover.preview_url)}"' not in preview_block
    assert 'src="${escapeHtml(video.preview_url)}"' not in preview_block
