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
