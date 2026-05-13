from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    next_start = source.find("\n  function ", start + 1)
    return source[start:] if next_start == -1 else source[start:next_start]


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
    assert "function synthesizeAsrCleanArtifact" in scripts
    assert 'step === "asr_clean" && !artifact' in scripts
    assert "currentTask?.utterances_raw" in scripts
    assert "currentTask?.utterances" in scripts
    assert "function renderShotDecomposeArtifact" in scripts
    assert "function renderAvSyncAuditArtifact" in scripts
    assert "中文审计结论" in scripts
    assert "artifact.human_report" in scripts
    assert "specializedPreviewOwnsStep(step)" in scripts
    assert 'step === "separate" || step === "loudness_match"' in scripts


def test_omni_av_sync_audit_renderer_exposes_chinese_findings():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "readable_findings" in scripts
    assert "audit_timeline" in scripts
    assert "function renderAvSyncAuditTimeline" in scripts
    assert "逐段 ASR 审片表" in scripts
    assert "画面内容" in scripts
    assert "诊断意见" in scripts
    assert "中文审计结论" in scripts
    assert "function auditRecommendation" in scripts
    assert "音频太长" in scripts
    assert "重新生成音频" in scripts


def test_multi_av_sync_audit_renderer_is_table_only():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "function isAvSyncTableOnlyReport" in scripts
    assert "renderAvSyncAuditTimeline(artifact, { tableOnly: true })" in scripts
    assert "正常翻译/TTS" in scripts
    assert "视频画面" in scripts
    assert "av-sync-timeline-grid table-only" in scripts

    artifact_body = _function_body(scripts, "renderAvSyncAuditArtifact")
    table_only_start = artifact_body.index("if (isAvSyncTableOnlyReport(artifact))")
    table_only_branch = artifact_body[table_only_start:artifact_body.index("const summary", table_only_start)]
    assert "renderReadableAuditFindings" not in table_only_branch
    assert "renderAuditList" not in table_only_branch
    assert "human_report" not in table_only_branch
    assert "完整审计 JSON" not in table_only_branch
