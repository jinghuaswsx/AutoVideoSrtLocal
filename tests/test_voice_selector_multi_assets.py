from pathlib import Path


SCRIPT = Path("web/static/voice_selector_multi.js").read_text(encoding="utf-8")
TEMPLATE = Path("web/templates/_voice_selector_multi.html").read_text(encoding="utf-8")


def test_voice_selector_multi_sanitizes_preview_media_sources():
    preview_attach = SCRIPT[
        SCRIPT.index("function tryAttachPreviewVideo"):
        SCRIPT.index("function markVideoLoaded")
    ]
    payload_block = SCRIPT[
        SCRIPT.index("function applySubtitlePreviewPayload"):
        SCRIPT.index("async function loadSubtitlePreviewPayload")
    ]
    result_block = SCRIPT[
        SCRIPT.index("function loadResultVideo"):
        SCRIPT.index("function checkResultVideo")
    ]
    row_block = SCRIPT[
        SCRIPT.index("function rowHtml"):
        SCRIPT.index("function render(waitingProgress)")
    ]

    assert "function safeMediaSrc(url" in SCRIPT
    assert 'const videoSrc = safeMediaSrc(sourceVideo && sourceVideo.getAttribute("src"), { allowBlob: true });' in preview_attach
    assert "const videoSrc = safeMediaSrc(src, { allowBlob: true });" in SCRIPT
    assert 'const videoUrl = safeMediaSrc(data.video_url || "", { allowBlob: true });' in payload_block
    assert "const videoSrc = safeMediaSrc(src);" in result_block
    assert "const previewUrl = safeMediaSrc(v.preview_local_url || v.preview_url);" in row_block
    assert 'src="${escapeHtml(previewUrl)}"' in row_block
    assert "previewVideo.src = src;" not in SCRIPT
    assert "resultVideo.src = src;" not in result_block
    assert 'src="${escapeHtml(v.preview_url)}"' not in row_block


def test_voice_selector_multi_escapes_voice_library_error_response_text():
    assert "const detail = escapeHtml(await resp.text());" in SCRIPT
    assert 'listEl.innerHTML = `<div class="vs-loading">加载失败：${detail}</div>`;' in SCRIPT
    assert 'listEl.innerHTML = `<div class="vs-loading">加载失败：${await resp.text()}</div>`;' not in SCRIPT


def test_voice_selector_multi_uses_incremental_voice_paging():
    assert "const VOICE_PAGE_SIZE = 30;" in SCRIPT
    assert "function loadVoicePage(" in SCRIPT
    assert "page: String(page)" in SCRIPT
    assert "function maybeLoadMoreVoices(" in SCRIPT
    assert 'listEl.addEventListener("scroll", maybeLoadMoreVoices' in SCRIPT
    assert 'modalListEl.addEventListener("scroll", maybeLoadMoreVoices' in SCRIPT
    assert "fetchFullVoiceLibrary" not in SCRIPT
    assert "Math.ceil(total / VOICE_PAGE_SIZE)" not in SCRIPT


def test_voice_selector_multi_modal_renders_only_when_open():
    render_block = SCRIPT[
        SCRIPT.index("function render(waitingProgress)"):
        SCRIPT.index("function selectVoice(", SCRIPT.index("function render(waitingProgress)"))
    ]
    open_block = SCRIPT[
        SCRIPT.index("function openVoiceModal()"):
        SCRIPT.index("function closeVoiceModal()")
    ]

    assert "function renderVoiceModalIfOpen(waitingProgress)" in SCRIPT
    assert "renderVoiceModalIfOpen(waitingProgress);" in render_block
    assert "renderVoiceModal(waitingProgress);" not in render_block
    assert "renderVoiceModal();" in open_block
    assert "currentModalOpen()" in SCRIPT


def test_voice_selector_multi_exposes_explicit_voice_select_control():
    assert 'for="vs-voice-select"' in TEMPLATE
    assert 'id="vs-voice-select"' in TEMPLATE
    assert 'const voiceSelect = document.getElementById("vs-voice-select");' in SCRIPT
    assert "function syncVoiceSelectOptions(" in SCRIPT
    assert "function selectVoiceFromControl()" in SCRIPT
    assert 'voiceSelect.addEventListener("change", selectVoiceFromControl);' in SCRIPT


def test_voice_selector_multi_renders_speed_metadata():
    assert "function voiceSpeedMetaHtml(rec)" in SCRIPT
    assert "preview_words_per_second" in SCRIPT
    assert "speed_match_score" in SCRIPT
    assert "combined_score" not in SCRIPT
    assert "语速未维护，按音色排序" in SCRIPT
    assert "语速参考" in SCRIPT
    assert ".vs-row-speed" in TEMPLATE
    assert 'class="vs-speed-match-pill"' in SCRIPT
    assert '<span class="vs-speed-match-label">语速参考</span>' in SCRIPT
    assert '<span class="vs-speed-match-value">${escapeHtml(speedScore)}</span>' in SCRIPT
    assert ".vs-speed-match-pill" in TEMPLATE
    assert ".vs-speed-match-value" in TEMPLATE
    assert "font-size: 1.5em" in TEMPLATE


