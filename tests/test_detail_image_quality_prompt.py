from __future__ import annotations

from appcore.image_translate_runtime import _EVAL_PROMPT

def test_detail_image_evaluation_prompt_exemptions():
    # Verify key exemptions are present in the evaluation prompt to ensure
    # that LLM quality checker behaves correctly.
    
    # 1. Physical English exemption must be documented
    assert "英文免检规则" in _EVAL_PROMPT
    assert "实物本身固有印刷英文" in _EVAL_PROMPT
    assert "货不对版" in _EVAL_PROMPT
    
    # 2. Marketing focus must be documented
    assert "后期" in _EVAL_PROMPT or "营销" in _EVAL_PROMPT
    
    # 3. Layout exemption must be documented
    assert "排版与布局完全免检" in _EVAL_PROMPT
    assert "has_layout_issue" in _EVAL_PROMPT
    assert "layout_issue_details" in _EVAL_PROMPT
    
    # 4. Score must depend strictly on translation quality
    assert "唯一决定" in _EVAL_PROMPT
