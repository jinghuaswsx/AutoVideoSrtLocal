from pathlib import Path


SCRIPT = Path("web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")


def test_task_workbench_preview_media_sources_are_sanitized():
    render_block = SCRIPT[
        SCRIPT.index("function renderPreviewItem"):
        SCRIPT.index("function renderSegmentPreview")
    ]

    assert "function safeMediaSrc(url)" in SCRIPT
    assert "function escapeAttr(value)" in SCRIPT
    assert "const src = safeMediaSrc(item.url || artifactUrl(item.artifact, variantKey));" in render_block
    assert "const videoSrc = safeMediaSrc(item.url || artifactUrl(item.artifact, variantKey));" in render_block
    assert 'src="${escapeAttr(videoSrc)}"' in render_block
    assert 'src="${item.url || artifactUrl(item.artifact, variantKey)}"' not in render_block


def test_task_workbench_duration_round_audio_sources_are_sanitized():
    duration_block = SCRIPT[
        SCRIPT.index("function renderTtsDurationLog"):
        SCRIPT.index("function _fmtDuration")
    ]
    speedup_block = SCRIPT[
        SCRIPT.index("function renderSpeedupCard"):
        SCRIPT.index("function escapeTextarea")
    ]

    assert "const audioUrl = safeMediaSrc(_durationRoundFileUrl(round.round, 'tts_full_audio'));" in duration_block
    assert 'src="${escapeAttr(audioUrl)}"' in duration_block
    assert 'src="${audioUrl}"' not in duration_block

    assert "const preUrl  = safeMediaSrc(" in speedup_block
    assert "const postUrl = safeMediaSrc(" in speedup_block
    assert 'src="${escapeAttr(preUrl)}"' in speedup_block
    assert 'src="${escapeAttr(postUrl)}"' in speedup_block
    assert 'src="${preUrl}"' not in speedup_block
    assert 'src="${postUrl}"' not in speedup_block
