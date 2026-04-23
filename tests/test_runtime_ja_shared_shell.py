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
