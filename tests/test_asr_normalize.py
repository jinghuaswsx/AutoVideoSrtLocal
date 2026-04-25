"""pipeline.asr_normalize 单元测试。"""
from __future__ import annotations

import pytest


def test_module_exports_required_symbols():
    from pipeline import asr_normalize
    assert hasattr(asr_normalize, "DETECT_SUPPORTED_LANGS")
    assert hasattr(asr_normalize, "LOW_CONFIDENCE_THRESHOLD")
    assert hasattr(asr_normalize, "LANG_LABELS")
    assert hasattr(asr_normalize, "DetectLanguageFailedError")
    assert hasattr(asr_normalize, "UnsupportedSourceLanguageError")
    assert hasattr(asr_normalize, "TranslateOutputInvalidError")
    assert hasattr(asr_normalize, "detect_language")
    assert hasattr(asr_normalize, "translate_to_en")
    assert hasattr(asr_normalize, "run_asr_normalize")


def test_detect_supported_langs_excludes_other():
    from pipeline.asr_normalize import DETECT_SUPPORTED_LANGS
    assert DETECT_SUPPORTED_LANGS == ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi")
    assert "other" not in DETECT_SUPPORTED_LANGS


def test_low_confidence_threshold_is_06():
    from pipeline.asr_normalize import LOW_CONFIDENCE_THRESHOLD
    assert LOW_CONFIDENCE_THRESHOLD == 0.6


def test_lang_labels_covers_all_supported():
    from pipeline.asr_normalize import LANG_LABELS, DETECT_SUPPORTED_LANGS
    for code in DETECT_SUPPORTED_LANGS:
        assert code in LANG_LABELS, f"LANG_LABELS missing {code!r}"
    assert LANG_LABELS["zh"] == "中文"
    assert LANG_LABELS["es"] == "西班牙语"
    assert LANG_LABELS["en"] == "英语"


from unittest.mock import MagicMock, patch


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_normal_returns_parsed_dict_and_usage(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "DETECT_PROMPT_FAKE"}
    mock_invoke.return_value = {
        "text": '{"language":"es","confidence":0.97,"is_mixed":false}',
        "usage": {"input_tokens": 320, "output_tokens": 40},
    }
    from pipeline.asr_normalize import detect_language
    parsed, usage = detect_language("Hola, este es un producto", task_id="t1", user_id=1)
    assert parsed == {"language": "es", "confidence": 0.97, "is_mixed": False}
    assert usage == {"input_tokens": 320, "output_tokens": 40}
    mock_invoke.assert_called_once()


@patch("pipeline.asr_normalize.time.sleep")  # 跳过真实 sleep
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_retries_once_on_api_error(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "DETECT_PROMPT_FAKE"}
    mock_invoke.side_effect = [
        Exception("network burp"),
        {"text": '{"language":"en","confidence":0.99,"is_mixed":false}',
         "usage": {"input_tokens": 100, "output_tokens": 30}},
    ]
    from pipeline.asr_normalize import detect_language
    parsed, _ = detect_language("Hello there", task_id="t2", user_id=1)
    assert parsed["language"] == "en"
    assert mock_invoke.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("pipeline.asr_normalize.time.sleep")
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_fails_after_two_attempts(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.side_effect = [Exception("fail1"), Exception("fail2")]
    from pipeline.asr_normalize import detect_language, DetectLanguageFailedError
    with pytest.raises(DetectLanguageFailedError) as exc_info:
        detect_language("foo", task_id="t3", user_id=1)
    assert "2 attempts" in str(exc_info.value)
    assert mock_invoke.call_count == 2


@patch("pipeline.asr_normalize.time.sleep")
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_handles_invalid_json_in_response(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {"text": "not json at all", "usage": {}}
    from pipeline.asr_normalize import detect_language, DetectLanguageFailedError
    with pytest.raises(DetectLanguageFailedError):
        detect_language("foo", task_id="t4", user_id=1)
