from pathlib import Path


def test_medias_js_ai_evaluation_modal_shows_timer_result_and_timeout():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "const AI_EVALUATION_TIMEOUT_MS = 5 * 60 * 1000;" in script
    assert "function openAiEvaluationRequestModal(product)" in script
    assert "function setAiEvaluationModalLoading(modalState)" in script
    assert "function setAiEvaluationModalResult(modalState, data)" in script
    assert "function setAiEvaluationModalFailure(modalState, reason)" in script
    assert "正在请求中" in script
    assert "已请求 ${elapsed} 秒" in script
    assert "本次评估失败" in script
    assert "服务器没有返回" in script
    assert "function aiEvaluationErrorMessage(err)" in script
    assert "Unexpected end of JSON input" in script
    assert "window.setInterval(updateElapsed, 1000)" in script
    assert "window.setTimeout(() => {" in script
    assert "EvalCountryTable.render(detail)" in script
    assert "openAiEvaluationRequestModal(product || { id: pid })" in script
