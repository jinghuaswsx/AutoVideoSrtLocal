"""Baseline characterization tests for ``appcore.runtime``.

锁定 ``runtime`` 公开 API 与 ``PipelineRunner`` 形状，作为 Stage 3 拆分
（PR 3.1+）的回归防护网。每个 sub-module 拆出之前本文件须保持绿；拆分
过程中函数签名与 import 路径不变，本文件不应被修改。

不实际跑 ``_step_*`` 视频流程。
"""
from __future__ import annotations

import inspect


def test_public_dispatchers_importable():
    from appcore import runtime as rt

    public = [
        "PipelineRunner",
        "dispatch_localize",
        "run_localize",
        "run_av_localize",
        "run_analysis_only",
    ]
    missing = [n for n in public if not hasattr(rt, n)]
    assert not missing, f"missing public symbols: {missing}"


def test_private_helpers_importable():
    """子类 / web routes / 测试用到的 ``_*`` helpers 必须保持可 import。"""
    from appcore import runtime as rt

    private = [
        # 顶层 helpers
        "_save_json", "_count_visible_chars",
        "_join_utterance_text", "_resolve_original_video_passthrough",
        "_is_original_video_passthrough", "_build_review_segments",
        "_translate_billing_provider", "_translate_billing_model",
        "_log_translate_billing", "_llm_request_payload", "_llm_response_payload",
        "_seconds_to_request_units",
        "_resolve_translate_provider", "_resolve_task_translate_provider",
        "_lang_display",
        "_is_av_pipeline_task", "_av_target_lang",
        "_tts_final_target_range", "_compute_next_target",
        "_distance_to_duration_range",
        "_fit_tts_segments_to_duration", "_trim_tts_metadata_to_segments",
        "_skip_legacy_artifact_upload",
        # AV helpers
        "_default_av_variant_state", "_ensure_variant_state",
        "_join_source_full_text", "_load_json_if_exists",
        "_restore_av_localize_outputs_from_files",
        "_normalize_av_sentences", "_build_av_localized_translation",
        "_build_av_tts_segments", "_rebuild_tts_full_audio_from_segments",
        "_build_av_debug_state", "_fail_localize", "_new_silent_runner",
    ]
    missing = [n for n in private if not hasattr(rt, n)]
    assert not missing, f"missing private helpers: {missing}"


def test_pipeline_runner_class_shape():
    from appcore.runtime import PipelineRunner

    # class attrs (subclasses override)
    for attr in (
        "project_type",
        "tts_language_code",
        "tts_model_id",
        "tts_default_voice_language",
        "localization_module",
        "target_language_label",
        "include_soft_video",
        "include_analysis_in_main_flow",
    ):
        assert hasattr(PipelineRunner, attr), f"missing class attr: {attr}"

    # core methods
    methods = [
        "__init__",
        "_emit", "_set_step", "_emit_substep_msg",
        "_get_localization_module", "_get_tts_target_language_label",
        "_get_tts_model_id", "_get_tts_language_code",
        "_run_tts_duration_loop", "_promote_final_artifacts",
        "_truncate_audio_to_duration", "_trim_tail_segments",
        "_resolve_voice",
        "start", "resume",
        "_get_pipeline_steps", "_run",
        "_step_extract", "_step_asr", "_step_alignment",
        "_step_translate", "_step_tts", "_step_subtitle",
        "_step_compose", "_step_analysis", "_step_export",
        "_step_av_asr_normalize", "_step_av_voice_match",
    ]
    missing = [m for m in methods if not hasattr(PipelineRunner, m)]
    assert not missing, f"missing PipelineRunner methods: {missing}"


def test_runtime_subclasses_importable():
    """All runtime variants (DE/FR/JA/multi/omni/v2/sentence) load and bind
    PipelineRunner via ``from appcore.runtime import PipelineRunner, ...``."""
    import importlib

    for mod in (
        "appcore.runtime_de",
        "appcore.runtime_fr",
        "appcore.runtime_ja",
        "appcore.runtime_multi",
        "appcore.runtime_omni",
        "appcore.runtime_v2",
        "appcore.runtime_sentence_translate",
    ):
        importlib.import_module(mod)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no DB / no event bus)
# ─────────────────────────────────────────────────────────────────────


