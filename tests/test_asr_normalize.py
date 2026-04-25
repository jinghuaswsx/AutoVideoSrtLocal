"""pipeline.asr_normalize 单元测试。"""
from __future__ import annotations

import json

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


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_preserves_timestamps_and_returns_usage(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "ES_PROMPT_FAKE"}
    mock_invoke.return_value = {
        "text": json.dumps({
            "utterances_en": [
                {"index": 0, "text_en": "Hi there"},
                {"index": 1, "text_en": "Check this out"},
            ],
        }),
        "usage": {"input_tokens": 1850, "output_tokens": 1620},
    }
    from pipeline.asr_normalize import translate_to_en
    utterances = [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este..."},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]
    out, usage = translate_to_en(
        utterances, detected_language="es", route="es_specialized",
        task_id="t10", user_id=1,
    )
    assert len(out) == 2
    assert out[0] == {"index": 0, "start": 0.5, "end": 2.3, "text": "Hi there"}
    assert out[1] == {"index": 1, "start": 2.3, "end": 4.8, "text": "Check this out"}
    assert usage == {"input_tokens": 1850, "output_tokens": 1620}


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_raises_on_length_mismatch(mock_invoke, mock_resolve):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "Only one"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en, TranslateOutputInvalidError
    utterances = [
        {"index": 0, "start": 0, "end": 1, "text": "a"},
        {"index": 1, "start": 1, "end": 2, "text": "b"},
    ]
    with pytest.raises(TranslateOutputInvalidError) as exc:
        translate_to_en(utterances, detected_language="fr",
                         route="generic_fallback", task_id="t11", user_id=1)
    assert "length mismatch" in str(exc.value).lower()


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_raises_on_index_gap(mock_invoke, mock_resolve):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [
            {"index": 0, "text_en": "a"},
            {"index": 2, "text_en": "c"},  # missing index 1
        ]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en, TranslateOutputInvalidError
    utterances = [
        {"index": 0, "start": 0, "end": 1, "text": "x"},
        {"index": 1, "start": 1, "end": 2, "text": "y"},
    ]
    with pytest.raises(TranslateOutputInvalidError) as exc:
        translate_to_en(utterances, detected_language="fr",
                         route="generic_fallback", task_id="t12", user_id=1)
    assert "index" in str(exc.value).lower()


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_uses_es_use_case_for_es_specialized_route(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    translate_to_en(
        [{"index": 0, "start": 0, "end": 1, "text": "x"}],
        detected_language="es", route="es_specialized",
        task_id="t13", user_id=1,
    )
    # use_case 第一个位置参数
    assert mock_invoke.call_args.args[0] == "asr_normalize.translate_es_to_en"
    mock_resolve.assert_called_with("asr_normalize.translate_es_en", "")


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_uses_generic_use_case_for_fallback_routes(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    for route in ("generic_fallback", "generic_fallback_low_confidence",
                  "generic_fallback_mixed"):
        translate_to_en(
            [{"index": 0, "start": 0, "end": 1, "text": "x"}],
            detected_language="pt", route=route, task_id="t", user_id=1,
        )
    for call in mock_invoke.call_args_list:
        assert call.args[0] == "asr_normalize.translate_generic_to_en"


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_passes_is_mixed_low_confidence_in_user_payload(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    translate_to_en(
        [{"index": 0, "start": 0, "end": 1, "text": "x"}],
        detected_language="pt", route="generic_fallback_mixed",
        task_id="t", user_id=1,
    )
    user_msg = mock_invoke.call_args.kwargs["messages"][1]["content"]
    payload = json.loads(user_msg)
    assert payload["is_mixed"] is True
    assert payload["low_confidence"] is False
