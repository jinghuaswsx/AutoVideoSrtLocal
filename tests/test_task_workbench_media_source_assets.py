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
        SCRIPT.index("function _speedupArtifactUrl"):
        SCRIPT.index("function escapeTextarea")
    ]

    assert "const audioUrl = safeMediaSrc(_durationRoundFileUrl(round.round, 'tts_full_audio'));" in duration_block
    assert 'src="${escapeAttr(audioUrl)}"' in duration_block
    assert 'src="${audioUrl}"' not in duration_block

    assert "return safeMediaSrc(" in speedup_block
    assert 'href="${escapeAttr(url)}"' in speedup_block
    assert "playAudio('${escapeJs(safeGid)}','${escapeJs(url)}')" in speedup_block
    assert "const preUrl = _speedupArtifactUrl(tid, preRel);" in speedup_block
    assert "const postUrl = _speedupArtifactUrl(tid, postRel);" in speedup_block
    assert 'src="${escapeAttr(preUrl)}"' not in speedup_block
    assert 'src="${escapeAttr(postUrl)}"' not in speedup_block
    assert 'src="${preUrl}"' not in speedup_block
    assert 'src="${postUrl}"' not in speedup_block


def test_task_workbench_speedup_audio_uses_configured_artifact_path_route():
    speedup_block = SCRIPT[
        SCRIPT.index("function _speedupArtifactUrl"):
        SCRIPT.index("function escapeTextarea")
    ]

    assert "function _speedupArtifactUrl" in speedup_block
    assert "TASK_WORKBENCH_CONFIG.apiBase" in speedup_block
    assert "artifact-path?path=" in speedup_block
    assert "/tasks/${encodeURIComponent(tid)}/artifact?path=" not in speedup_block
    assert "const preUrl = _speedupArtifactUrl(tid, preRel);" in speedup_block
    assert "const postUrl = _speedupArtifactUrl(tid, postRel);" in speedup_block


def test_task_workbench_voice_list_avoids_inline_handlers_for_api_fields():
    voice_block = SCRIPT[
        SCRIPT.index("function renderVoiceList"):
        SCRIPT.index("function selectVoice")
    ]

    assert 'const voiceId = String(voice.id == null ? "" : voice.id);' in voice_block
    assert 'const gender = voice.gender === "female" ? "female" : "male";' in voice_block
    assert 'data-voice-id="${escapeHtml(voiceId)}"' in voice_block
    assert 'data-preview-url="${escapeHtml(previewUrl)}"' in voice_block
    assert 'list.querySelectorAll(".voice-item").forEach' in voice_block
    assert 'list.querySelectorAll(".voice-play-btn[data-preview-url]").forEach' in voice_block
    assert 'onclick="selectVoice(${voice.id})"' not in voice_block
    assert 'onclick="playVoicePreview(event,' not in voice_block
