from web import store
from web.services import pipeline_runner


def test_step_alignment_auto_confirms_when_interactive_review_disabled(tmp_path, monkeypatch):
    task = store.create("task-auto-alignment", "video.mp4", str(tmp_path))
    task["utterances"] = [
        {"text": "hello", "start_time": 0.0, "end_time": 0.8},
        {"text": "world", "start_time": 0.8, "end_time": 1.6},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.alignment.detect_scene_cuts", lambda video_path: [])
    monkeypatch.setattr(
        "pipeline.alignment.compile_alignment",
        lambda utterances, scene_cuts=None: {
            "break_after": [False, True],
            "script_segments": [
                {"index": 0, "text": "hello world", "start_time": 0.0, "end_time": 1.6},
            ],
        },
    )

    class FakeVoiceLibrary:
        def recommend_voice(self, text):
            return {"id": "adam"}

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())

    pipeline_runner._step_alignment("task-auto-alignment", "video.mp4", str(tmp_path))

    saved = store.get("task-auto-alignment")
    assert saved["_alignment_confirmed"] is True
    assert saved["steps"]["alignment"] == "done"
    assert saved["script_segments"][0]["text"] == "hello world"


def test_step_translate_persists_source_text_and_localized_translation(tmp_path, monkeypatch):
    task = store.create("task-localized", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.0},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one\npart two")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal": {
            "full_text": "Hook line. Closing line.",
            "sentences": [
                {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
                {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
            ],
        },
    )

    pipeline_runner._step_translate("task-localized")

    saved = store.get("task-localized")
    assert saved["steps"]["translate"] == "done"
    assert saved["source_full_text_zh"] == "part one\npart two"
    assert saved["localized_translation"]["full_text"] == "Hook line. Closing line."


def test_step_translate_populates_both_variants(tmp_path, monkeypatch):
    task = store.create("task-variant-translate", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.0},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one\npart two")

    def fake_generate_localized_translation(source_full_text_zh, script_segments, variant="normal"):
        if variant == "hook_cta":
            return {
                "full_text": "Hook CTA copy.",
                "sentences": [
                    {"index": 0, "text": "Hook CTA copy.", "source_segment_indices": [0, 1]},
                ],
            }
        return {
            "full_text": "Normal copy.",
            "sentences": [
                {"index": 0, "text": "Normal copy.", "source_segment_indices": [0, 1]},
            ],
        }

    monkeypatch.setattr("pipeline.translate.generate_localized_translation", fake_generate_localized_translation)

    pipeline_runner._step_translate("task-variant-translate")

    saved = store.get("task-variant-translate")
    assert saved["variants"]["normal"]["localized_translation"]["full_text"] == "Normal copy."
    assert saved["variants"]["hook_cta"]["localized_translation"]["full_text"] == "Hook CTA copy."


def test_step_tts_populates_both_variant_outputs(tmp_path, monkeypatch):
    task = store.create("task-variant-tts", "video.mp4", str(tmp_path))
    task["video_path"] = "video.mp4"
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]
    task["recommended_voice_id"] = "adam"
    task["variants"]["normal"]["localized_translation"] = {
        "full_text": "Normal copy.",
        "sentences": [{"index": 0, "text": "Normal copy.", "source_segment_indices": [0]}],
    }
    task["variants"]["hook_cta"]["localized_translation"] = {
        "full_text": "Hook CTA copy.",
        "sentences": [{"index": 0, "text": "Hook CTA copy.", "source_segment_indices": [0]}],
    }

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 12.0)
    monkeypatch.setattr("pipeline.tts.get_voice_by_id", lambda voice_id: {"id": voice_id, "elevenlabs_voice_id": "voice_1"})
    monkeypatch.setattr("pipeline.tts.get_default_voice", lambda gender="male": {"id": "default", "elevenlabs_voice_id": "voice_1"})

    def fake_generate_tts_script(localized_translation):
        text = localized_translation["full_text"]
        return {
            "full_text": text,
            "blocks": [{"index": 0, "text": text, "sentence_indices": [0], "source_segment_indices": [0]}],
            "subtitle_chunks": [{"index": 0, "text": text.rstrip("."), "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]}],
        }

    monkeypatch.setattr("pipeline.translate.generate_tts_script", fake_generate_tts_script)
    monkeypatch.setattr(
        "pipeline.localization.build_tts_segments",
        lambda tts_script, script_segments: [
            {
                "index": 0,
                "text": script_segments[0]["text"],
                "translated": tts_script["full_text"],
                "tts_text": tts_script["full_text"],
                "source_segment_indices": [0],
                "start_time": 0.0,
                "end_time": 1.0,
            }
        ],
    )

    def fake_generate_full_audio(segments, voice_id, output_dir, variant=None):
        return {
            "full_audio_path": str(tmp_path / f"tts_full.{variant}.mp3"),
            "segments": [{**segments[0], "tts_path": f"{variant}.mp3", "tts_duration": 1.2}],
        }

    monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_generate_full_audio)
    monkeypatch.setattr(
        "pipeline.timeline.build_timeline_manifest",
        lambda segments, video_duration: {"segments": segments, "video_duration": video_duration, "total_tts_duration": 1.2},
    )

    pipeline_runner._step_tts("task-variant-tts", str(tmp_path))

    saved = store.get("task-variant-tts")
    assert saved["variants"]["normal"]["tts_script"]["full_text"] == "Normal copy."
    assert saved["variants"]["hook_cta"]["tts_script"]["full_text"] == "Hook CTA copy."
    assert saved["variants"]["normal"]["tts_audio_path"].endswith("tts_full.normal.mp3")
    assert saved["variants"]["hook_cta"]["tts_audio_path"].endswith("tts_full.hook_cta.mp3")


def test_tail_steps_emit_readable_chinese_messages(tmp_path, monkeypatch):
    task = store.create("task-tail-messages", "video.mp4", str(tmp_path))
    task["video_path"] = "video.mp4"
    task["subtitle_position"] = "bottom"

    for variant, text in (("normal", "Normal copy"), ("hook_cta", "Hook CTA copy")):
        audio_path = tmp_path / f"tts_full.{variant}.mp3"
        audio_path.write_bytes(b"fake-audio")
        srt_path = tmp_path / f"subtitle.{variant}.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        task["variants"][variant].update(
            {
                "tts_audio_path": str(audio_path),
                "srt_path": str(srt_path),
                "tts_script": {
                    "subtitle_chunks": [
                        {
                            "index": 0,
                            "text": text,
                            "block_indices": [0],
                            "sentence_indices": [0],
                            "source_segment_indices": [0],
                        }
                    ]
                },
                "timeline_manifest": {"segments": [], "total_tts_duration": 1.0},
            }
        )

    messages = []
    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pipeline_runner,
        "set_step",
        lambda task_id, step, status, message="": messages.append((step, status, message)),
    )
    monkeypatch.setattr(
        "pipeline.asr.transcribe_local_audio",
        lambda path, prefix="": [{"text": "hello there", "start_time": 0.0, "end_time": 1.0}],
    )
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 1.0)
    monkeypatch.setattr(
        "pipeline.subtitle_alignment.align_subtitle_chunks_to_asr",
        lambda subtitle_chunks, english_asr_result, total_duration=0.0: [
            {
                "index": 0,
                "text": subtitle_chunks[0]["text"],
                "start_time": 0.0,
                "end_time": 1.0,
            }
        ],
    )
    monkeypatch.setattr(
        "pipeline.compose.compose_video",
        lambda **kwargs: {
            "soft_video": str(tmp_path / f"{kwargs['variant']}_soft.mp4"),
            "hard_video": str(tmp_path / f"{kwargs['variant']}_hard.mp4"),
            "srt": kwargs["srt_path"],
        },
    )

    def fake_export_capcut_project(**kwargs):
        variant = kwargs["variant"]
        manifest_path = tmp_path / f"manifest.{variant}.json"
        manifest_path.write_text('{"backend":"pyJianYingDraft"}', encoding="utf-8")
        return {
            "project_dir": str(tmp_path / f"capcut_{variant}"),
            "archive_path": str(tmp_path / f"capcut_{variant}.zip"),
            "manifest_path": str(manifest_path),
        }

    monkeypatch.setattr("pipeline.capcut.export_capcut_project", fake_export_capcut_project)

    pipeline_runner._step_subtitle("task-tail-messages", str(tmp_path))
    pipeline_runner._step_compose("task-tail-messages", "video.mp4", str(tmp_path))
    pipeline_runner._step_export("task-tail-messages", "video.mp4", str(tmp_path))

    assert ("subtitle", "running", "正在根据英文音频校正字幕...") in messages
    assert ("subtitle", "done", "英文字幕生成完成") in messages
    assert ("compose", "running", "正在合成视频...") in messages
    assert ("compose", "done", "视频合成完成") in messages
    assert ("export", "running", "正在导出 CapCut 项目...") in messages
    assert ("export", "done", "CapCut 项目已导出") in messages


def test_start_route_defaults_interactive_review_to_false(monkeypatch):
    app = __import__("web.app", fromlist=["create_app"]).create_app()
    client = app.test_client()
    store.create("task-start-auto", "video.mp4", "output/task-start-auto")
    captured = {}

    monkeypatch.setattr("web.services.pipeline_runner.start", lambda task_id: captured.setdefault("task_id", task_id))

    response = client.post("/api/tasks/task-start-auto/start", json={})

    assert response.status_code == 200
    assert captured["task_id"] == "task-start-auto"
    assert store.get("task-start-auto")["interactive_review"] is False
