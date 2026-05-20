from pathlib import Path


SCRIPT = Path("web/static/voice_library.js").read_text(encoding="utf-8")


def test_voice_library_sanitizes_preview_audio_sources():
    card_block = SCRIPT[
        SCRIPT.index("function renderCard"):
        SCRIPT.index("function setPlayVisual")
    ]
    play_block = SCRIPT[
        SCRIPT.index("function togglePlay"):
        SCRIPT.index("audio.addEventListener")
    ]

    assert "function safeMediaSrc(url)" in SCRIPT
    assert "playBtn.dataset.url = safeMediaSrc(v.preview_local_url || v.preview_url);" in card_block
    assert "const url = safeMediaSrc(btn.dataset.url);" in play_block
    assert "audio.src = url;" in play_block
    assert "playBtn.dataset.url = v.preview_url || \"\";" not in card_block
