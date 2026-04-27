"""Unit tests for ASR same-language purification."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline import asr_clean


SAMPLE_ES_UTTS = [
    {"index": 0, "start_time": 0.0, "end_time": 2.0, "text": "Hola amigos, today vamos a hablar de"},
    {"index": 1, "start_time": 2.0, "end_time": 4.0, "text": "este producto que es 太棒了 increíble"},
]


def _ok_response(items):
    import json
    return {"text": json.dumps({"utterances": items}), "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_validator_accepts_clean_es_output():
    items = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert errors == []


def test_validator_rejects_length_mismatch():
    items = [{"index": 0, "text": "Hola"}]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("length" in e for e in errors)


def test_validator_rejects_index_set_mismatch():
    items = [
        {"index": 0, "text": "Hola"},
        {"index": 5, "text": "increíble"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("index" in e for e in errors)


def test_validator_rejects_cjk_in_es():
    items = [
        {"index": 0, "text": "Hola amigos"},
        {"index": 1, "text": "这是一段中文 而不是西语"},  # contaminated
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("cjk" in e.lower() for e in errors)


def test_validator_accepts_cjk_in_zh():
    items = [
        {"index": 0, "text": "你好朋友们今天我们来聊"},
        {"index": 1, "text": "这个产品真的太棒了"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="zh")
    assert errors == []


def test_validator_rejects_empty_text():
    items = [
        {"index": 0, "text": "Hola"},
        {"index": 1, "text": ""},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("empty" in e for e in errors)


def test_purify_primary_success_returns_cleaned():
    cleaned_items = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    with patch("pipeline.asr_clean.llm_client.invoke_chat", return_value=_ok_response(cleaned_items)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-1", user_id=1,
        )
    assert result["cleaned"] is True
    assert result["fallback_used"] is False
    assert result["utterances"][0]["text"] == "Hola amigos, hoy vamos a hablar de"
    assert result["utterances"][0]["start_time"] == 0.0  # timestamps preserved
    assert result["utterances"][0]["end_time"] == 2.0


def test_purify_falls_back_when_primary_invalid():
    bad = [{"index": 0, "text": "only one"}]  # length mismatch
    good = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    responses = iter([_ok_response(bad), _ok_response(good)])
    with patch("pipeline.asr_clean.llm_client.invoke_chat", side_effect=lambda *a, **kw: next(responses)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-2", user_id=1,
        )
    assert result["cleaned"] is True
    assert result["fallback_used"] is True


def test_purify_returns_uncleaned_when_both_fail():
    bad = [{"index": 0, "text": "only one"}]
    with patch("pipeline.asr_clean.llm_client.invoke_chat", return_value=_ok_response(bad)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-3", user_id=1,
        )
    assert result["cleaned"] is False
    assert result["fallback_used"] is True
    assert result["utterances"] == SAMPLE_ES_UTTS  # original returned untouched
    assert result["validation_errors"]


def test_validator_accepts_cjk_extension_a():
    """Ext A character (㐀, U+3400) should be recognized as CJK in zh validation."""
    items = [
        {"index": 0, "text": "㐀普通中文测试"},
        {"index": 1, "text": "另一段中文文本"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="zh")
    assert errors == []


def test_validator_accepts_halfwidth_katakana():
    """Halfwidth katakana (ｱｲｳ, U+FF66+) should be recognized in ja validation."""
    items = [
        {"index": 0, "text": "ｱｲｳｴｵこんにちは"},
        {"index": 1, "text": "テストです"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="ja")
    assert errors == []


def test_validator_passes_unlisted_language_with_warning(caplog):
    """Unknown language should accept (no errors) but log a warning."""
    items = [
        {"index": 0, "text": "Some unknown text"},
        {"index": 1, "text": "ASR output"},
    ]
    import logging
    with caplog.at_level(logging.WARNING, logger="pipeline.asr_clean"):
        errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="ko")
    assert errors == []  # No errors but warning logged
    assert any("no validator" in rec.message for rec in caplog.records)
