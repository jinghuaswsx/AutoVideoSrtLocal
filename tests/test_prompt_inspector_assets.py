from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_task_workbench_contains_prompt_inspector_modal_assets():
    template = (ROOT / "web/templates/_task_workbench.html").read_text(encoding="utf-8")
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (ROOT / "web/templates/_task_workbench_styles.html").read_text(encoding="utf-8")

    assert 'id="promptInspectorModal"' in template
    assert 'id="promptInspectorBody"' in template
    assert "function renderLlmDebugButtons" in scripts
    assert "function openPromptInspector" in scripts
    assert "function renderPromptInspectorPayload" in scripts
    assert ".llm-prompt-pill" in styles
    assert ".prompt-inspector-grid" in styles


def test_prompt_inspector_button_label_uses_step_label():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "${stepLabel(step)}提示词" in scripts
    assert "currentTask?.llm_debug_refs?.[step]" in scripts
    assert '"quality_assessment"' in scripts
    assert 'document.querySelector("#quality-assessment-card .step-name-row")' in scripts


def test_omni_pipeline_exposes_asr_clean_prompt_step():
    template = (ROOT / "web/templates/_task_workbench.html").read_text(encoding="utf-8")
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert 'id="step-asr_clean"' in template
    assert 'id="preview-asr_clean"' in template
    assert '"asr_clean"' in scripts
    assert 'asr_clean: "原文纯净化"' in scripts


def test_omni_pipeline_exposes_dynamic_prompt_steps():
    template = (ROOT / "web/templates/_task_workbench.html").read_text(encoding="utf-8")
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert 'id="step-asr_normalize"' in template
    assert 'id="step-shot_decompose"' in template
    assert 'id="step-av_sync_audit"' in template
    assert 'av_sync_audit: "音画同步审计"' in scripts


def test_omni_js_step_order_tracks_dynamic_pipeline_steps():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert (
        'const STEP_ORDER = ["extract", "asr", "separate", "shot_decompose", '
        '"asr_clean", "voice_match", "alignment", "translate", "tts", '
        '"av_sync_audit", "loudness_match", "subtitle", "compose", "export", "analysis"];'
    ) in scripts
    assert (
        'const MAIN_STEPS = ["extract", "asr", "separate", "shot_decompose", '
        '"asr_clean", "voice_match", "alignment", "translate", "tts", '
        '"av_sync_audit", "loudness_match", "subtitle", "compose", "export"];'
    ) in scripts


def test_omni_workbench_renders_special_artifacts_and_keeps_separation_panel():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "function renderAsrCleanArtifact" in scripts
    assert "function renderShotDecomposeArtifact" in scripts
    assert "function renderAvSyncAuditArtifact" in scripts
    assert "中文审计结论" in scripts
    assert "artifact.human_report" in scripts
    assert "specializedPreviewOwnsStep(step)" in scripts
    assert 'step === "separate" || step === "loudness_match"' in scripts


def test_omni_av_sync_audit_renderer_exposes_chinese_findings():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "readable_findings" in scripts
    assert "中文审计结论" in scripts
    assert "function auditRecommendation" in scripts
    assert "音频太长" in scripts
    assert "重新生成音频" in scripts
