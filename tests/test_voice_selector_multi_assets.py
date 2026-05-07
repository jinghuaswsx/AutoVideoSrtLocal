from pathlib import Path


SCRIPT = Path("web/static/voice_selector_multi.js").read_text(encoding="utf-8")


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
    assert "const previewUrl = safeMediaSrc(v.preview_url);" in row_block
    assert 'src="${escapeHtml(previewUrl)}"' in row_block
    assert "previewVideo.src = src;" not in SCRIPT
    assert "resultVideo.src = src;" not in result_block
    assert 'src="${escapeHtml(v.preview_url)}"' not in row_block


def test_voice_selector_multi_escapes_voice_library_error_response_text():
    assert "const detail = escapeHtml(await resp.text());" in SCRIPT
    assert 'listEl.innerHTML = `<div class="vs-loading">加载失败：${detail}</div>`;' in SCRIPT
    assert 'listEl.innerHTML = `<div class="vs-loading">加载失败：${await resp.text()}</div>`;' not in SCRIPT
