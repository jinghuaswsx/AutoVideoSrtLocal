import re
from pathlib import Path


def test_multi_and_ja_detail_templates_extend_shared_shell():
    root = Path(__file__).resolve().parents[1]
    multi = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    ja = (root / "web" / "templates" / "ja_translate_detail.html").read_text(encoding="utf-8")

    assert '{% extends "_translate_detail_shell.html" %}' in multi
    assert '{% extends "_translate_detail_shell.html" %}' in ja
    assert '{% include "_translate_detail_shell.html" %}' not in multi
    assert '{% include "_translate_detail_shell.html" %}' not in ja


def test_shared_shell_contains_mode_specific_layout_rules():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "detail_mode == 'multi'" in shared
    assert "detail_mode == 'ja'" in shared
    assert "detail_mode in ('multi', 'ja', 'av_sync')" in shared
    assert "detail_mode in ('multi', 'av_sync')" in shared
    assert '{% include "_voice_selector_multi.html" %}' in shared
    assert '{% include "_task_workbench.html" %}' in shared
    assert "#voicePanel { display: none !important; }" in shared
    assert "#configPanel { display: none !important; }" in shared


def test_shared_shell_does_not_label_source_language_as_auto_detected():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "（自动识别）" not in shared
    assert "原始语言识别中" not in shared
    assert "自动识别" not in shared
    assert "（自动识别）" not in script
    assert "原始语言识别中" not in script


def test_omni_detail_reselect_source_language_offers_all_manual_codes():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    for code in ("zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi"):
        assert f'<option value="{code}"' in template


def test_shared_shell_keeps_parent_task_copy_mode_specific():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "🔗 本任务由批次翻译创建" in shared
    assert "本任务由批次翻译创建 · <a href=\"/tasks/{{ state.parent_task_id }}\"" in shared


def test_task_workbench_config_exposes_detail_mode_and_selector_endpoints():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "detailMode:" in script


def test_omni_detail_shell_contains_preset_summary_slot():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "plugin_config_annotation" in shared
    assert "omni-preset-summary" in shared
    assert ".omni-preset-summary" in styles


def test_voice_selector_script_mounts_for_ja_multi_and_av_sync_modes():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "{% if detail_mode in ('multi', 'ja', 'av_sync') %}" in shared
    assert "voice_selector_multi.js" in shared


def test_voice_selector_launch_button_is_full_width_and_prominent():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_voice_selector_multi.html").read_text(encoding="utf-8")

    footer = re.search(r"\.vs-footer\s*\{(?P<body>.*?)\}", template, re.S)
    button = re.search(r"\.vs-launch-btn\s*\{(?P<body>.*?)\}", template, re.S)
    assert footer
    assert button
    assert 'id="vs-launch-btn" class="vs-launch-btn"' in template

    footer_css = re.sub(r"\s+", "", footer.group("body"))
    button_css = re.sub(r"\s+", "", button.group("body"))

    assert "align-items:stretch;" in footer_css
    assert "width:100%;" in button_css
    assert "min-width:0;" in button_css
    assert "min-height:72px;" in button_css
    assert "font-size:24px;" in button_css
    assert "font-weight:800;" in button_css
    assert "display:inline-flex;" in button_css
    assert "align-items:center;" in button_css
    assert "justify-content:center;" in button_css
    assert "box-sizing:border-box;" in button_css


def test_asr_normalize_card_moves_after_asr_without_reordering_other_cards():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    voice_selector = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")
    av_sync = (root / "web" / "templates" / "av_sync_detail.html").read_text(encoding="utf-8")

    assert "#pipelineCard .steps > #step-asr { order: -2; }" in shared
    assert "#pipelineCard .steps > #step-extract { order: -1; }" in shared
    assert "#pipelineCard .steps > #step-asr_normalize { order: -2; }" in shared
    assert 'const anchor = document.getElementById("step-asr");' in voice_selector
    assert "step-asr_normalize\") ||" not in voice_selector
    assert "{% set pipeline_kind = 'multi_translate' %}" in av_sync


def test_voice_separation_card_stays_after_audio_extract_before_tts_selector():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    separation = (root / "web" / "templates" / "_separation_card.html").read_text(encoding="utf-8")

    assert "#pipelineCard .steps > #step-extract { order: -1; }" in shared
    assert "#pipelineCard .steps > #step-separate { order: -1; }" in shared
    assert "voiceSel.parentNode.insertBefore(step, voiceSel)" not in separation
    assert "moveSeparateBeforeVoiceSelector" not in separation


def test_tts_generation_summary_is_rendered_in_duration_log():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "tts_generation_summary" in script
    assert "本任务用了" in script
    assert "次文本翻译" in script
    assert "轮语音生成" in script
    assert "次分段语音合成" in script
    assert "收敛后变速候选生成" in script
    assert "converged_speedup_audio_generations" in script
    assert "audio_segment_calls" in script
    assert "audio_rounds" in script
    assert "rewrite_word_tolerance_ratio" in script
    assert "rewrite_word_window" in script
    assert "±10%" not in script
    assert "duration-generation-summary" in script
    assert ".duration-generation-summary" in styles
    assert "final_converged_overshoot" in script
    assert "stage1_converged_postprocess" in script
    assert "采用变速音频参与视频合成" in script
    assert "保留收敛音频参与视频合成" in script


def test_tts_speedup_debug_shows_segment_assembly_truncation():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "assembly_truncated" in script
    assert "converged_segment_assembly_truncated" in script
    assert "best_pick_segment_assembly_truncated" in script
    assert "segment_assembly_truncated" in script
    assert "segment_assembly_pre_truncation_duration" in script
    assert "segment_assembly_post_truncation_duration" in script
    assert "segment_assembly_removed_duration" in script
    assert "segment_assembly_untrimmed_audio_path" in script
    assert "最优拼接裁剪" in script
    assert "最终用于合成" in script
    assert ".tts-speedup-trim-summary" in styles


def test_tts_speedup_players_render_as_readable_preview_cards():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")
    speedup_section = script[
        script.index("function _speedupArtifactUrl"):
        script.index("function escapeTextarea")
    ]
    render_block = script[
        script.index("function renderSpeedupCard"):
        script.index("function escapeTextarea")
    ]

    assert "function _speedupAudioPreview" in script
    assert "tts-speedup-player-card" in speedup_section
    assert "audio-play-btn" in speedup_section
    assert "audio-pause-btn" in speedup_section
    assert "打开文件" in speedup_section
    assert "_speedupAudioPreview(preGid, '变速前', preDur, preUrl)" in render_block
    assert "_speedupAudioPreview(postGid, '变速后', postDur, postUrl)" in render_block
    assert "<audio controls preload=\"none\"" not in render_block
    assert ".tts-speedup-players" in styles
    assert "grid-template-columns" in styles
    assert ".tts-speedup-player-card" in styles
    assert "minmax(260px, 1fr)" in styles


def test_sentence_reconcile_process_is_rendered_in_tts_duration_log():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "renderSentenceReconcileDurationLog" in script
    assert "mode === 'sentence_reconcile'" in script
    assert "句级时长收敛（Sentence Reconcile）" in script
    assert "rewrite_skip_reason" in script


def test_shot_char_limit_translate_process_has_legacy_state_fallback():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "buildTranslateArtifactFromTask" in script
    assert "buildAsrPrimaryShotTranslationRows" in script
    assert "translation.source_text" in script
    assert "shot_context" in script
    assert 'item.type === "shot_translation_summary"' in script
    assert 'item.type === "shot_translations"' in script
    assert "镜头级翻译过程" in script
    assert ".shot-translation-grid" in styles
