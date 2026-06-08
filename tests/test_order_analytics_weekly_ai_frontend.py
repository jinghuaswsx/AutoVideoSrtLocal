from pathlib import Path


TEMPLATE = Path("web/templates/order_analytics.html")


def test_weekly_ai_data_quality_warnings_are_visible_in_template():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "weeklyAiDqDetails" in source
    assert "weeklyAiStabilityWarnings" in source
    assert "weeklyAiProductEvaluationWarnings" in source
    assert "weeklyAiQualityItems" in source
    assert "weeklyAiCandidateQualityNotes" in source


def test_weekly_ai_manual_generation_skip_message_is_rendered():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "skipped_sync" in source
    assert "本次同步生成已跳过逐产品 AI" in source
