"""Unit tests for translation quality assessment scoring + verdict mapping."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pipeline import translation_quality as tq


def _llm_response(payload):
    return {"text": json.dumps(payload), "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_compute_score_arithmetic_mean():
    dims = {"semantic_fidelity": 80, "completeness": 90, "naturalness": 85}
    assert tq._compute_score(dims) == 85  # (80+90+85)/3 = 85


def test_compute_score_rounds_half_to_int():
    dims = {"a": 70, "b": 80, "c": 81}
    # (70+80+81)/3 = 77.0
    assert tq._compute_score(dims) == 77


def test_verdict_recommend_when_both_high():
    assert tq._verdict(85, 85) == "recommend"
    assert tq._verdict(100, 90) == "recommend"


def test_verdict_usable_when_both_above_70():
    assert tq._verdict(70, 80) == "usable_with_minor_issues"
    assert tq._verdict(84, 84) == "usable_with_minor_issues"


def test_verdict_needs_review_when_in_60_70():
    assert tq._verdict(65, 90) == "needs_review"
    assert tq._verdict(90, 60) == "needs_review"


def test_verdict_recommend_redo_when_below_60():
    assert tq._verdict(59, 90) == "recommend_redo"
    assert tq._verdict(90, 50) == "recommend_redo"


def test_verdict_boundary_85_85_recommend():
    assert tq._verdict(85, 85) == "recommend"


def test_verdict_boundary_84_85_usable():
    # one side below 85 → drops to usable
    assert tq._verdict(84, 85) == "usable_with_minor_issues"


def test_verdict_boundary_69_70():
    assert tq._verdict(69, 70) == "needs_review"
    assert tq._verdict(70, 70) == "usable_with_minor_issues"


def test_verdict_boundary_59_60():
    assert tq._verdict(59, 70) == "recommend_redo"
    assert tq._verdict(60, 70) == "needs_review"


def test_assess_returns_full_payload():
    response = {
        "translation_dimensions": {
            "semantic_fidelity": 90,
            "completeness": 85,
            "naturalness": 80,
            "hook_strength": 75,
            "ending_integrity": 70,
        },
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 85},
        "translation_issues": ["minor"],
        "translation_highlights": ["clear"],
        "tts_issues": [],
        "tts_highlights": ["smooth"],
        "verdict_reason": "good"
    }
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response(response)):
        result = tq.assess(
            original_asr="Hola amigos",
            translation="Hi friends",
            tts_recognition="Hi friends here",
            source_language="es",
            target_language="en",
            task_id="t-1",
            user_id=1,
        )
    assert result["translation_score"] == 80  # (90+85+80+75+70)/5
    assert result["tts_score"] == 90          # (95+90+85)/3
    assert result["verdict"] == "usable_with_minor_issues"
    assert result["translation_dimensions"]["semantic_fidelity"] == 90
    assert result["translation_dimensions"]["hook_strength"] == 75
    assert result["translation_dimensions"]["ending_integrity"] == 70
    assert result["raw_response"] is not None
    assert result["_llm_debug_call"]["use_case_code"] == "translation_quality.assess"
    assert result["_llm_debug_call"]["request_payload"]["max_tokens"] == 1500


def test_assess_raises_on_malformed_response():
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response({"foo": "bar"})):
        try:
            tq.assess(
                original_asr="x", translation="y", tts_recognition="z",
                source_language="es", target_language="en",
                task_id="t-2", user_id=1,
            )
            assert False, "expected exception"
        except tq.AssessmentResponseInvalidError:
            pass


def test_response_format_requires_hook_and_ending_dimensions():
    schema = tq._response_format()["json_schema"]["schema"]
    dims = schema["properties"]["translation_dimensions"]

    assert dims["properties"]["hook_strength"] == {"type": "integer", "minimum": 0, "maximum": 100}
    assert dims["properties"]["ending_integrity"] == {"type": "integer", "minimum": 0, "maximum": 100}
    assert "hook_strength" in dims["required"]
    assert "ending_integrity" in dims["required"]


def test_system_prompt_mentions_hook_and_ending_dimensions():
    prompt = tq._system_prompt()

    assert "hook_strength" in prompt
    assert "3-second hook" in prompt
    assert "ending_integrity" in prompt
    assert "closing / CTA intent" in prompt


def test_missing_new_dimension_rejected():
    response = {
        "translation_dimensions": {
            "semantic_fidelity": 90,
            "completeness": 85,
            "naturalness": 80,
            "hook_strength": 75,
        },
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 85},
        "translation_issues": [],
        "translation_highlights": [],
        "tts_issues": [],
        "tts_highlights": [],
        "verdict_reason": "missing ending",
    }
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response(response)):
        with pytest.raises(tq.AssessmentResponseInvalidError):
            tq.assess(
                original_asr="x", translation="y", tts_recognition="z",
                source_language="es", target_language="en",
                task_id="t-3", user_id=1,
            )


def test_assess_appends_notes_to_user_prompt():
    response = {
        "translation_dimensions": {
            "semantic_fidelity": 90,
            "completeness": 85,
            "naturalness": 80,
            "hook_strength": 75,
            "ending_integrity": 70,
        },
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 85},
        "translation_issues": [],
        "translation_highlights": [],
        "tts_issues": [],
        "tts_highlights": [],
        "verdict_reason": "ok",
    }
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response(response)) as invoke:
        tq.assess(
            original_asr="src", translation="dst", tts_recognition="dst2",
            source_language="es", target_language="en",
            task_id="t-4", user_id=1,
            notes="NOTE: the final audio was tail-truncated, 2 sentences removed before export.",
        )

    messages = invoke.call_args.kwargs["messages"]
    assert messages[-1]["content"].rstrip().endswith(
        "NOTE: the final audio was tail-truncated, 2 sentences removed before export."
    )
