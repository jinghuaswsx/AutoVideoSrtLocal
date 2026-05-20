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
    assert "语速未维护，已按音色排序" in SCRIPT
    assert "语速匹配" in SCRIPT
    assert ".vs-row-speed" in TEMPLATE
    assert 'class="vs-speed-match-pill"' in SCRIPT
    assert '<span class="vs-speed-match-label">语速匹配</span>' in SCRIPT
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
    assert "${simBadge}${rankBadge}" in rows_block
    assert ".vs-row-rank" in TEMPLATE


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
    assert "if (shouldSkipAutomaticLibraryRefresh()) return;" in load_block
    assert "markVoiceMatchReadyFrozen();" in page_block
    assert "voiceMatchReadyFrozen = true;" in SCRIPT
    assert "schedulePoll();" in page_block
