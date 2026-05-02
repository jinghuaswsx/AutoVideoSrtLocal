from __future__ import annotations


def test_clear_av_compose_outputs_removes_stale_compose_and_export_state_without_mutating_inputs():
    from web.services.task_av_rewrite import clear_av_compose_outputs

    task = {
        "result": {"hard_video": "/task/hard.mp4", "soft_video": "/task/soft.mp4"},
        "exports": {
            "capcut_archive": "/task/capcut.zip",
            "capcut_project": "/task/project",
            "jianying_project_dir": "/jianying/project",
            "notes": "keep",
        },
        "artifacts": {
            "compose": {"items": []},
            "export": {"items": []},
            "tts": {"items": ["keep"]},
        },
        "preview_files": {
            "hard_video": "/task/hard.mp4",
            "tts_full_audio": "/task/tts.mp3",
            "srt": "/task/subtitle.srt",
        },
        "tos_uploads": {
            "av:srt": {"variant": "av", "tos_key": "av-srt"},
            "custom:hard": {"variant": "av", "tos_key": "av-hard"},
            "normal:hard_video": {"variant": "normal", "tos_key": "normal-hard"},
            "legacy": "keep",
        },
    }
    variant_state = {
        "result": {"hard_video": "/task/av-hard.mp4", "soft_video": "/task/av-soft.mp4"},
        "exports": {"capcut_archive": "/task/av-capcut.zip"},
        "artifacts": {"compose": {}, "export": {}, "tts": {"items": ["keep"]}},
        "preview_files": {"hard_video": "/task/av-hard.mp4", "tts_full_audio": "/task/av-tts.mp3"},
    }

    cleared = clear_av_compose_outputs(task, variant_state, variant="av")

    assert cleared.result == {"soft_video": "/task/soft.mp4"}
    assert cleared.exports == {"notes": "keep"}
    assert cleared.artifacts == {"tts": {"items": ["keep"]}}
    assert cleared.preview_files == {
        "tts_full_audio": "/task/tts.mp3",
        "srt": "/task/subtitle.srt",
    }
    assert cleared.tos_uploads == {
        "normal:hard_video": {"variant": "normal", "tos_key": "normal-hard"},
        "legacy": "keep",
    }
    assert cleared.variant_result == {}
    assert cleared.variant_exports == {}
    assert cleared.variant_artifacts == {"tts": {"items": ["keep"]}}
    assert cleared.variant_preview_files == {"tts_full_audio": "/task/av-tts.mp3"}

    assert task["result"]["hard_video"] == "/task/hard.mp4"
    assert "av:srt" in task["tos_uploads"]
    assert variant_state["result"]["hard_video"] == "/task/av-hard.mp4"


def test_resolve_av_voice_ids_uses_variant_voice_and_database_mapping():
    from web.services.task_av_rewrite import resolve_av_voice_ids

    calls = []

    def fake_get_voice_by_id(voice_id, user_id):
        calls.append((voice_id, user_id))
        return {
            "id": "voice-row-id",
            "elevenlabs_voice_id": "elevenlabs-voice-id",
        }

    resolved_voice_id, elevenlabs_voice_id = resolve_av_voice_ids(
        {"voice_id": "task-voice", "recommended_voice_id": "recommended-voice"},
        {"voice_id": "variant-voice"},
        user_id=42,
        get_voice_by_id=fake_get_voice_by_id,
    )

    assert calls == [("variant-voice", 42)]
    assert resolved_voice_id == "voice-row-id"
    assert elevenlabs_voice_id == "elevenlabs-voice-id"


def test_resolve_av_voice_ids_falls_back_to_stored_string_when_lookup_fails():
    from web.services.task_av_rewrite import resolve_av_voice_ids

    def failing_get_voice_by_id(_voice_id, _user_id):
        raise RuntimeError("voice lookup failed")

    resolved_voice_id, elevenlabs_voice_id = resolve_av_voice_ids(
        {"recommended_voice_id": "recommended-voice"},
        {},
        user_id=42,
        get_voice_by_id=failing_get_voice_by_id,
    )

    assert resolved_voice_id == "recommended-voice"
    assert elevenlabs_voice_id == "recommended-voice"
