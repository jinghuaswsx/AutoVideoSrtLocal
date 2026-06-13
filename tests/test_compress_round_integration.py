"""Block3 集成测试：压缩重译终轮在 5 轮未收敛后动态扩出。

构造一个"永不收敛"的任务（音频固定 50s，video=30s，永远落在 final 窗外），
验证：① compress 终轮被扩出（出现 compress_round=True 的轮记录）；
     ② compress 轮 target 瞄准 video−0.5s；
     ③ 关掉开关后 compress 轮不出现（与现状一致）。
"""
import os

import config


def _disable_db_and_language_guard(monkeypatch):
    """隔离 DB 持久化、计费汇总与 TTS 语言守门 LLM 调用。"""
    monkeypatch.setattr("appcore.task_state._db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr("appcore.task_state._sync_task_to_db", lambda *a, **kw: None)
    monkeypatch.setattr("appcore.task_state.set_expires_at", lambda *a, **kw: None)
    monkeypatch.setattr("appcore.db.query_one", lambda *a, **kw: None)
    monkeypatch.setattr("appcore.tts_generation_stats.finalize", lambda *a, **kw: None)

    def fake_language_check(**kwargs):
        return {
            "is_target_language": True,
            "detected_language": kwargs.get("target_language"),
            "confidence": 1.0,
            "reason": "test bypass",
            "problem_excerpt": "",
        }

    monkeypatch.setattr(
        "appcore.runtime.validate_tts_script_language_or_raise",
        fake_language_check, raising=False)
    monkeypatch.setattr(
        "appcore.runtime._pipeline_runner.validate_tts_script_language_or_raise",
        fake_language_check, raising=False)


def _make_runner():
    from appcore.events import EventBus
    from appcore.runtime import PipelineRunner
    bus = EventBus()
    return PipelineRunner(bus=bus, user_id=1)


def _wire_never_converge(monkeypatch, *, audio_duration=20.0):
    """所有 TTS 音频固定 audio_duration，永不落进 [video-1, video]。"""
    def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
        out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        with open(out, "wb") as f:
            f.write(b"fake")
        return {"full_audio_path": out,
                "segments": [{"index": 0, "tts_path": out,
                              "tts_duration": audio_duration}]}

    monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: audio_duration)
    monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
    monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

    # tts_script 固定文本（词数无关紧要，关键是音频时长永不收敛）。
    monkeypatch.setattr(
        "pipeline.translate.generate_tts_script",
        lambda loc, **kwargs: {
            "full_text": loc.get("full_text", "Short text."),
            "blocks": [{"index": 0, "text": "Short.", "sentence_indices": [0],
                        "source_segment_indices": [0]}],
            "subtitle_chunks": [],
        },
    )

    captured = {"rewrite_targets": []}

    def fake_rewrite(*, source_full_text, prev_localized_translation, target_words,
                     direction, **kwargs):
        captured["rewrite_targets"].append(target_words)
        # 让候选词数恰好等于 target_words → 永远落字数窗（隔离 rewrite 不收敛因素，
        # 专测时长不收敛 → compress 触发路径）。
        words = " ".join(["word"] * max(1, target_words))
        return {"full_text": words,
                "sentences": [{"index": 0, "text": words,
                               "source_segment_indices": [0]}]}

    monkeypatch.setattr("pipeline.translate.generate_localized_rewrite", fake_rewrite)

    import importlib
    loc_mod = importlib.import_module("pipeline.localization")
    monkeypatch.setattr(
        loc_mod, "build_tts_segments",
        lambda script, segs: [{"index": 0, "tts_text": "Short.", "tts_duration": 0.0}])
    # speed-up/段级拼装路径走不通时 fall back，保持简单：让段级拼装直接失败。
    return loc_mod, captured


def _run(monkeypatch, tmp_path, task_id, loc_mod):
    runner = _make_runner()
    from appcore import task_state
    # 隔离 DB + TTS 语言守门。
    _disable_db_and_language_guard(monkeypatch)
    task_state.create(task_id, "v.mp4", str(tmp_path),
                      original_filename="v.mp4", user_id=1)
    initial = {"full_text": "Short text.",
               "sentences": [{"index": 0, "text": "Short text.",
                              "source_segment_indices": [0]}]}
    voice = {"id": 1, "elevenlabs_voice_id": "test-voice"}
    return runner._run_tts_duration_loop(
        task_id=task_id, task_dir=str(tmp_path), loc_mod=loc_mod,
        provider="openrouter", video_duration=30.0, voice=voice,
        initial_localized_translation=initial, source_full_text="Source zh.",
        source_language="zh", elevenlabs_api_key="fake-key",
        script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
        variant="normal",
    )


def test_compress_round_triggers_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OMNI_REWRITE_GUARD_ENABLED", False)
    monkeypatch.setattr(config, "OMNI_COMPRESS_RETRANSLATE_ENABLED", True)
    loc_mod, captured = _wire_never_converge(monkeypatch)
    result = _run(monkeypatch, tmp_path, "tdl-compress-on", loc_mod)
    compress_rounds = [r for r in result["rounds"] if r.get("compress_round")]
    assert len(compress_rounds) == 1, "压缩重译终轮应被扩出且只有一轮"
    cr = compress_rounds[0]
    # video=30 → compress target_duration = 29.5；wps=15 → 442 words (29.5*15)
    assert cr["target_duration"] == 29.5
    assert cr["direction"] == "expand"  # audio 20 < video 30


def test_compress_round_absent_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OMNI_REWRITE_GUARD_ENABLED", False)
    monkeypatch.setattr(config, "OMNI_COMPRESS_RETRANSLATE_ENABLED", False)
    loc_mod, captured = _wire_never_converge(monkeypatch)
    result = _run(monkeypatch, tmp_path, "tdl-compress-off", loc_mod)
    compress_rounds = [r for r in result["rounds"] if r.get("compress_round")]
    assert compress_rounds == [], "开关关掉时不应出现压缩重译终轮"
