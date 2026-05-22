from pathlib import Path


def test_mk_selection_has_fine_ai_button_and_json_renderer():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "精细AI评估" in body
    assert "mkiFineAiEvaluateFromCard" in body
    assert "mkiFineAiRenderResult" in body
    assert "/ai-evaluation" in body
    assert "frontend.cards" in body
    assert "frontend.tables.country_overview" in body
    assert "frontend.charts.country_score_bar" in body
    assert "marked.parse" not in body
