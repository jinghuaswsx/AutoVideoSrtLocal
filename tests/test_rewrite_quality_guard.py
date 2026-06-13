import json
from unittest.mock import patch

from pipeline.rewrite_quality_guard import assess_rewrite_candidate

KW = dict(source_full_text="src", reference_translation_text="ref",
          candidate_text="cand", target_lang="en", task_id="t1", user_id=1)


def _resp(payload):
    return {"text": json.dumps(payload), "usage": {}}


def test_pass_when_all_good():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 90, "hook_ok": True, "ending_ok": True, "issues": []})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is True and r["guard_error"] is False


def test_fail_on_low_fidelity():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 60, "hook_ok": True, "ending_ok": True, "issues": ["漏卖点"]})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is False and r["issues"] == ["漏卖点"]


def test_fail_on_broken_ending():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 95, "hook_ok": True, "ending_ok": False, "issues": ["结尾CTA丢失"]})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is False


def test_fail_on_broken_hook():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 95, "hook_ok": False, "ending_ok": True, "issues": ["首句不够钩"]})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is False


def test_fail_open_on_llm_error():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.side_effect = RuntimeError("boom")
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is True and r["guard_error"] is True


def test_fail_open_on_non_json():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = {"text": "not json at all", "usage": {}}
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is True and r["guard_error"] is True


def test_issues_capped_at_three():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 90, "hook_ok": True, "ending_ok": True,
             "issues": ["a", "b", "c", "d", "e"]})
        r = assess_rewrite_candidate(**KW)
    assert r["issues"] == ["a", "b", "c"]


def test_debug_call_attached():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 90, "hook_ok": True, "ending_ok": True, "issues": []})
        r = assess_rewrite_candidate(**KW)
    assert isinstance(r["_llm_debug_call"], dict)
    assert r["_llm_debug_call"]["use_case_code"] == "video_translate.rewrite_guard"