def test_count_visible_chars():
    from appcore.runtime import _count_visible_chars

    assert _count_visible_chars("") == 0
    assert _count_visible_chars(None) == 0
    assert _count_visible_chars("abc") == 3
    assert _count_visible_chars("a b c") == 3  # spaces excluded


def test_join_utterance_text_shape():
    from appcore.runtime import _join_utterance_text

    assert _join_utterance_text([]) == ""
    assert _join_utterance_text([{"text": "  hello  "}, {"text": "world"}]) == "hello world"


def test_resolve_original_video_passthrough_short():
    from appcore.runtime import _resolve_original_video_passthrough

    result = _resolve_original_video_passthrough([{"text": "tiny"}])
    assert isinstance(result, dict)
    assert {"enabled", "reason", "source_full_text", "source_chars"}.issubset(result.keys())
    assert result["enabled"] is True


def test_resolve_original_video_passthrough_long():
    from appcore.runtime import _resolve_original_video_passthrough

    long_text = "x" * 100
    result = _resolve_original_video_passthrough([{"text": long_text}])
    assert result["enabled"] is False


def test_is_original_video_passthrough_shape():
    from appcore.runtime import _is_original_video_passthrough

    assert _is_original_video_passthrough(None) is False
    assert _is_original_video_passthrough({"media_passthrough_mode": "original_video"}) is True
    assert _is_original_video_passthrough({"media_passthrough_mode": "normal"}) is False


def test_is_av_pipeline_task_shape():
    from appcore.runtime import _is_av_pipeline_task

    assert _is_av_pipeline_task(None) is False
    assert _is_av_pipeline_task({}) is False
    assert _is_av_pipeline_task({"type": "av_translate"}) is True
    assert _is_av_pipeline_task({"pipeline_version": "av"}) is True


def test_seconds_to_request_units_shape():
    from appcore.runtime import _seconds_to_request_units

    assert _seconds_to_request_units(None) is None
    n = _seconds_to_request_units(120.5)
    assert isinstance(n, int)


def test_lang_display_shape():
    from appcore.runtime import _lang_display

    assert _lang_display("") == ""
    out = _lang_display("de")
    assert isinstance(out, str)


def test_tts_final_target_range_shape():
    from appcore.runtime import _tts_final_target_range

    lo, hi = _tts_final_target_range(60.0)
    assert isinstance(lo, float)
    assert isinstance(hi, float)
    assert lo <= hi


def test_compute_next_target_shape():
    from appcore.runtime import _compute_next_target

    sig = inspect.signature(_compute_next_target)
    # callable shape only
    assert callable(_compute_next_target)
    assert len(sig.parameters) >= 1


def test_distance_to_duration_range_shape():
    from appcore.runtime import _distance_to_duration_range

    assert _distance_to_duration_range(50.0, 40.0, 60.0) == 0.0
    assert _distance_to_duration_range(30.0, 40.0, 60.0) == 10.0
    assert _distance_to_duration_range(70.0, 40.0, 60.0) == 10.0


def test_fit_tts_segments_to_duration_empty():
    from appcore.runtime import _fit_tts_segments_to_duration

    assert _fit_tts_segments_to_duration([], 10.0) == []


def test_save_json_writes_file(tmp_path):
    from appcore.runtime import _save_json

    _save_json(str(tmp_path), "out.json", {"x": 1})
    assert (tmp_path / "out.json").read_text(encoding="utf-8")


def test_default_av_variant_state_shape():
    from appcore.runtime import _default_av_variant_state

    state = _default_av_variant_state()
    assert isinstance(state, dict)


def test_join_source_full_text_shape():
    from appcore.runtime import _join_source_full_text

    assert _join_source_full_text([]) == ""
    out = _join_source_full_text([{"text": "abc"}, {"text": "def"}])
    assert isinstance(out, str)


def test_load_json_if_exists_missing(tmp_path):
    from appcore.runtime import _load_json_if_exists

    assert _load_json_if_exists(str(tmp_path / "nope.json")) is None


def test_normalize_av_sentences_empty():
    from appcore.runtime import _normalize_av_sentences

    assert _normalize_av_sentences([]) == []


def test_build_av_localized_translation_empty():
    from appcore.runtime import _build_av_localized_translation

    out = _build_av_localized_translation([])
    assert isinstance(out, dict)


def test_build_av_tts_segments_empty():
    from appcore.runtime import _build_av_tts_segments

    assert _build_av_tts_segments([]) == []