def test_voice_selector_multi_renders_independent_voice_match_rank_badge():
    rows_block = SCRIPT[
        SCRIPT.index("function rowsHtml"):
        SCRIPT.index("function renderRowsInto")
    ]

    assert "similarityRankMap" in SCRIPT
    assert "candidate.similarity_rank" in SCRIPT
    assert "const voiceMatchSimilarityRank = isRec" in rows_block
    assert 'class="vs-row-sim"' in rows_block
    assert 'class="vs-row-rank"' in rows_block
    assert "#${voiceMatchSimilarityRank}" in rows_block
    assert "${simBadge}${rankBadge}${aiRankBadge}" in rows_block
    assert ".vs-row-rank" in TEMPLATE


def test_voice_selector_multi_renders_llm_rank_badge_after_match_rank():
    rows_block = SCRIPT[
        SCRIPT.index("function rowsHtml"):
        SCRIPT.index("function renderRowsInto")
    ]

    assert "function voiceAiRankBadgeHtml(rec)" in SCRIPT
    assert "llm_rank" in SCRIPT
    assert "llm_reason_summary" in SCRIPT
    assert 'class="vs-row-ai-rank"' in SCRIPT
    assert "const aiRankBadge = isRec ? voiceAiRankBadgeHtml(rec) : \"\";" in rows_block
    assert "${simBadge}${rankBadge}${aiRankBadge}" in rows_block
    assert ".vs-row-ai-rank" in TEMPLATE


def test_voice_selector_multi_exposes_llm_rank_debug_modal():
    assert 'id="vs-ai-rank-debug-btn"' in TEMPLATE
    assert "大模型音色选择排名" in TEMPLATE
    assert 'id="vs-ai-rank-modal"' in TEMPLATE
    assert 'data-ai-rank-tab="request"' in TEMPLATE
    assert 'data-ai-rank-tab="result"' in TEMPLATE
    assert 'id="vs-ai-rank-request-visual"' in TEMPLATE
    assert 'id="vs-ai-rank-request-raw"' in TEMPLATE
    assert 'id="vs-ai-rank-result-visual"' in TEMPLATE
    assert 'id="vs-ai-rank-result-raw"' in TEMPLATE
    assert "let voiceAiRankDebug = null;" in SCRIPT
    assert "function openVoiceAiRankModal(" in SCRIPT
    assert "function renderVoiceAiRankDebugModal(" in SCRIPT
    assert "function renderVoiceAiRankRequestVisual(" in SCRIPT
    assert "function renderVoiceAiRankResultVisual(" in SCRIPT
    assert "voice_ai_rank_debug" in SCRIPT


def test_voice_selector_multi_keeps_ai_rank_controls_visible_and_adds_rerank_button():
    heading_block = TEMPLATE[
        TEMPLATE.index('<div class="vs-heading">'):
        TEMPLATE.index('</div>', TEMPLATE.index('<div class="vs-heading">'))
    ]

    assert 'id="vs-ai-rank-status-pill"' in heading_block
    assert 'id="vs-ai-rank-debug-btn"' in heading_block
    assert 'id="vs-ai-rank-run-btn"' in heading_block
    assert heading_block.index('id="vs-ai-rank-status-pill"') < heading_block.index('id="vs-ai-rank-debug-btn"')
    assert heading_block.index('id="vs-ai-rank-debug-btn"') < heading_block.index('id="vs-ai-rank-run-btn"')
    assert 'id="vs-ai-rank-debug-btn" hidden' not in heading_block
    assert 'id="vs-ai-rank-run-btn" hidden' not in heading_block
    assert 'class="vs-ai-rank-status-pill"' in TEMPLATE
    assert "function updateVoiceAiRankControls()" in SCRIPT
    assert 'const aiRankStatusPill = document.getElementById("vs-ai-rank-status-pill");' in SCRIPT
    assert "function updateVoiceAiRankStatusPill()" in SCRIPT
    assert "AI音色选择请求中." in SCRIPT
    assert "AI音色选择 已成功" in SCRIPT
    assert "AI音色选择 已失败" in SCRIPT
    assert "aiRankDebugBtn.hidden = false;" in SCRIPT
    assert "aiRankRunBtn.hidden = false;" in SCRIPT


def test_voice_selector_multi_reranks_current_gender_and_applies_cached_payloads():
    rematch_block = SCRIPT[
        SCRIPT.index("async function onGenderPillClick"):
        SCRIPT.index("launchBtn.addEventListener", SCRIPT.index("async function onGenderPillClick"))
    ]

    assert 'const aiRankRunBtn = document.getElementById("vs-ai-rank-run-btn");' in SCRIPT
    assert "function currentVoiceAiRankGender()" in SCRIPT
    assert "function applyVoiceAiRankPayload(data)" in SCRIPT
    assert "async function rerunVoiceAiRanking()" in SCRIPT
    assert "fetch(`${apiBase}/${taskId}/voice-ai-ranking`, {" in SCRIPT
    assert "body: JSON.stringify({ gender: currentVoiceAiRankGender() })," in SCRIPT
    assert "applyVoiceAiRankPayload(data);" in rematch_block


