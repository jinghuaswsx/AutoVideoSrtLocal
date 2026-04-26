"""Unit tests for translation quality assessment scoring + verdict mapping."""
from __future__ import annotations

import json
from unittest.mock import patch

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
        "translation_dimensions": {"semantic_fidelity": 90, "completeness": 85, "naturalness": 80},
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
    assert result["translation_score"] == 85  # (90+85+80)/3
    assert result["tts_score"] == 90          # (95+90+85)/3
    assert result["verdict"] == "recommend"
    assert result["translation_dimensions"]["semantic_fidelity"] == 90
    assert result["raw_response"] is not None


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
