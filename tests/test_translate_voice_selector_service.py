import base64

from appcore.voice_ai_rank_cache import candidate_signature


def test_rematch_voice_selection_derives_gender_rankings_from_all_cache(monkeypatch):
    from web.services.translate_voice_selector import rematch_voice_selection

    all_candidates = [
        {"voice_id": "voice-a", "similarity": 0.95, "gender": "male", "llm_rank": 1, "llm_reason_summary": "stable"},
        {"voice_id": "voice-b", "similarity": 0.91, "gender": "female", "llm_rank": 2, "llm_reason_summary": "bright"},
    ]
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
        "utterances": [{"text": "hola mundo", "start_time": 0, "end_time": 1}],
        "voice_match_candidates": all_candidates,
        "voice_ai_rank_cache": {
            "all": {
                "condition": "all",
                "candidate_signature": candidate_signature(all_candidates),
                "candidates": all_candidates,
                "rankings": [
                    {"voice_id": "voice-a", "llm_rank": 1, "reason_summary": "stable"},
                    {"voice_id": "voice-b", "llm_rank": 2, "reason_summary": "bright"},
                ],
                "status": "done",
                "model": "google/gemini-3.5-flash",
                "provider": "openrouter",
                "debug": {"status": "done"},
                "candidate_limit": 10,
                "usage_log_id": 98765,
                "source": "llm",
            },
        },
    }

    monkeypatch.setattr(
        "appcore.video_translate_defaults.resolve_default_voice",
        lambda language, user_id=None: "default-voice-id",
    )
    monkeypatch.setattr(
        "pipeline.voice_embedding.deserialize_embedding",
        lambda raw: "decoded-embedding",
    )
    seen_match = {}

    def fake_match_candidates_speed_aware(*args, **kwargs):
        seen_match.update(kwargs)
        return [{"voice_id": "voice-b", "similarity": 0.91, "gender": "female"}]

    monkeypatch.setattr(
        "pipeline.voice_match_speed.match_candidates_speed_aware",
        fake_match_candidates_speed_aware,
    )
    monkeypatch.setattr(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        lambda language, voice_ids: [{"voice_id": voice_ids[0], "name": "B", "gender": "female"}],
    )

    result = rematch_voice_selection(
        state=state,
        owner_user_id=1,
        current_user_id=1,
        gender="female",
    )

    assert result.should_persist is True
    assert seen_match["gender"] == "female"
    assert seen_match["exclude_voice_ids"] == {"default-voice-id"}
    assert result.payload["voice_ai_rank_status"] == "derived_from_all"
    assert result.payload["voice_ai_rank_cache_key"] == "female"
    assert result.payload["candidates"][0]["voice_id"] == "voice-b"
    assert result.payload["candidates"][0]["llm_rank"] == 1
    assert result.state_updates["voice_ai_rank_usage_log_id"] == 98765


def test_rerun_voice_ai_ranking_uses_cache_before_llm(monkeypatch, tmp_path):
    from web.services.translate_voice_selector import rerun_voice_ai_ranking_for_state

    source = tmp_path / "voice_ai_ranking" / "source_sample.mp3"
    source.parent.mkdir()
    source.write_bytes(b"source-audio")
    candidates = [{"voice_id": "v1", "similarity": 0.9}]
    state = {
        "task_dir": str(tmp_path),
        "target_lang": "de",
        "voice_match_candidates": candidates,
        "voice_ai_rank_cache": {
            "all": {
                "condition": "all",
                "candidate_signature": candidate_signature(candidates),
                "candidates": [{"voice_id": "v1", "similarity": 0.9, "llm_rank": 1, "llm_reason_summary": "fit"}],
                "rankings": [{"voice_id": "v1", "llm_rank": 1, "reason_summary": "fit"}],
                "status": "done",
                "model": "google/gemini-3.5-flash",
                "provider": "openrouter",
                "debug": {"status": "done"},
                "candidate_limit": 10,
                "usage_log_id": 222,
                "source": "llm",
            },
        },
    }
    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("cached AI ranking should not rerun LLM")),
    )

    monkeypatch.setattr(
        "web.services.translate_voice_selector.is_voice_ai_auto_select_enabled",
        lambda: False,
    )

    result = rerun_voice_ai_ranking_for_state(
        task_id="task-ai",
        state=state,
        body={"gender": None},
        user_id=1,
    )

    assert result.payload["voice_ai_rank_cached"] is True
    assert result.payload["voice_ai_auto_select_enabled"] is False
    assert result.payload["voice_ai_rank_usage_log_id"] == 222
    assert result.payload["candidates"][0]["llm_rank"] == 1
    assert result.state_updates["voice_ai_rank_cache"]["all"]["usage_log_id"] == 222
