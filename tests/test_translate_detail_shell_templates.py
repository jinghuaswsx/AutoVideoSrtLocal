from pathlib import Path


def test_multi_and_ja_detail_templates_include_shared_shell():
    root = Path(__file__).resolve().parents[1]
    multi = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    ja = (root / "web" / "templates" / "ja_translate_detail.html").read_text(encoding="utf-8")

    assert '{% include "_translate_detail_shell.html" %}' in multi
    assert '{% include "_translate_detail_shell.html" %}' in ja


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


def test_shared_shell_keeps_parent_task_copy_mode_specific():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "🔗 本任务由批次翻译创建" in shared
    assert "本任务由批次翻译创建 · <a href=\"/tasks/{{ state.parent_task_id }}\"" in shared


def test_task_workbench_config_exposes_detail_mode_and_selector_endpoints():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "detailMode:" in script
    assert "userDefaultVoiceApi:" in script


def test_voice_selector_script_mounts_for_ja_multi_and_av_sync_modes():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "{% if detail_mode in ('multi', 'ja', 'av_sync') %}" in shared
    assert "voice_selector_multi.js" in shared


def test_tts_generation_summary_is_rendered_in_duration_log():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "tts_generation_summary" in script
    assert "本任务用了" in script
    assert "次文本翻译" in script
    assert "轮语音生成" in script
    assert "次分段语音合成" in script
    assert "audio_segment_calls" in script
    assert "audio_rounds" in script
    assert "duration-generation-summary" in script
    assert ".duration-generation-summary" in styles
