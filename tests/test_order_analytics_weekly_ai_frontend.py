from pathlib import Path


TEMPLATE = Path("web/templates/order_analytics.html")


def test_weekly_ai_data_quality_warnings_are_visible_in_template():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "weeklyAiDqDetails" in source
    assert "weeklyAiStabilityWarnings" in source
    assert "weeklyAiProductEvaluationWarnings" in source
    assert "weeklyAiQualityItems" in source
    assert "weeklyAiCandidateQualityNotes" in source


def test_weekly_ai_complete_generation_modal_and_auto_run_are_rendered():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "weeklyAiGenerateModal" in source
    assert "weeklyAiEnsureComplete" in source
    assert "/order-analytics/weekly-ai-analysis/ensure" in source
    assert "开始完整评估" in source
    assert "完整评估完成后展示稳定品 / 潜力品逐产品推进建议" in source
