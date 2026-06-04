from __future__ import annotations

from pathlib import Path


def test_task_center_readiness_cards_render_title_manual_confirm_button():
    template = Path("web/templates/tasks_list.html").read_text(encoding="utf-8")

    assert "tcRenderManualConfirmButton" in template
    assert "tc-manual-confirm-pill" in template
    assert "tcConfirmChildStep" in template
    assert "/tasks/api/child/' + taskId + '/steps/' + encodeURIComponent(stepKey) + '/confirm" in template
    assert "tcRenderManualConfirmButton(taskId, check)" in template
