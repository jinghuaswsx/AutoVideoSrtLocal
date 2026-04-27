"""Tests for appcore/runtime.py PipelineRunner.

All pipeline steps are mocked — runtime logic only.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import appcore.task_state as task_state
from appcore.events import EVT_PIPELINE_ERROR, EVT_STEP_UPDATE, Event, EventBus
from appcore.runtime import PipelineRunner


def _make_task(task_id: str) -> None:
    task_state.create(task_id, "/video.mp4", "/task_dir", "video.mp4")


def _make_runner() -> tuple[PipelineRunner, list[Event]]:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(lambda e: events.append(e))
    runner = PipelineRunner(bus=bus)
    return runner, events


def test_set_step_publishes_step_update_event():
    task_id = "test_set_step"
    _make_task(task_id)
    runner, events = _make_runner()
    runner._set_step(task_id, "asr", "running", "testing")
    assert any(
        e.type == EVT_STEP_UPDATE and e.payload["step"] == "asr" and e.payload["status"] == "running"
        for e in events
    )
    assert task_state.get(task_id)["steps"]["asr"] == "running"


def test_emit_substep_msg_publishes_event_without_persisting(monkeypatch):
    """_emit_substep_msg should publish EVT_STEP_UPDATE reflecting the current
    step status, but not call task_state.set_step_message / set_step (avoid
    per-segment disk writes)."""
    task_id = "substep-task"
    _make_task(task_id)
    runner, events = _make_runner()

    # Pre-set the step to running (production scenario: substep is emitted
    # while the step is running)
    runner._set_step(task_id, "tts", "running", "正在生成英语配音...")
    events.clear()  # drop the _set_step event so we only assert the substep one

    set_step_calls = []
    set_msg_calls = []
    monkeypatch.setattr(task_state, "set_step",
                        lambda *a, **kw: set_step_calls.append((a, kw)))
    monkeypatch.setattr(task_state, "set_step_message",
                        lambda *a, **kw: set_msg_calls.append((a, kw)))

    runner._emit_substep_msg(task_id, "tts", "正在生成英语配音 · 第 1 轮 · 切分朗读文案中")

    step_events = [e for e in events if e.type == EVT_STEP_UPDATE]
    assert len(step_events) == 1
    assert step_events[0].payload["step"] == "tts"
    assert step_events[0].payload["status"] == "running"
    assert step_events[0].payload["message"] == "正在生成英语配音 · 第 1 轮 · 切分朗读文案中"
    assert set_step_calls == []
    assert set_msg_calls == []


def test_step_tts_emits_loading_voice_substep(tmp_path, monkeypatch):
    """_step_tts 一进来就应该立即发一条 EVT_STEP_UPDATE，message 包含
    '加载配音模板'，覆盖首轮 LLM 调用前的几百毫秒空白。"""
    task_id = "loading-msg-task"
    _make_task(task_id)
    runner, events = _make_runner()

    # Prepare task state with minimal required fields
    task_state.update(task_id, source_full_text="hi",
                      script_segments=[{"index": 0, "text": "hi", "start_time": 0.0, "end_time": 1.0}],
                      localized_translation={"full_text": "hola", "sentences": [{"text": "hola"}]},
                      variants={"normal": {"localized_translation": {"full_text": "hola", "sentences": [{"text": "hola"}]}}})

    # Mock _run_tts_duration_loop to fail immediately so we only test entry
    monkeypatch.setattr(
        runner, "_run_tts_duration_loop",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("stop here")),
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda p: 30.0)
    monkeypatch.setattr(runner, "_resolve_voice", lambda task, mod: {
        "id": 1, "elevenlabs_voice_id": "vid"})
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *a, **kw: "fake")

    try:
        runner._step_tts(task_id, str(tmp_path))
    except RuntimeError:
        pass

    msgs = [e.payload["message"] for e in events if e.type == EVT_STEP_UPDATE]
    assert any("加载配音模板" in m for m in msgs), f"got messages: {msgs}"


def test_run_calls_all_steps_in_order():
    task_id = "test_run_order"
    _make_task(task_id)
    runner, events = _make_runner()

    call_order = []

    runner._step_extract = lambda *a: call_order.append("extract")
    runner._step_asr = lambda *a: call_order.append("asr")
    runner._step_alignment = lambda *a: call_order.append("alignment")
    runner._step_translate = lambda *a: call_order.append("translate")
    runner._step_tts = lambda *a: call_order.append("tts")
    runner._step_subtitle = lambda *a: call_order.append("subtitle")
    runner._step_compose = lambda *a: call_order.append("compose")
    runner._step_export = lambda *a: call_order.append("export")

    with patch("appcore.source_video.ensure_local_source_video", lambda task_id: None):
        runner._run(task_id)

    assert call_order == ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]


def test_run_publishes_pipeline_error_on_exception():
    task_id = "test_run_error"
    _make_task(task_id)
    runner, events = _make_runner()

    runner._step_extract = MagicMock(side_effect=RuntimeError("boom"))
    runner._step_asr = MagicMock()

    with patch("appcore.source_video.ensure_local_source_video", lambda task_id: None):
        runner._run(task_id)

    error_events = [e for e in events if e.type == EVT_PIPELINE_ERROR]
    assert len(error_events) == 1
    assert "boom" in error_events[0].payload["error"]
    assert task_state.get(task_id)["status"] == "error"


def test_no_flask_or_socketio_imports():
    """Ensure runtime.py never imports Flask or socketio."""
    import importlib
    import sys

    # Remove cached module to re-check imports cleanly
    mod_name = "appcore.runtime"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Temporarily block flask/socketio
    import builtins
    real_import = builtins.__import__

    forbidden = []

    def guarded_import(name, *args, **kwargs):
        if name in ("flask", "flask_socketio", "web.extensions"):
            forbidden.append(name)
        return real_import(name, *args, **kwargs)

    builtins.__import__ = guarded_import
    try:
        import appcore.runtime  # noqa: F401
    finally:
        builtins.__import__ = real_import

    assert not forbidden, f"appcore.runtime imported forbidden modules: {forbidden}"


def test_pipeline_runner_has_tts_class_attributes():
    from appcore.runtime import PipelineRunner
    # Default (English) values
    assert PipelineRunner.tts_language_code is None
    assert PipelineRunner.tts_model_id == "eleven_turbo_v2_5"
    assert PipelineRunner.tts_default_voice_language is None
    assert PipelineRunner.localization_module == "pipeline.localization"
    assert PipelineRunner.target_language_label == "en"


def test_log_translate_billing_forwards_payloads(monkeypatch):
    from appcore import runtime

    captured = {}
    monkeypatch.setattr(
        runtime.ai_billing,
        "log_request",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        runtime,
        "_translate_billing_model",
        lambda provider, user_id: "gemini-3.1-flash-lite-preview",
    )

    runtime._log_translate_billing(
        user_id=7,
        project_id="task-7",
        use_case_code="video_translate.localize",
        provider="vertex_gemini_31_flash_lite",
        input_tokens=10,
        output_tokens=5,
        request_payload={"messages": [{"role": "user", "content": "hi"}]},
        response_payload={"full_text": "ok"},
    )

    assert captured["request_payload"] == {
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert captured["response_payload"] == {"full_text": "ok"}


def test_de_runner_overrides_tts_class_attributes():
    from appcore.runtime_de import DeTranslateRunner
    assert DeTranslateRunner.tts_language_code == "de"
    assert DeTranslateRunner.tts_model_id == "eleven_multilingual_v2"
    assert DeTranslateRunner.tts_default_voice_language == "de"
    assert DeTranslateRunner.localization_module == "pipeline.localization_de"
    assert DeTranslateRunner.target_language_label == "de"


def test_fr_runner_overrides_tts_class_attributes():
    from appcore.runtime_fr import FrTranslateRunner
    assert FrTranslateRunner.tts_language_code == "fr"
    assert FrTranslateRunner.tts_model_id == "eleven_multilingual_v2"
    assert FrTranslateRunner.tts_default_voice_language == "fr"
    assert FrTranslateRunner.localization_module == "pipeline.localization_fr"
    assert FrTranslateRunner.target_language_label == "fr"


def test_run_av_localize_fallback_to_v1(tmp_path, monkeypatch):
    task_id = "test_av_localize_fallback"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    runner, _events = _make_runner()
    captured = {}

    monkeypatch.setattr("config.AV_LOCALIZE_FALLBACK", True)
    monkeypatch.setattr(
        "appcore.runtime.run_localize",
        lambda task_id, runner=None, variant="normal": captured.update(
            {"task_id": task_id, "runner": runner, "variant": variant}
        ),
    )

    with patch("appcore.source_video.ensure_local_source_video", lambda task_id: None):
        import appcore.runtime as runtime

        runtime.run_av_localize(task_id, runner=runner)

    assert captured == {
        "task_id": task_id,
        "runner": runner,
        "variant": "normal",
    }


def test_dispatch_localize_routes_av_pipeline_version(tmp_path, monkeypatch):
    task_id = "test_dispatch_av_pipeline"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(task_id, pipeline_version="av")
    captured = {}

    monkeypatch.setattr(
        "appcore.runtime.run_av_localize",
        lambda task_id, runner=None, variant="av": captured.setdefault(
            "av", {"task_id": task_id, "runner": runner, "variant": variant}
        ),
    )
    monkeypatch.setattr(
        "appcore.runtime.run_localize",
        lambda task_id, runner=None, variant="normal": captured.setdefault(
            "legacy", {"task_id": task_id, "runner": runner, "variant": variant}
        ),
    )

    import appcore.runtime as runtime

    runtime.dispatch_localize(task_id)

    assert "legacy" not in captured
    assert captured["av"]["task_id"] == task_id
    assert captured["av"]["variant"] == "av"


def test_run_av_localize_fails_when_market_missing(tmp_path, monkeypatch):
    task_id = "test_av_localize_missing_market"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        script_segments=[
            {"index": 0, "text": "原文", "start_time": 0.0, "end_time": 1.0},
        ],
        av_translate_inputs={
            "target_language": "en",
            "target_language_name": "English",
            "target_market": None,
            "product_overrides": {},
        },
    )
    runner, _events = _make_runner()
    stage_calls = []

    monkeypatch.setattr("config.AV_LOCALIZE_FALLBACK", False)
    monkeypatch.setattr(
        "pipeline.shot_notes.generate_shot_notes",
        lambda **kwargs: stage_calls.append("shot_notes"),
    )

    with patch("appcore.source_video.ensure_local_source_video", lambda task_id: None):
        import appcore.runtime as runtime

        runtime.run_av_localize(task_id, runner=runner)

    saved = task_state.get(task_id)
    assert saved["status"] == "failed"
    assert saved["steps"]["translate"] == "error"
    assert "target_market" in saved["error"]
    assert stage_calls == []


def test_run_av_localize_happy_flow(tmp_path, monkeypatch):
    task_id = "test_av_localize_happy_flow"
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake-video")
    task_state.create(task_id, str(video_path), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        script_segments=[
            {"index": 0, "text": "第一句", "start_time": 0.0, "end_time": 1.0},
            {"index": 1, "text": "第二句", "start_time": 1.0, "end_time": 2.2},
        ],
        recommended_voice_id="voice-1",
        av_translate_inputs={
            "target_language": "en",
            "target_language_name": "English",
            "target_market": "US",
            "product_overrides": {
                "product_name": None,
                "brand": None,
                "selling_points": None,
                "price": None,
                "target_audience": None,
                "extra_info": None,
            },
        },
    )
    runner, _events = _make_runner()
    call_order = []

    shot_notes = {
        "global": {"overall_theme": "海边场景"},
        "sentences": [
            {"asr_index": 0, "scene": "桌面", "action": "展示产品"},
            {"asr_index": 1, "scene": "近景", "action": "强调卖点"},
        ],
    }
    av_output = {
        "sentences": [
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 1.0,
                "target_duration": 1.0,
                "target_chars_range": (8, 10),
                "source_text": "第一句",
                "shot_context": {"scene": "桌面"},
                "role_in_structure": "hook",
                "text": "First line",
                "est_chars": 10,
            },
            {
                "asr_index": 1,
                "start_time": 1.0,
                "end_time": 2.2,
                "target_duration": 1.2,
                "target_chars_range": (10, 12),
                "source_text": "第二句",
                "shot_context": {"scene": "近景"},
                "role_in_structure": "cta",
                "text": "Second line",
                "est_chars": 11,
            },
        ],
    }
    tts_output = {
        "full_audio_path": str(tmp_path / "tts_full.av.mp3"),
        "segments": [
            {
                "index": 0,
                "asr_index": 0,
                "translated": "First line",
                "tts_duration": 1.0,
                "tts_path": str(tmp_path / "seg0.mp3"),
            },
            {
                "index": 1,
                "asr_index": 1,
                "translated": "Second line",
                "tts_duration": 1.1,
                "tts_path": str(tmp_path / "seg1.mp3"),
            },
        ],
    }
    final_sentences = [
        {
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 1.0,
            "target_duration": 1.0,
            "target_chars_range": (8, 10),
            "text": "First line",
            "tts_duration": 1.0,
            "tts_path": str(tmp_path / "seg0.mp3"),
            "speed": 1.0,
            "rewrite_rounds": 0,
            "status": "ok",
        },
        {
            "asr_index": 1,
            "start_time": 1.0,
            "end_time": 2.2,
            "target_duration": 1.2,
            "target_chars_range": (10, 12),
            "text": "Second line",
            "tts_duration": 1.1,
            "tts_path": str(tmp_path / "seg1.mp3"),
            "speed": 1.02,
            "rewrite_rounds": 0,
            "status": "speed_adjusted",
        },
    ]

    monkeypatch.setattr("config.AV_LOCALIZE_FALLBACK", False)
    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.shot_notes.generate_shot_notes",
        lambda **kwargs: call_order.append("shot_notes") or shot_notes,
    )
    monkeypatch.setattr(
        "pipeline.av_translate.generate_av_localized_translation",
        lambda **kwargs: call_order.append("av_translate") or av_output,
    )
    monkeypatch.setattr(
        "pipeline.tts.get_voice_by_id",
        lambda voice_id, user_id=None: {
            "id": voice_id,
            "name": "Voice 1",
            "elevenlabs_voice_id": "el_voice_1",
        },
    )
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda segments, voice_id, output_dir, variant=None, **kwargs: call_order.append("tts") or tts_output,
    )
    monkeypatch.setattr(
        "appcore.runtime.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True, "answer": "是"},
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.reconcile_duration",
        lambda **kwargs: call_order.append("reconcile") or final_sentences,
    )
    monkeypatch.setattr(
        "pipeline.subtitle.build_srt_from_tts",
        lambda segments: call_order.append("subtitle") or "1\n00:00:00,000 --> 00:00:01,000\nFirst line\n",
    )

    import appcore.runtime as runtime

    runtime.run_av_localize(task_id, runner=runner)

    saved = task_state.get(task_id)
    assert call_order == ["shot_notes", "av_translate", "tts", "reconcile", "subtitle"]
    assert saved["steps"]["translate"] == "done"
    assert saved["steps"]["tts"] == "done"
    assert saved["steps"]["subtitle"] == "done"
    assert saved["shot_notes"]["global"]["overall_theme"] == "海边场景"
    assert saved["variants"]["av"]["voice_id"] == "voice-1"
    assert saved["variants"]["av"]["sentences"][1]["status"] == "speed_adjusted"
    assert saved["variants"]["av"]["tts_audio_path"].endswith("tts_full.av.mp3")
    assert saved["variants"]["av"]["srt_path"].endswith("subtitle.av.srt")


def test_step_translate_dispatches_av_pipeline_version(tmp_path, monkeypatch):
    task_id = "test_step_translate_dispatches_av"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(task_id, pipeline_version="av")
    runner, _events = _make_runner()
    captured = {}

    monkeypatch.setitem(
        runner._step_translate.__func__.__globals__,
        "run_av_localize",
        lambda task_id, runner=None, variant="av": captured.update(
            {"task_id": task_id, "runner": runner, "variant": variant}
        ),
    )

    runner._step_translate(task_id)

    assert captured == {
        "task_id": task_id,
        "runner": runner,
        "variant": "av",
    }
