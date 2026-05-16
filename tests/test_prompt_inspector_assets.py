from pathlib import Path
import subprocess


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


def test_task_workbench_step_title_renders_provider_model_tags():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (ROOT / "web/templates/_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "function collectStepModelTags" in scripts
    assert "function renderStepTitleModelTags" in scripts
    assert "currentTask?.step_model_tags?.[step]" in scripts
    assert "currentTask?.llm_debug_refs?.[step]" in scripts
    assert "ref.use_case" in scripts
    assert "ref.phase" in scripts
    assert "renderStepTitleModelTags(step, row)" in scripts
    assert ".step-title-model-tags" in styles
    assert ".step-title-model-tag" in styles


def test_translate_lab_step_title_renders_provider_model_tags():
    template = (ROOT / "web/templates/translate_lab_detail.html").read_text(encoding="utf-8")
    scripts = (ROOT / "web/static/translate_lab.js").read_text(encoding="utf-8")
    styles = (ROOT / "web/static/translate_lab.css").read_text(encoding="utf-8")

    assert "data-step-title-model-tags" in template
    assert "function renderLabStepTitleModelTag" in scripts
    assert "D.stepModelTags" in scripts
    assert 'item.querySelector(".lab-step-label")' in scripts
    assert ".lab-step-title-model-tags" in styles


def test_copywriting_step_title_renders_provider_model_tags():
    template = (ROOT / "web/templates/copywriting_detail.html").read_text(encoding="utf-8")
    scripts = (ROOT / "web/templates/_copywriting_scripts.html").read_text(encoding="utf-8")
    styles = (ROOT / "web/templates/_copywriting_styles.html").read_text(encoding="utf-8")

    assert "data-cw-step-title" in template
    assert "function renderCopywritingStepTitleModelTag" in scripts
    assert "renderCopywritingStepTitleModelTag(el, modelTag)" in scripts
    assert ".cw-step-title-model-tags" in styles
    assert ".cw-step-title-model-tag" in styles


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
        'const STEP_ORDER = ["extract", "asr", "separate", '
        '"asr_clean", "voice_match", "alignment", "shot_decompose", "translate", "tts", '
        '"av_sync_audit", "loudness_match", "subtitle", "compose", "export", "analysis"];'
    ) in scripts
    assert (
        'const MAIN_STEPS = ["extract", "asr", "separate", '
        '"asr_clean", "voice_match", "alignment", "shot_decompose", "translate", "tts", '
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
    assert 'artifact.mode === "report_only"' in scripts
    assert "Array.isArray(artifact.audit_timeline)" in scripts
    assert "逐段 ASR 审片表" in scripts
    assert "画面内容" in scripts
    assert "诊断意见" in scripts
    assert "中文审计结论" in scripts
    assert "function auditRecommendation" in scripts
    assert "音频太长" in scripts
    assert "重新生成音频" in scripts


def test_multi_av_sync_audit_renderer_is_table_only():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (ROOT / "web/templates/_task_workbench_styles.html").read_text(encoding="utf-8")

    assert "function isAvSyncTableOnlyReport" in scripts
    assert "renderAvSyncAuditTimeline(artifact, { tableOnly: true })" in scripts
    assert "正常翻译/TTS" in scripts
    assert "视频画面" in scripts
    assert "问题诊断" in scripts
    assert "function renderAvSyncAsrField" in scripts
    assert "asr_text_zh" in scripts
    assert "中文对照" in scripts
    assert "isAvSyncChineseSource" in scripts
    assert "同步评分" in scripts
    assert "整改建议" in scripts
    assert "av-sync-timeline-grid table-only" in scripts
    assert "function isAvSyncAuditProblemRow" in scripts
    assert 'av-sync-timeline-row table-only ${isIssue ? "is-issue" : ""}' in scripts
    assert 'diagnosis-field ${isIssue ? "is-issue" : ""}' in scripts
    assert ".av-sync-timeline-field.diagnosis-field.is-issue" in styles

    artifact_body = _function_body(scripts, "renderAvSyncAuditArtifact")
    table_only_start = artifact_body.index("if (isAvSyncTableOnlyReport(artifact))")
    table_only_branch = artifact_body[table_only_start:artifact_body.index("const summary", table_only_start)]
    assert "renderReadableAuditFindings" not in table_only_branch
    assert "renderAuditList" not in table_only_branch
    assert "human_report" not in table_only_branch
    assert "完整审计 JSON" not in table_only_branch


def test_av_sync_target_chinese_reference_does_not_hide_japanese_target():
    scripts = (ROOT / "web/templates/_task_workbench_scripts.html").read_text(encoding="utf-8")
    js = "\n".join([
        "const currentTask = { target_lang: 'ja' };",
        _function_body(scripts, "avSyncTargetChineseReference"),
        _function_body(scripts, "isAvSyncChineseTarget"),
        _function_body(scripts, "isMostlyChineseText"),
        "const row = {",
        "  target_text: 'ズボンが長くても、お直し代はもう払わないで。',",
        "  target_text_zh: '裤子太长也不要再付改裤脚的钱了。',",
        "};",
        "process.stdout.write(avSyncTargetChineseReference(row, row.target_text));",
    ])
    result = subprocess.run(
        ["node", "-e", js],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.stdout == "裤子太长也不要再付改裤脚的钱了。"
