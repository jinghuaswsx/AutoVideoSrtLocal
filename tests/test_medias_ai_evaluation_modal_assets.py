from pathlib import Path


def test_medias_js_ai_evaluation_modal_shows_timer_result_and_timeout():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const AI_EVALUATION_TIMEOUT_MS = 5 * 60 * 1000;" in script
    assert "function openAiEvaluationRequestModal(product)" in script
    assert "function setAiEvaluationModalLoading(modalState)" in script
    assert "function setAiEvaluationModalResult(modalState, data)" in script
    assert "function setAiEvaluationModalFailure(modalState, reason)" in script
    assert "function aiEvaluationErrorMessage(err)" in script
    assert "Unexpected end of JSON input" in script
    assert "window.setInterval(updateElapsed, 1000)" in script
    assert "window.setTimeout(() => {" in script
    assert "EvalCountryTable.render(detail)" in script
    assert "openAiEvaluationRequestModal(product || { id: pid })" in script

    request_modal = script[
        script.index("function openAiEvaluationRequestModal"):
        script.index("function setAiEvaluationModalLoading")
    ]
    assert "window.EvalCountryTable.openModal('', { title: titleText })" in request_modal
    assert "modalState.modal.classList.add('ect-modal--ai-evaluating')" in request_modal
    assert "oc-modal-mask" not in request_modal

    assert "ect-ai-request-card" in script
    assert "ect-ai-request-timer" in script
    assert "ect-ai-request-title" in script
    assert "ect-ai-request-desc" in script
    assert "ect-ai-request-error" in script
