from __future__ import annotations

from types import SimpleNamespace


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


def test_rebuild_tts_full_audio_writes_concat_file_and_runs_ffmpeg(tmp_path):
    from web.services.task_av_rewrite import rebuild_tts_full_audio

    seg0 = tmp_path / "seg 0.mp3"
    seg1 = tmp_path / "seg'1.mp3"
    seg0.write_bytes(b"seg0")
    seg1.write_bytes(b"seg1")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    result = rebuild_tts_full_audio(
        str(tmp_path),
        [{"tts_path": str(seg0)}, {"tts_path": str(seg1)}],
        "av",
        run_command=fake_run,
    )

    concat_list = tmp_path / "tts_segments" / "av" / "concat.rewrite.txt"
    assert result == str(tmp_path / "tts_full.av.mp3")
    assert concat_list.read_text(encoding="utf-8") == (
        f"file '{seg0.resolve()}'\n"
        f"file '{str(seg1.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
    )
    assert calls == [
        (
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(tmp_path / "tts_full.av.mp3"),
            ],
            {"capture_output": True, "text": True},
        )
    ]


def test_rebuild_tts_full_audio_rejects_missing_segment(tmp_path):
    import pytest
    from web.services.task_av_rewrite import rebuild_tts_full_audio

    with pytest.raises(FileNotFoundError):
        rebuild_tts_full_audio(str(tmp_path), [{"tts_path": str(tmp_path / "missing.mp3")}])
