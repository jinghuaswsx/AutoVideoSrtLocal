import numpy as np
from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_step_voice_match_writes_candidates_to_state():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    source_utterances = [{"start_time": 0, "end_time": 10, "text": "hola mundo"}]
    normalized_utterances = [{"start_time": 0, "end_time": 10, "text": "hello world"}]
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "utterances": source_utterances,
        "utterances_en": normalized_utterances,
        "video_path": "/tmp/x/src.mp4",
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_multi.extract_sample_from_utterances",
               return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_multi.embed_audio_file",
               return_value=np.zeros(256, dtype=np.float32)), \
         patch("appcore.runtime_multi.resolve_default_voice",
                return_value="default-voice-id"), \
         patch("appcore.voice_ai_ranking.resolve_voice_ai_model_selection",
               return_value={
                   "provider": "gemini_aistudio",
                   "model": "gemini-3.5-flash",
                   "source": "db",
               }), \
         patch("pipeline.voice_match_speed.match_candidates_speed_aware") as m_match, \
         patch("appcore.voice_ai_ranking_task.queue_voice_ai_ranking") as m_ai_queue:
        m_match.return_value = [
            {"voice_id": "v1", "name": "A", "similarity": 0.85,
             "gender": "male", "preview_url": "u1"},
            {"voice_id": "v2", "name": "B", "similarity": 0.80,
             "gender": "male", "preview_url": "u2"},
            {"voice_id": "v3", "name": "C", "similarity": 0.74,
             "gender": "female", "preview_url": "u3"},
        ]
        runner._step_voice_match("t1")

    payload = m_update.call_args.kwargs
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}
    assert m_match.call_args.kwargs["candidate_pool_size"] == 20
    assert m_match.call_args.kwargs["top_k"] == 20
    assert m_match.call_args.kwargs["source_utterances"] == normalized_utterances
    assert payload["voice_match_candidates"][0]["voice_id"] == "v1"
    assert "llm_rank" not in payload["voice_match_candidates"][1]
    assert payload["voice_ai_rankings"] == []
    assert payload["voice_ai_rank_status"] == "running"
    assert payload["voice_ai_rank_model"] == "gemini-3.5-flash"
    assert payload["voice_ai_rank_provider"] == "gemini_aistudio"
    assert len(payload["voice_match_candidates"]) == 3
    m_ai_queue.assert_called_once()
    assert m_ai_queue.call_args.kwargs["candidates"] == payload["voice_match_candidates"]
    assert m_ai_queue.call_args.kwargs["source_audio_path"] == "/tmp/x/clip.wav"


def test_step_voice_match_fallback_when_empty():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x", "target_lang": "de",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hi"}],
        "video_path": "/tmp/x/src.mp4",
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_multi.extract_sample_from_utterances",
               return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_multi.embed_audio_file",
               return_value=np.zeros(256, dtype=np.float32)), \
         patch("pipeline.voice_match_speed.match_candidates_speed_aware", return_value=[]), \
         patch("appcore.runtime_multi.resolve_default_voice",
                return_value="default-voice-id"), \
         patch("appcore.voice_ai_ranking.resolve_voice_ai_model_selection",
               return_value={
                   "provider": "openrouter",
                   "model": "google/gemini-3.5-flash",
                   "source": "default",
               }):
        runner._step_voice_match("t1")

    payload = m_update.call_args.kwargs
    assert payload["voice_match_candidates"] == []
    assert payload.get("voice_match_fallback_voice_id") == "default-voice-id"
    assert payload["voice_ai_rankings"] == []
    assert payload["voice_ai_rank_status"] == "skipped"
    assert payload["voice_ai_rank_model"] == "google/gemini-3.5-flash"
    assert payload["voice_ai_rank_provider"] == "openrouter"


def test_step_voice_match_skips_when_original_video_passthrough_enabled():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hi"}],
        "video_path": "/tmp/x/src.mp4",
        "media_passthrough_mode": "original_video",
        "media_passthrough_reason": "short_asr",
        "media_passthrough_source_chars": 12,
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.task_state.set_current_review_step") as m_set_review, \
         patch("appcore.runtime_multi.extract_sample_from_utterances",
               side_effect=AssertionError("voice match should be skipped for passthrough tasks")):
        runner._step_voice_match("t1")

    payload = m_update.call_args.kwargs
    assert payload["voice_match_candidates"] == []
    assert payload["voice_match_fallback_voice_id"] is None
    assert payload["voice_match_query_embedding"] is None
    m_set_review.assert_called_with("t1", "")
