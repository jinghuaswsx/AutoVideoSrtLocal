"""pipeline.asr_normalize 单元测试。"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _stub_purify_utterances(monkeypatch):
    """Default no-op stub so tests that don't care about purify don't hit LLMs.

    Tests that need to assert on purify behaviour can override by patching
    `pipeline.asr_clean.purify_utterances` themselves (the explicit patch
    wins because monkeypatch is applied first per-test).
    """
    def _fake_purify(utterances, *, language, task_id, user_id):
        return {
            "utterances": utterances,
            "cleaned": False,
            "fallback_used": False,
            "model_used": "stub",
            "validation_errors": [],
            "raw_response_primary": "",
            "raw_response_fallback": None,
            "usage": {"primary": {}, "fallback": {}},
        }
    monkeypatch.setattr("pipeline.asr_clean.purify_utterances", _fake_purify)


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
    assert out[0] == {"index": 0, "start_time": 0.5, "end_time": 2.3, "text": "Hi there"}
    assert out[1] == {"index": 1, "start_time": 2.3, "end_time": 4.8, "text": "Check this out"}
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


def _make_utterances():
    return [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este es un producto"},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_en_to_en_skip(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "en", "confidence": 0.99, "is_mixed": False},
                                 {"input_tokens": 100, "output_tokens": 30})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "en_skip"
    assert artifact["detected_source_language"] == "en"
    assert "_utterances_en" not in artifact
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_zh_to_zh_skip(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "zh", "confidence": 0.98, "is_mixed": False},
                                 {"input_tokens": 90, "output_tokens": 30})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "zh_skip"
    assert artifact["detected_source_language"] == "zh"
    assert "_utterances_en" not in artifact
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_es_to_specialized(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.97, "is_mixed": False},
                                 {"input_tokens": 320, "output_tokens": 40})
    fake_en = [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
               {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}]
    mock_translate.return_value = (fake_en, {"input_tokens": 1850, "output_tokens": 1620})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "es_specialized"
    assert artifact["_utterances_en"] == fake_en
    assert artifact["detected_source_language"] == "es"
    assert artifact["confidence"] == 0.97
    mock_translate.assert_called_once_with(
        _make_utterances(), detected_language="es",
        route="es_specialized", task_id="t", user_id=1,
    )


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_pt_to_generic_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "pt", "confidence": 0.92, "is_mixed": False},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Olá"}],
    )
    assert artifact["route"] == "generic_fallback"
    assert artifact["detected_source_language"] == "pt"
    mock_translate.assert_called_once()
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_low_confidence_to_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "fr", "confidence": 0.45, "is_mixed": False},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Bonjour"}],
    )
    assert artifact["route"] == "generic_fallback_low_confidence"
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback_low_confidence"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_mixed_to_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.85, "is_mixed": True},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Hola hello"}],
    )
    assert artifact["route"] == "generic_fallback_mixed"
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback_mixed"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_raises_unsupported_on_other(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "other", "confidence": 0.88, "is_mixed": False},
                                 {})
    from pipeline.asr_normalize import run_asr_normalize, UnsupportedSourceLanguageError
    with pytest.raises(UnsupportedSourceLanguageError) as exc:
        run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert "other" in str(exc.value)
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_artifact_includes_token_metadata(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.97, "is_mixed": False},
                                 {"input_tokens": 320, "output_tokens": 40})
    mock_translate.return_value = (
        [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
         {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}],
        {"input_tokens": 1850, "output_tokens": 1620},
    )
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["tokens"]["detect"] == {"input_tokens": 320, "output_tokens": 40}
    assert artifact["tokens"]["translate"] == {"input_tokens": 1850, "output_tokens": 1620}
    assert "elapsed_ms" in artifact and artifact["elapsed_ms"] >= 0
    assert artifact["model"]["detect"] == "gemini-3.1-flash-lite-preview"
    assert artifact["model"]["translate"] == "anthropic/claude-sonnet-4.6"
    assert artifact["input"]["language_label"] == "西班牙语"
    assert artifact["input"]["utterance_count"] == 2
    assert artifact["output"]["utterance_count"] == 2
    assert artifact["detection_source"] == "llm"


# ---------------------------------------------------------------------------
# run_user_specified：用户明确指定语言时跳过 detect_language 直接路由
# ---------------------------------------------------------------------------

@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_user_specified_es_routes_to_es_specialized_translates(
    mock_detect, mock_translate,
):
    mock_translate.return_value = (
        [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
         {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}],
        {"input_tokens": 100, "output_tokens": 80},
    )
    from pipeline.asr_normalize import run_user_specified
    artifact = run_user_specified(
        task_id="t", user_id=1, utterances=_make_utterances(), source_language="es",
    )
    mock_detect.assert_not_called()
    mock_translate.assert_called_once()
    assert mock_translate.call_args.kwargs["route"] == "es_specialized"
    assert artifact["route"] == "es_specialized"
    assert artifact["detected_source_language"] == "es"
    assert artifact["confidence"] == 1.0
    assert artifact["is_mixed"] is False
    assert artifact["detection_source"] == "user_specified"
    assert artifact["model"]["detect"] is None
    assert artifact["model"]["translate"] == "anthropic/claude-sonnet-4.6"
    assert "_utterances_en" in artifact


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_user_specified_pt_uses_generic_fallback_translates(
    mock_detect, mock_translate,
):
    mock_translate.return_value = (
        [{"index": 0, "start": 0, "end": 1, "text": "Hello"},
         {"index": 1, "start": 1, "end": 2, "text": "Look"}],
        {"input_tokens": 100, "output_tokens": 80},
    )
    from pipeline.asr_normalize import run_user_specified
    artifact = run_user_specified(
        task_id="t", user_id=1, utterances=_make_utterances(), source_language="pt",
    )
    mock_detect.assert_not_called()
    mock_translate.assert_called_once()
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback"
    assert artifact["route"] == "generic_fallback"
    assert artifact["detected_source_language"] == "pt"
    assert artifact["detection_source"] == "user_specified"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_user_specified_zh_skips_translate(mock_detect, mock_translate):
    from pipeline.asr_normalize import run_user_specified
    artifact = run_user_specified(
        task_id="t", user_id=1, utterances=_make_utterances(), source_language="zh",
    )
    mock_detect.assert_not_called()
    mock_translate.assert_not_called()
    assert artifact["route"] == "zh_skip"
    assert "_utterances_en" not in artifact
    assert artifact["model"]["translate"] is None


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_user_specified_en_skips_translate(mock_detect, mock_translate):
    from pipeline.asr_normalize import run_user_specified
    artifact = run_user_specified(
        task_id="t", user_id=1, utterances=_make_utterances(), source_language="en",
    )
    mock_detect.assert_not_called()
    mock_translate.assert_not_called()
    assert artifact["route"] == "en_skip"
    assert "_utterances_en" not in artifact


def test_run_user_specified_rejects_unsupported_lang():
    from pipeline.asr_normalize import run_user_specified
    with pytest.raises(ValueError) as exc:
        run_user_specified(
            task_id="t", user_id=1, utterances=_make_utterances(), source_language="ru",
        )
    assert "ru" in str(exc.value)


# ---------------------------------------------------------------------------
# Task 12: ASR same-language purification injected before translate_to_en
# ---------------------------------------------------------------------------


def test_run_user_specified_calls_purify():
    from pipeline import asr_normalize
    utts = [{"index": 0, "start": 0, "end": 1, "text": "Hola"}]
    with patch("pipeline.asr_clean.purify_utterances",
               return_value={"utterances": utts, "cleaned": True,
                             "fallback_used": False, "model_used": "test",
                             "validation_errors": [], "raw_response_primary": "",
                             "raw_response_fallback": None,
                             "usage": {"primary": {}, "fallback": {}}}) as p, \
         patch("pipeline.asr_normalize.translate_to_en",
               return_value=([{"index": 0, "start": 0, "end": 1, "text": "Hello"}], {})):
        artifact = asr_normalize.run_user_specified(
            task_id="t-1", user_id=1, utterances=utts, source_language="es",
        )
    p.assert_called_once()
    assert artifact["asr_clean"]["performed"] is True
    assert artifact["asr_clean"]["cleaned"] is True


def test_run_user_specified_skips_purify_for_zh_skip_route():
    """zh_skip route still purifies the input (zh in the supported list)."""
    from pipeline import asr_normalize
    utts = [{"index": 0, "start": 0, "end": 1, "text": "你好"}]
    with patch("pipeline.asr_clean.purify_utterances",
               return_value={"utterances": utts, "cleaned": True,
                             "fallback_used": False, "model_used": "test",
                             "validation_errors": [], "raw_response_primary": "",
                             "raw_response_fallback": None,
                             "usage": {"primary": {}, "fallback": {}}}) as p:
        artifact = asr_normalize.run_user_specified(
            task_id="t-2", user_id=1, utterances=utts, source_language="zh",
        )
    # zh_skip route: purify still runs but translate_to_en doesn't
    p.assert_called_once()
    assert artifact["asr_clean"]["performed"] is True
    assert artifact["route"] == "zh_skip"
