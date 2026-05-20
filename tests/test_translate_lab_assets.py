from pathlib import Path


SCRIPT = Path("web/static/translate_lab.js").read_text(encoding="utf-8")


def test_translate_lab_sanitizes_voice_preview_audio_sources():
    candidates_start = SCRIPT.index("function renderVoiceCandidates")
    candidates_block = SCRIPT[
        candidates_start:
        SCRIPT.index("document.addEventListener", candidates_start)
    ]
    play_block = SCRIPT[
        SCRIPT.index("function playVoicePreview"):
        SCRIPT.index("function renderVoiceConfirmed")
    ]
    confirmed_block = SCRIPT[
        SCRIPT.index("function renderVoiceConfirmed"):
        SCRIPT.index("function renderTranslateTts")
    ]

    assert "function safeMediaSrc(url)" in SCRIPT
    assert "var preview = safeMediaSrc(v.preview_local_url || v.preview_url || v.preview_audio || \"\");" in candidates_block
    assert "var safeUrl = safeMediaSrc(url);" in play_block
    assert "audio.src = safeUrl;" in play_block
    assert "var preview = safeMediaSrc(voice.preview_local_url || voice.preview_url || \"\");" in confirmed_block
    assert "audio.src = url;" not in play_block
    assert "escapeHtml(voice.preview_url)" not in confirmed_block


def test_translate_lab_create_redirect_encodes_task_id():
    start = SCRIPT.index('requestJson("/api/translate-lab"')
    submit_block = SCRIPT[
        start:
        SCRIPT.index('var syncBtn = $("#syncVoiceLibraryBtn");', start)
    ]

    assert 'window.location.href = "/translate-lab/" + encodeURIComponent(data.task_id || "");' in submit_block
    assert 'window.location.href = "/translate-lab/" + data.task_id;' not in submit_block