def test_voice_selector_multi_enables_manual_ai_ranking_for_multi_translate():
    supports_block = SCRIPT[
        SCRIPT.index("function supportsManualVoiceAiRanking"):
        SCRIPT.index("function updateVoiceAiRankControls")
    ]

    assert '"/api/english-redub"' in supports_block
    assert '"/api/multi-translate"' in supports_block
    assert '"/api/omni-translate"' not in supports_block


def test_voice_selector_multi_ai_rank_request_status_does_not_mutate_cards_on_failure():
    rerun_block = SCRIPT[
        SCRIPT.index("async function rerunVoiceAiRanking"):
        SCRIPT.index("function updateLaunchState")
    ]
    http_failure_block = rerun_block[
        rerun_block.index("if (!resp.ok)"):
        rerun_block.index("const data = await resp.json();")
    ]
    catch_block = rerun_block[
        rerun_block.index("} catch (err) {"):
        rerun_block.index("} finally {")
    ]
    success_block = rerun_block[
        rerun_block.index("const data = await resp.json();"):
        rerun_block.index("openVoiceAiRankModal")
    ]

    assert 'setVoiceAiRankRequestState("running");' in rerun_block
    assert 'setVoiceAiRankRequestState("failed");' in http_failure_block
    assert "applyVoiceAiRankPayload" not in http_failure_block
    assert "render();" not in http_failure_block
    assert 'setVoiceAiRankRequestState("failed");' in catch_block
    assert "applyVoiceAiRankPayload" not in catch_block
    assert "render();" not in catch_block
    assert "applyVoiceAiRankPayload(data);" in success_block
    assert "mergeVoiceItems(allItems, data.extra_items || [], loadedVoiceIds);" in success_block
    assert "render();" in success_block
    assert 'setVoiceAiRankRequestState("success");' in success_block


def test_voice_selector_multi_status_pill_only_reflects_current_request_state():
    display_block = SCRIPT[
        SCRIPT.index("function voiceAiRankDisplayState"):
        SCRIPT.index("function updateVoiceAiRankStatusPill")
    ]

    assert "voiceAiRankRequestState" in display_block
    assert "voiceAiRankStatus" not in display_block
    assert "isVoiceAiRankSuccessStatus" not in display_block
    assert "isVoiceAiRankFailureStatus" not in display_block


def test_voice_selector_multi_exposes_full_voice_modal():
    assert 'id="vs-open-modal-btn"' in TEMPLATE
    assert 'id="vs-voice-modal"' in TEMPLATE
    assert 'id="vs-modal-list"' in TEMPLATE
    assert 'role="dialog"' in TEMPLATE
    assert 'aria-modal="true"' in TEMPLATE
    assert 'const modalEl = document.getElementById("vs-voice-modal");' in SCRIPT
    assert "function openVoiceModal()" in SCRIPT
    assert "function closeVoiceModal()" in SCRIPT
    assert "function renderVoiceModal(waitingProgress)" in SCRIPT
    assert 'openModalBtn.addEventListener("click", openVoiceModal);' in SCRIPT


def test_voice_selector_multi_preserves_focus_and_scroll_during_refreshes():
    assert "function captureRenderState()" in SCRIPT
    assert "function restoreRenderState(state)" in SCRIPT
    assert "listScrollTop: listEl ? listEl.scrollTop : 0" in SCRIPT
    assert "modalScrollTop: modalListEl ? modalListEl.scrollTop : 0" in SCRIPT
    assert "activeVoiceId: activeVoiceElement ? activeVoiceElement.dataset.voiceId : null" in SCRIPT
    assert "restoreRenderState(renderState);" in SCRIPT
    assert "sessionStorage.setItem(RELOAD_STATE_KEY" in SCRIPT
    assert "function restoreReloadState()" in SCRIPT
    assert "window.scrollTo({ top: saved.scrollY || 0" in SCRIPT


def test_voice_selector_multi_freezes_after_voice_match_ready():
    page_block = SCRIPT[
        SCRIPT.index("async function loadVoicePage"):
        SCRIPT.index("async function loadLibrary")
    ]
    load_block = SCRIPT[
        SCRIPT.index("async function loadLibrary"):
        SCRIPT.index("function schedulePoll")
    ]

    assert "let voiceMatchReadyFrozen = false;" in SCRIPT
    assert "function markVoiceMatchReadyFrozen()" in SCRIPT
    assert "function shouldSkipAutomaticLibraryRefresh()" in SCRIPT
    assert "function shouldPollVoiceAiRanking()" in SCRIPT
    assert "if (shouldSkipAutomaticLibraryRefresh()) return;" in load_block
    assert "markVoiceMatchReadyFrozen();" in page_block
    assert "voiceMatchReadyFrozen = true;" in SCRIPT
    assert "schedulePoll();" in page_block
    assert "if (shouldPollVoiceAiRanking()) schedulePoll(6000);" in page_block
