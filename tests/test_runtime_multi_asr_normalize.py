"""MultiTranslateRunner._step_asr_normalize 集成测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_runner():
    """复用 test_runtime_multi_translate.py 同款 runner 构造方式。"""
    from appcore.runtime_multi import MultiTranslateRunner
    runner = MultiTranslateRunner.__new__(MultiTranslateRunner)
    runner.user_id = 1
    runner._emit = MagicMock()
    runner._set_step = MagicMock()  # stub so tests can assert on it
    return runner


def _utterances():
    return [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este es un producto"},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_preserves_manual_source_language_for_es_route(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {
        "utterances": _utterances(),
        "_user_id": 1,
        "source_language": "es",
        "user_specified_source_language": True,
    }
    fake_en = [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
               {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}]
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "es",
        "confidence": 1.0,
        "is_mixed": False,
        "route": "es_specialized",
        "detection_source": "user_specified",
        "input": {"language_label": "西班牙语", "full_text_preview": "Hola, este...",
                   "utterance_count": 2},
        "output": {"full_text_preview": "Hi Look", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}},
        "elapsed_ms": 100,
        "model": {"detect": None, "translate": "c"},
        "_utterances_en": fake_en,
    }
    runner = _make_runner()
    runner._step_asr_normalize("t1")
    mock_asr_norm.run_user_specified.assert_called_once_with(
        task_id="t1", user_id=1, utterances=_utterances(), source_language="es",
    )
    mock_asr_norm.run_asr_normalize.assert_not_called()
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "es"
    assert update_kwargs["detected_source_language"] == "es"
    assert update_kwargs["utterances_en"] == fake_en
    # artifact 写入时不应该再含 _utterances_en
    set_artifact_kwargs = mock_state.set_artifact.call_args
    artifact_arg = set_artifact_kwargs.args[2]
    assert "_utterances_en" not in artifact_arg


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_routes_zh_keeps_source_language_zh(
    mock_asr_norm, mock_state,
):
    utterances = [{"index": 0, "start": 0, "end": 1, "text": "你好"}]
    mock_state.get.return_value = {
        "utterances": utterances,
        "_user_id": 1,
        "source_language": "zh",
        "user_specified_source_language": True,
    }
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "zh",
        "confidence": 0.98, "is_mixed": False, "route": "zh_skip",
        "detection_source": "user_specified",
        "input": {"language_label": "中文", "full_text_preview": "你好",
                   "utterance_count": 1},
        "output": {"full_text_preview": "你好", "utterance_count": 1},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 50,
        "model": {"detect": "g", "translate": None},
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-zh")
    mock_asr_norm.run_user_specified.assert_called_once_with(
        task_id="t-zh", user_id=1, utterances=utterances, source_language="zh",
    )
    mock_asr_norm.run_asr_normalize.assert_not_called()
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "zh"
    assert update_kwargs["detected_source_language"] == "zh"
    assert "utterances_en" not in update_kwargs


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_short_circuits_on_empty_utterances(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": [], "_user_id": 1}
    runner = _make_runner()
    runner._step_asr_normalize("t-empty")
    mock_asr_norm.run_asr_normalize.assert_not_called()
    # 标记为 done，message 含"无音频文本"
    set_step_call = runner._set_step.call_args
    assert set_step_call.args[1] == "asr_normalize"
    assert set_step_call.args[2] == "done"
    assert "无音频文本" in set_step_call.args[3]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_marks_failed_on_unsupported_language(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": _utterances(), "_user_id": 1}
    runner = _make_runner()
    runner._step_asr_normalize("t-other")
    mock_asr_norm.run_user_specified.assert_not_called()
    mock_asr_norm.run_asr_normalize.assert_not_called()
    set_step_call = runner._set_step.call_args
    assert set_step_call.args[1] == "asr_normalize"
    assert set_step_call.args[2] == "failed"
    assert "source_language" in set_step_call.args[3]
    update_kwargs = mock_state.update.call_args.kwargs
    assert "error" in update_kwargs


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_marks_failed_on_detect_exhaustion(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {
        "utterances": _utterances(),
        "_user_id": 1,
        "source_language": "es",
        "user_specified_source_language": True,
    }
    mock_asr_norm.run_user_specified.side_effect = RuntimeError("network")
    runner = _make_runner()
    runner._step_asr_normalize("t-net")
    set_step_call = runner._set_step.call_args
    assert set_step_call.args[2] == "failed"
    update_kwargs = mock_state.update.call_args.kwargs
    assert "按手动选择源语言标准化失败" in update_kwargs["error"]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_resume_idempotent_when_utterances_en_present(
    mock_asr_norm, mock_state,
):
    """再次调用时（utterances_en 已存在）应短路 done，不重新调 LLM。"""
    mock_state.get.return_value = {
        "utterances": _utterances(),
        "utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hi"}],
        "source_language": "en",
        "_user_id": 1,
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-resume")
    mock_asr_norm.run_asr_normalize.assert_not_called()

    # step 应标记为 done，message 含"resume 跳过"
    set_step_call = runner._set_step.call_args
    assert set_step_call.args[1] == "asr_normalize"
    assert set_step_call.args[2] == "done"
    assert "resume 跳过" in set_step_call.args[3]
    # 幂等：不应修改 task 状态，也不写 artifact
    mock_state.update.assert_not_called()
    mock_state.set_artifact.assert_not_called()


def test_get_pipeline_steps_inserts_asr_normalize_after_asr_before_voice_match():
    runner = _make_runner()
    base = [("extract", lambda: None), ("asr", lambda: None),
            ("alignment", lambda: None)]
    with patch.object(type(runner).__bases__[0], "_get_pipeline_steps",
                       return_value=base):
        steps = runner._get_pipeline_steps("t1", "/tmp/v.mp4", "/tmp")
    names = [name for name, _ in steps]
    asr_idx = names.index("asr")
    norm_idx = names.index("asr_normalize")
    voice_idx = names.index("voice_match")
    assert asr_idx < norm_idx < voice_idx


# ---------------------------------------------------------------------------
# CRITICAL #1: _step_alignment uses utterances_en when present
# ---------------------------------------------------------------------------

@patch("appcore.runtime.task_state.get")
@patch("pipeline.alignment.compile_alignment")
def test_step_alignment_uses_utterances_en_when_present(mock_compile, mock_get):
    """utterances_en 存在时，alignment 走英文文本（不是原始外语）。"""
    mock_get.return_value = {
        "utterances": [{"index": 0, "start": 0, "end": 1, "text": "Hola"}],
        "utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hi"}],
        "scene_cuts": [],
        "_user_id": 1,
        "_alignment_confirmed": False,
        "interactive_review": False,
    }
    mock_compile.return_value = {"script_segments": [], "break_after": []}
    # patch detect_scene_cuts and get_voice_library to avoid heavy I/O
    with patch("pipeline.alignment.detect_scene_cuts", return_value=[]), \
         patch("pipeline.voice_library.get_voice_library") as mock_vl, \
         patch("appcore.runtime.task_state.set_step"), \
         patch("appcore.runtime.task_state.set_step_message"), \
         patch("appcore.runtime.task_state.update"), \
         patch("appcore.runtime.task_state.set_artifact"), \
         patch("appcore.runtime.task_state.set_current_review_step"), \
         patch("appcore.runtime._save_json"), \
         patch("appcore.runtime.build_alignment_artifact", return_value={}):
        mock_vl.return_value.recommend_voice.return_value = None
        runner = _make_runner()
        runner._step_alignment("t-aln", "/tmp/v.mp4", "/tmp")
    # First positional arg to compile_alignment should be utterances_en
    called_utterances = mock_compile.call_args.args[0]
    assert called_utterances[0]["text"] == "Hi"


@patch("appcore.runtime.task_state.get")
@patch("pipeline.alignment.compile_alignment")
def test_step_alignment_falls_back_to_utterances_when_en_missing(mock_compile, mock_get):
    """utterances_en 缺失时，alignment 走原 utterances（zh/en 路径）。"""
    mock_get.return_value = {
        "utterances": [{"index": 0, "start": 0, "end": 1, "text": "你好"}],
        "scene_cuts": [],
        "_user_id": 1,
        "_alignment_confirmed": False,
        "interactive_review": False,
    }
    mock_compile.return_value = {"script_segments": [], "break_after": []}
    with patch("pipeline.alignment.detect_scene_cuts", return_value=[]), \
         patch("pipeline.voice_library.get_voice_library") as mock_vl, \
         patch("appcore.runtime.task_state.set_step"), \
         patch("appcore.runtime.task_state.set_step_message"), \
         patch("appcore.runtime.task_state.update"), \
         patch("appcore.runtime.task_state.set_artifact"), \
         patch("appcore.runtime.task_state.set_current_review_step"), \
         patch("appcore.runtime._save_json"), \
         patch("appcore.runtime.build_alignment_artifact", return_value={}):
        mock_vl.return_value.recommend_voice.return_value = None
        runner = _make_runner()
        runner._step_alignment("t-zh", "/tmp/v.mp4", "/tmp")
    called_utterances = mock_compile.call_args.args[0]
    assert called_utterances[0]["text"] == "你好"


# ---------------------------------------------------------------------------
# CRITICAL #2: _step_asr_normalize failure sets status="error" to stop pipeline
# ---------------------------------------------------------------------------

@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_failure_sets_status_error_to_stop_pipeline(
    mock_asr_norm, mock_state,
):
    """缺少手动源语言 → task status='error' so _run loop exits."""
    mock_state.get.return_value = {"utterances": _utterances(), "_user_id": 1}
    runner = _make_runner()
    runner._step_asr_normalize("t1")
    mock_asr_norm.run_user_specified.assert_not_called()
    mock_asr_norm.run_asr_normalize.assert_not_called()
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs.get("status") == "error"
    assert "error" in update_kwargs


# ---------------------------------------------------------------------------
# user_specified_source_language 路径：跳过 detect_language，直接路由
# ---------------------------------------------------------------------------

@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_user_specified_es_calls_run_user_specified(
    mock_asr_norm, mock_state,
):
    """user_specified=True + es → 走 run_user_specified（不调 run_asr_normalize）。"""
    mock_state.get.return_value = {
        "utterances": _utterances(), "_user_id": 1,
        "source_language": "es",
        "user_specified_source_language": True,
    }
    fake_en = [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
               {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}]
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "es",
        "confidence": 1.0,
        "is_mixed": False,
        "route": "es_specialized",
        "detection_source": "user_specified",
        "input": {"language_label": "西班牙语",
                   "full_text_preview": "Hola este...",
                   "utterance_count": 2},
        "output": {"full_text_preview": "Hi Look", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}},
        "elapsed_ms": 80,
        "model": {"detect": None, "translate": "anthropic/claude-sonnet-4.6"},
        "_utterances_en": fake_en,
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-es-user")

    mock_asr_norm.run_user_specified.assert_called_once()
    mock_asr_norm.run_asr_normalize.assert_not_called()
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "es"
    assert update_kwargs["detected_source_language"] == "es"
    assert update_kwargs["utterances_en"] == fake_en
    # 原始 artifact 通过 update 写入 task["asr_normalize_artifact"]
    assert update_kwargs["asr_normalize_artifact"]["detection_source"] == "user_specified"
    # set_artifact 写入的是 UI 投影：左右对照视图
    artifact_arg = mock_state.set_artifact.call_args.args[2]
    assert artifact_arg["title"] == "原文标准化"
    assert artifact_arg["items"][0]["type"] == "side_by_side"


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_user_specified_pt_uses_generic_fallback(
    mock_asr_norm, mock_state,
):
    """user_specified=True + pt → run_user_specified 返回 generic_fallback route。"""
    mock_state.get.return_value = {
        "utterances": _utterances(), "_user_id": 1,
        "source_language": "pt",
        "user_specified_source_language": True,
    }
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "pt",
        "confidence": 1.0, "is_mixed": False,
        "route": "generic_fallback",
        "detection_source": "user_specified",
        "input": {"language_label": "葡萄牙语",
                   "full_text_preview": "Olá", "utterance_count": 2},
        "output": {"full_text_preview": "Hello", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 80,
        "model": {"detect": None, "translate": "anthropic/claude-sonnet-4.6"},
        "_utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hello"},
                           {"index": 1, "start": 1, "end": 2, "text": "."}],
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-pt")
    mock_asr_norm.run_user_specified.assert_called_once_with(
        task_id="t-pt", user_id=1, utterances=_utterances(), source_language="pt",
    )
    mock_asr_norm.run_asr_normalize.assert_not_called()


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_user_specified_zh_writes_artifact(
    mock_asr_norm, mock_state,
):
    """user_specified=True + zh → run_user_specified 返回 zh_skip，仍写 artifact。

    用户指定时一定会有 artifact，让详情页能展示「用户指定语言」卡片。
    """
    mock_state.get.return_value = {
        "utterances": _utterances(), "_user_id": 1,
        "source_language": "zh",
        "user_specified_source_language": True,
    }
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "zh",
        "confidence": 1.0, "is_mixed": False,
        "route": "zh_skip",
        "detection_source": "user_specified",
        "input": {"language_label": "中文", "full_text_preview": "你好",
                   "utterance_count": 2},
        "output": {"full_text_preview": "你好", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 5,
        "model": {"detect": None, "translate": None},
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-zh-user")

    mock_asr_norm.run_user_specified.assert_called_once()
    mock_asr_norm.run_asr_normalize.assert_not_called()
    # zh_skip 路径不写 utterances_en
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "zh"
    assert "utterances_en" not in update_kwargs
    # 但 artifact 一定写
    mock_state.set_artifact.assert_called_once()


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_user_specified_fr_uses_generic_fallback(
    mock_asr_norm, mock_state,
):
    """source_language=fr 也应按人工选择路径跑，不应被运行时拒绝。"""
    mock_state.get.return_value = {
        "utterances": _utterances(), "_user_id": 1,
        "source_language": "fr",
        "user_specified_source_language": True,
    }
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "fr",
        "confidence": 1.0, "is_mixed": False,
        "route": "generic_fallback",
        "detection_source": "user_specified",
        "input": {"language_label": "法语", "full_text_preview": "Bonjour", "utterance_count": 2},
        "output": {"full_text_preview": "Hello", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 80,
        "model": {"detect": None, "translate": "anthropic/claude-sonnet-4.6"},
        "_utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hello"}],
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-fr")
    mock_asr_norm.run_user_specified.assert_called_once_with(
        task_id="t-fr", user_id=1, utterances=_utterances(), source_language="fr",
    )
    mock_asr_norm.run_asr_normalize.assert_not_called()
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "fr"


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_uses_source_language_even_when_legacy_flag_is_false(
    mock_asr_norm, mock_state,
):
    """兼容旧任务：只要有 source_language，就按该语言跑，不再走自动检测。"""
    mock_state.get.return_value = {
        "utterances": _utterances(), "_user_id": 1,
        "source_language": "es",
        "user_specified_source_language": False,
    }
    mock_asr_norm.run_user_specified.return_value = {
        "detected_source_language": "es",
        "confidence": 1.0, "is_mixed": False,
        "route": "es_specialized",
        "detection_source": "user_specified",
        "input": {"language_label": "西班牙语",
                   "full_text_preview": "Hola", "utterance_count": 2},
        "output": {"full_text_preview": "Hi", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 80,
        "model": {"detect": None, "translate": "c"},
        "_utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hi"},
                           {"index": 1, "start": 1, "end": 2, "text": "Look"}],
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-legacy-manual")
    mock_asr_norm.run_user_specified.assert_called_once_with(
        task_id="t-legacy-manual", user_id=1, utterances=_utterances(), source_language="es",
    )
    mock_asr_norm.run_asr_normalize.assert_not_called()
