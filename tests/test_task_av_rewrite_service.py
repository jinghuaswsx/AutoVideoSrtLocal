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
