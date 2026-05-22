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


def test_fine_ai_button_checks_latest_before_starting_new_run():
    body = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    start = body.index("async function mkiFineAiEvaluateFromCard")
    end = body.index("function mkiEnsureImportedStatusIcon", start)
    click_handler = body[start:end]

    assert "await mkiFineAiOpenLatestOrStart(context);" in click_handler
    assert "await mkiFineAiStartRun(context);" not in click_handler
    assert "async function mkiFineAiOpenLatestOrStart(context)" in body
