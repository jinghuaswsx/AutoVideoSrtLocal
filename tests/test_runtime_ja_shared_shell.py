import numpy as np
from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_ja import JapaneseTranslateRunner


def test_ja_pipeline_inserts_voice_match_after_asr():
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    steps = runner._get_pipeline_steps("task-ja", "/tmp/demo.mp4", "/tmp/out")
    names = [name for name, _fn in steps]

    assert names[names.index("asr") + 1] == "voice_match"


def test_ja_voice_match_writes_candidates_to_state():
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "ja",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hello"}],
        "video_path": "/tmp/x/src.mp4",
    }

    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_ja.extract_sample_from_utterances", return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_ja.embed_audio_file", return_value=np.zeros(256, dtype=np.float32)), \
         patch("appcore.runtime_ja.resolve_default_voice", return_value="ja-default"), \
         patch("appcore.runtime_ja.match_candidates", return_value=[{"voice_id": "ja-1", "similarity": 0.9}]):
        runner._step_voice_match("task-ja")

    assert m_update.call_args.kwargs["voice_match_candidates"][0]["voice_id"] == "ja-1"


def test_ja_step_tts_sets_shared_final_duration_fields(tmp_path, monkeypatch):
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": str(tmp_path),
        "video_path": str(tmp_path / "src.mp4"),
        "script_segments": [{"index": 0, "text": "Store caps neatly."}],
        "variants": {
            "normal": {
                "localized_translation": {
                    "sentences": [{"index": 0, "text": "帽子をすっきり収納", "source_segment_indices": [0]}]
                }
            }
        },
    }

    monkeypatch.setattr("appcore.task_state.get", lambda task_id: task)
    updates = []
    monkeypatch.setattr("appcore.task_state.update", lambda task_id, **kwargs: updates.append(kwargs))
    monkeypatch.setattr("appcore.task_state.set_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.task_state.set_preview_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.runtime_ja.get_video_duration", lambda path: 11.7)
    monkeypatch.setattr("appcore.runtime_ja._tts_final_target_range", lambda duration: (10.7, 13.7))
    monkeypatch.setattr("appcore.runtime_ja.resolve_key", lambda *args, **kwargs: "eleven-key")
    monkeypatch.setattr("appcore.runtime_ja.resolve", lambda use_case_code: {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"})
    monkeypatch.setattr("appcore.runtime_ja.resolve_default_voice", lambda *args, **kwargs: "ja-default")
    monkeypatch.setattr(
        "appcore.runtime_ja.ja_translate.build_ja_tts_script",
        lambda localized: {"full_text": "帽子をすっきり収納", "blocks": [], "subtitle_chunks": []},
    )
    monkeypatch.setattr(
        "appcore.runtime_ja.ja_translate.build_ja_tts_segments",
        lambda script, segs: [{"translated": "帽子をすっきり収納"}],
    )
    monkeypatch.setattr(
        "appcore.runtime_ja.generate_full_audio",
        lambda *args, **kwargs: {
            "full_audio_path": str(tmp_path / "tts_full.ja_round_1.mp3"),
            "segments": [{"tts_duration": 11.9}],
        },
    )
    monkeypatch.setattr("appcore.runtime_ja._get_audio_duration", lambda path: 11.9)
    monkeypatch.setattr("appcore.runtime_ja.build_timeline_manifest", lambda *args, **kwargs: {})
    monkeypatch.setattr("appcore.runtime_ja.build_tts_artifact", lambda *args, **kwargs: {})
    monkeypatch.setattr("appcore.runtime_ja.speech_rate_model.update_rate", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.runtime_ja.ai_billing.log_request", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.runtime_ja.shutil.copy2", lambda *args, **kwargs: None)

    runner._step_tts("task-ja", str(tmp_path))

    final_update = [u for u in updates if "tts_final_round" in u][-1]
    assert final_update["tts_final_round"] == 1
    assert final_update["tts_final_reason"] == "converged"
    assert final_update["tts_duration_status"] == "converged"


def test_ja_step_tts_emits_per_segment_substeps(tmp_path, monkeypatch):
    """日语 TTS 也应该在每段 ElevenLabs 完成时发一条 substep msg。"""
    import os
    from appcore.events import EventBus, EVT_STEP_UPDATE
    from appcore.runtime_ja import JapaneseTranslateRunner

    bus = EventBus()
    captured = []
    bus.subscribe(lambda e: captured.append(e))

    def fake_gen_full_audio(tts_segments, voice_id, output_dir, *, variant=None,
                             on_progress=None, on_segment_done=None, **kw):
        out = os.path.join(output_dir, f"tts_full.{variant}.mp3")
        with open(out, "wb") as f:
            f.write(b"fake")

        def _emit_done(done, total, info):
            if on_progress:
                on_progress({"state": "completed", "total": total, "done": done,
                              "active": 0, "queued": total - done, "info": info})
            if on_segment_done:
                on_segment_done(done, total, info)

        _emit_done(1, 2, {"segment_index": 0})
        _emit_done(2, 2, {"segment_index": 1})
        return {"full_audio_path": out, "segments": [
            {"index": 0, "tts_path": out, "tts_duration": 1.0},
            {"index": 1, "tts_path": out, "tts_duration": 1.5},
        ]}

    monkeypatch.setattr("appcore.runtime_ja.generate_full_audio", fake_gen_full_audio)
    monkeypatch.setattr("appcore.runtime_ja._get_audio_duration", lambda p: 30.0)
    monkeypatch.setattr("appcore.runtime_ja.get_video_duration", lambda p: 30.0)
    monkeypatch.setattr("appcore.runtime_ja.resolve_key", lambda *a, **kw: "fake")

    import appcore.runtime_ja as rt_ja
    monkeypatch.setattr(rt_ja.ja_translate, "build_ja_tts_script",
                        lambda loc: {"full_text": "ハロー", "blocks": [],
                                     "subtitle_chunks": []})
    monkeypatch.setattr(rt_ja.ja_translate, "build_ja_tts_segments",
                        lambda script, segs: [
                            {"index": 0, "tts_text": "ハロー"},
                            {"index": 1, "tts_text": "ワールド"},
                        ])
    monkeypatch.setattr(rt_ja.ja_translate, "count_visible_japanese_chars",
                        lambda txt: 5)
    monkeypatch.setattr("pipeline.speech_rate_model.update_rate",
                        lambda *a, **kw: None)
    monkeypatch.setattr("appcore.runtime_ja.ai_billing.log_request",
                        lambda **kw: None)

    fake_task = {
        "task_dir": str(tmp_path),
        "video_path": str(tmp_path / "v.mp4"),
        "script_segments": [{"index": 0, "text": "hi",
                              "start_time": 0.0, "end_time": 1.0}],
        "localized_translation": {"full_text": "ハロー",
                                   "sentences": [{"text": "ハロー"}]},
        "variants": {},
    }
    monkeypatch.setattr("appcore.runtime_ja.task_state.get", lambda tid: fake_task)
    monkeypatch.setattr("appcore.runtime_ja.task_state.update", lambda tid, **kw: None)
    monkeypatch.setattr("appcore.runtime_ja.task_state.set_artifact", lambda *a, **kw: None)
    monkeypatch.setattr("appcore.runtime_ja.task_state.set_preview_file", lambda *a, **kw: None)

    runner = JapaneseTranslateRunner(bus=bus, user_id=1)
    monkeypatch.setattr(runner, "_resolve_voice", lambda task, mod: {
        "id": 1, "elevenlabs_voice_id": "vid"})

    runner._step_tts("ja-substep-task", str(tmp_path))

    msgs = [e.payload["message"] for e in captured if e.type == EVT_STEP_UPDATE]
    # 并发改造后文案统一为 "1/2（活跃 N 路）"
    assert any("1/2" in m and "活跃" in m for m in msgs), f"got: {msgs}"
    assert any("2/2" in m and "活跃" in m for m in msgs), f"got: {msgs}"
