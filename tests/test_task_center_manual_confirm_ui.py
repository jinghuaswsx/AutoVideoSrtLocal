from __future__ import annotations

import re
from pathlib import Path


def test_task_center_readiness_cards_render_title_manual_confirm_button():
    template = Path("web/templates/tasks_list.html").read_text(encoding="utf-8")

    assert "tcRenderManualConfirmButton" in template
    assert "tc-manual-confirm-pill" in template
    assert "tcConfirmChildStep" in template
    assert "/tasks/api/child/' + taskId + '/steps/' + encodeURIComponent(stepKey) + '/confirm" in template
    assert "tcRenderManualConfirmButton(taskId, check)" in template


def test_task_center_manual_confirm_button_sits_next_to_step_title():
    template = Path("web/templates/tasks_list.html").read_text(encoding="utf-8")

    readiness_head = re.search(r"\.tc-readiness-check-head\s*\{([^}]+)\}", template)
    product_link_head = re.search(r"\.tc-product-link-combo-check-head\s*\{([^}]+)\}", template)

    assert readiness_head is not None
    assert product_link_head is not None
    assert "justify-content:flex-start" in readiness_head.group(1)
    assert "justify-content:space-between" not in readiness_head.group(1)
    assert "justify-content:flex-start" in product_link_head.group(1)
    assert "justify-content:space-between" not in product_link_head.group(1)


def test_task_center_shows_final_push_confirmation_manual_step():
    template = Path("web/templates/tasks_list.html").read_text(encoding="utf-8")

    assert "final_push_confirmation" in template
    assert "最终推送人工确认" in template
    assert "String(check.key || '') === 'final_push_confirmation'" in template
