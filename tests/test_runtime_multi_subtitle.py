from unittest.mock import patch
from types import SimpleNamespace

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_step_subtitle_uses_lang_rules_for_weak_starters_and_post_process():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "fr",
        "variants": {
            "normal": {
                "tts_audio_path": "/tmp/x/audio.mp3",
                "tts_script": {"subtitle_chunks": [
                    {"text": "Bonjour les amis", "block_indices": [0],
                     "sentence_indices": [0], "source_segment_indices": [0]}
                ]},
            }
        },
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_preview_file"), \
         patch("appcore.asr_router.resolve_adapter",
               return_value=(SimpleNamespace(display_name="Scribe", model_id="scribe_v2"), {})), \
         patch("appcore.asr_router.transcribe",
               return_value={"utterances": [{"text": "Bonjour les amis", "start_time": 0, "end_time": 1}]}), \
         patch("appcore.runtime_multi._get_audio_duration", return_value=1.0), \
         patch("appcore.runtime_multi.align_subtitle_chunks_to_asr") as m_align, \
         patch("appcore.runtime_multi.build_srt_from_chunks") as m_build, \
         patch("appcore.runtime_multi.save_srt", return_value="/tmp/x/subtitle.srt"), \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime_multi.resolve_key", return_value="volc"):
        m_align.return_value = [{"text": "Bonjour les amis",
                                   "start_time": 0.0, "end_time": 1.0}]
        m_build.return_value = "1\n00:00:00,000 --> 00:00:01,000\nBonjour les amis ?\n"
        runner._step_subtitle("t1", "/tmp/x")

    kwargs = m_build.call_args.kwargs
    assert "et" in kwargs["weak_boundary_words"]
    assert "ou" in kwargs["weak_boundary_words"]
    assert kwargs["max_chars_per_line"] == 42
    assert kwargs["max_lines"] == 2
