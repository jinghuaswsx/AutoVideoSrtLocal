"""
Pipeline runner integration tests.

Now tests appcore.runtime directly; web/services/pipeline_runner is a thin adapter.
"""
import appcore.runtime as runtime
from appcore.events import EventBus
from web import store


def _silent_bus():
    """EventBus that discards all events (no socketio needed in tests)."""
    return EventBus()


def test_step_alignment_auto_confirms_when_interactive_review_disabled(tmp_path, monkeypatch):
    task = store.create("task-auto-alignment", "video.mp4", str(tmp_path))
    task["utterances"] = [
        {"text": "hello", "start_time": 0.0, "end_time": 0.8},
        {"text": "world", "start_time": 0.8, "end_time": 1.6},
    ]

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
        def recommend_voice(self, user_id, text):
            return {"id": "adam"}

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_alignment("task-auto-alignment", "video.mp4", str(tmp_path))

    saved = store.get("task-auto-alignment")
    assert saved["_alignment_confirmed"] is True
    assert saved["steps"]["alignment"] == "done"
    assert saved["script_segments"][0]["text"] == "hello world"


def test_step_alignment_waits_when_interactive_review_enabled(tmp_path, monkeypatch):
    task = store.create("task-manual-alignment", "video.mp4", str(tmp_path))
    task["interactive_review"] = True
    task["utterances"] = [
        {"text": "hello", "start_time": 0.0, "end_time": 0.8},
        {"text": "world", "start_time": 0.8, "end_time": 1.6},
    ]

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
        def recommend_voice(self, user_id, text):
            return {"id": "adam"}

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_alignment("task-manual-alignment", "video.mp4", str(tmp_path))

    saved = store.get("task-manual-alignment")
    assert saved["steps"]["alignment"] == "waiting"
    assert saved["_alignment_confirmed"] is False
    assert saved["current_review_step"] == "alignment"
    segments_item = next(
        item for item in saved["artifacts"]["alignment"]["items"]
        if item.get("segments")
    )
    assert segments_item["segments"][0]["text"] == "hello world"


def test_step_translate_persists_source_text_and_localized_translation(tmp_path, monkeypatch):
    task = store.create("task-localized", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one\npart two")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal", **kwargs: {
            "full_text": "Hook line. Closing line.",
            "sentences": [
                {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
                {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
            ],
        },
    )

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_translate("task-localized")

    saved = store.get("task-localized")
    assert saved["steps"]["translate"] == "done"
    assert saved["source_full_text_zh"] == "part one\npart two"
    assert saved["localized_translation"]["full_text"] == "Hook line. Closing line."


def test_step_translate_logs_ai_billing_for_localize(tmp_path, monkeypatch):
    task = store.create("task-localized-billing", "video.mp4", str(tmp_path), user_id=7)
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one")
    monkeypatch.setattr("appcore.runtime._resolve_translate_provider", lambda user_id: "vertex_gemini_31_flash_lite")
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "gemini-3.1-flash-lite-preview")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal", **kwargs: {
            "full_text": "Hook line.",
            "sentences": [
                {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
            ],
            "_usage": {"input_tokens": 12, "output_tokens": 8},
        },
    )
    billing_calls = []
    monkeypatch.setattr(runtime.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    runner = runtime.PipelineRunner(bus=_silent_bus(), user_id=7)
    runner._step_translate("task-localized-billing")

    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_translate.localize"
    assert billing_calls[0]["user_id"] == 7
    assert billing_calls[0]["project_id"] == "task-localized-billing"
    assert billing_calls[0]["provider"] == "gemini_vertex"
    assert billing_calls[0]["model"] == "gemini-3.1-flash-lite-preview"
    assert billing_calls[0]["input_tokens"] == 12
    assert billing_calls[0]["output_tokens"] == 8
    assert billing_calls[0]["units_type"] == "tokens"
    assert billing_calls[0]["success"] is True


def test_step_asr_logs_ai_billing_with_audio_seconds(tmp_path, monkeypatch):
    task = store.create("task-asr-billing", "video.mp4", str(tmp_path), user_id=9)
    task["audio_path"] = str(tmp_path / "audio.wav")

    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda user_id, service, env_key: "volc-key")
    monkeypatch.setattr("pipeline.storage.upload_file", lambda path, tos_key: "https://example.com/audio.wav")
    monkeypatch.setattr("pipeline.storage.delete_file", lambda tos_key: None)
    monkeypatch.setattr(
        "pipeline.asr.transcribe",
        lambda audio_url, volc_api_key=None: [
            {"start_time": 0.0, "end_time": 12.4, "text": "hello"},
        ],
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 12.4)
    billing_calls = []
    monkeypatch.setattr(runtime.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    runner = runtime.PipelineRunner(bus=_silent_bus(), user_id=9)
    runner._step_asr("task-asr-billing", str(tmp_path))

    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_translate.asr"
    assert billing_calls[0]["provider"] == "doubao_asr"
    assert billing_calls[0]["model"] == "big-model"
    assert billing_calls[0]["units_type"] == "seconds"
    assert billing_calls[0]["audio_duration_seconds"] == 12.4
    assert billing_calls[0]["request_units"] == 13


def test_step_translate_waits_when_interactive_review_enabled(tmp_path, monkeypatch):
    task = store.create("task-manual-translate", "video.mp4", str(tmp_path))
    task["interactive_review"] = True
    task["script_segments"] = [
        {"index": 0, "text": "hello there", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "hello there")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal", **kwargs: {
            "full_text": "Hello there",
            "sentences": [
                {"index": 0, "text": "Hello there", "source_segment_indices": [0]},
            ],
        },
    )

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_translate("task-manual-translate")

    saved = store.get("task-manual-translate")
    assert saved["steps"]["translate"] == "waiting"
    assert saved["_segments_confirmed"] is False
    assert saved["current_review_step"] == "translate"
    assert saved["segments"][0]["translated"] == "Hello there"
    assert saved["artifacts"]["translate"]["layout"] == "variant_compare"


def test_step_translate_populates_both_variants(tmp_path, monkeypatch):
    task = store.create("task-variant-translate", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one\npart two")

    def fake_generate_localized_translation(source_full_text_zh, script_segments, variant="normal", **kwargs):
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

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_translate("task-variant-translate")

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

    monkeypatch.setattr(
        "pipeline.tts.get_voice_by_id",
        lambda vid, user_id=None: {"id": vid, "name": "Adam", "elevenlabs_voice_id": "el_adam"},
    )
    monkeypatch.setattr(
        "pipeline.translate.generate_tts_script",
        lambda localized_translation, **kwargs: {
            "full_text": localized_translation["full_text"],
            "subtitle_chunks": [],
            "sentences": localized_translation["sentences"],
        },
    )
    monkeypatch.setattr(
        "pipeline.localization.build_tts_segments",
        lambda tts_script, script_segments: [],
    )

    def fake_generate_full_audio(tts_segments, voice_id, output_dir, variant="normal", **kwargs):
        path = str(tmp_path / f"tts_full.{variant}.mp3")
        open(path, "wb").write(b"fake")
        return {"full_audio_path": path, "segments": []}

    monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_generate_full_audio)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 1.2)
    monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.usage_log.record", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "pipeline.timeline.build_timeline_manifest",
        lambda segments, video_duration: {"segments": segments, "video_duration": video_duration, "total_tts_duration": 1.2},
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 10.0)

    runner = runtime.PipelineRunner(bus=_silent_bus())

    def fake_run_tts_duration_loop(**kwargs):
        round_path = tmp_path / f"tts_full.round_1.{kwargs['variant']}.mp3"
        round_path.write_bytes(b"fake")
        localized_translation = kwargs["initial_localized_translation"]
        return {
            "localized_translation": localized_translation,
            "tts_script": {
                "full_text": localized_translation["full_text"],
                "subtitle_chunks": [],
                "sentences": localized_translation["sentences"],
            },
            "tts_audio_path": str(round_path),
            "tts_segments": [],
            "rounds": [{"round": 1, "audio_duration": 1.2}],
            "final_round": 1,
        }

    monkeypatch.setattr(runner, "_run_tts_duration_loop", fake_run_tts_duration_loop)
    runner._step_tts("task-variant-tts", str(tmp_path))

    saved = store.get("task-variant-tts")
    assert saved["variants"]["normal"]["tts_script"]["full_text"] == "Normal copy."
    assert saved["variants"]["hook_cta"]["tts_script"]["full_text"] == "Hook CTA copy."
    assert saved["variants"]["normal"]["tts_audio_path"].endswith("tts_full.normal.mp3")
    assert saved["variants"]["hook_cta"]["tts_audio_path"].endswith("tts_full.hook_cta.mp3")


def test_step_export_populates_variant_capcut_download_urls(tmp_path, monkeypatch):
    task = store.create("task-export-download-links", "video.mp4", str(tmp_path))
    task["video_path"] = "video.mp4"
    task["subtitle_position"] = "bottom"

    for variant in ("normal", "hook_cta"):
        audio_path = tmp_path / f"tts_full.{variant}.mp3"
        audio_path.write_bytes(b"fake-audio")
        srt_path = tmp_path / f"subtitle.{variant}.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        task["variants"][variant].update(
            {
                "tts_audio_path": str(audio_path),
                "srt_path": str(srt_path),
                "timeline_manifest": {"segments": [], "total_tts_duration": 1.0},
            }
        )

    def fake_export_capcut_project(video_path=None, tts_audio_path=None, srt_path=None, output_dir=None, timeline_manifest=None, variant="normal", **kwargs):
        manifest_path = tmp_path / f"manifest.{variant}.json"
        manifest_path.write_text("{}", encoding="utf-8")
        return {
            "project_dir": str(tmp_path / f"capcut_{variant}"),
            "archive_path": str(tmp_path / f"capcut_{variant}.zip"),
            "manifest_path": str(manifest_path),
            "jianying_project_dir": "",
        }

    monkeypatch.setattr("pipeline.capcut.export_capcut_project", fake_export_capcut_project)

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_export("task-export-download-links", "video.mp4", str(tmp_path))

    saved = store.get("task-export-download-links")
    export_artifact = saved["artifacts"]["export"]

    assert export_artifact["variants"]["normal"]["items"][0]["url"] == "/api/tasks/task-export-download-links/download/capcut?variant=normal"
    assert export_artifact["variants"]["hook_cta"]["items"][0]["url"] == "/api/tasks/task-export-download-links/download/capcut?variant=hook_cta"


def test_step_export_passes_user_jianying_root_to_capcut_export(tmp_path, monkeypatch):
    task = store.create("task-export-jianying-root", "video.mp4", str(tmp_path), user_id=123)
    task["video_path"] = "video.mp4"
    task["subtitle_position"] = "bottom"

    for variant in ("normal", "hook_cta"):
        audio_path = tmp_path / f"tts_full.{variant}.mp3"
        audio_path.write_bytes(b"fake-audio")
        srt_path = tmp_path / f"subtitle.{variant}.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        task["variants"][variant].update(
            {
                "tts_audio_path": str(audio_path),
                "srt_path": str(srt_path),
                "timeline_manifest": {"segments": [], "total_tts_duration": 1.0},
            }
        )

    captured_roots = {}

    def fake_export_capcut_project(video_path=None, tts_audio_path=None, srt_path=None, output_dir=None, timeline_manifest=None, variant="normal", jianying_project_root=None, **kwargs):
        captured_roots[variant] = jianying_project_root
        manifest_path = tmp_path / f"manifest.{variant}.json"
        manifest_path.write_text("{}", encoding="utf-8")
        return {
            "project_dir": str(tmp_path / f"capcut_{variant}"),
            "archive_path": str(tmp_path / f"capcut_{variant}.zip"),
            "manifest_path": str(manifest_path),
            "jianying_project_dir": "",
        }

    monkeypatch.setattr("appcore.runtime.resolve_jianying_project_root", lambda user_id: r"D:\JianyingDrafts")
    monkeypatch.setattr("pipeline.capcut.export_capcut_project", fake_export_capcut_project)

    runner = runtime.PipelineRunner(bus=_silent_bus(), user_id=123)
    runner._step_export("task-export-jianying-root", "video.mp4", str(tmp_path))

    assert captured_roots == {
        "normal": r"D:\JianyingDrafts",
        "hook_cta": r"D:\JianyingDrafts",
    }


def test_step_export_passes_display_name_to_capcut_export(tmp_path, monkeypatch):
    task = store.create("task-export-display-name", "video.mp4", str(tmp_path), user_id=123)
    task["video_path"] = "video.mp4"
    task["display_name"] = "example"
    task["subtitle_position"] = "bottom"

    for variant in ("normal", "hook_cta"):
        audio_path = tmp_path / f"tts_full.{variant}.mp3"
        audio_path.write_bytes(b"fake-audio")
        srt_path = tmp_path / f"subtitle.{variant}.srt"
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        task["variants"][variant].update(
            {
                "tts_audio_path": str(audio_path),
                "srt_path": str(srt_path),
                "timeline_manifest": {"segments": [], "total_tts_duration": 1.0},
            }
        )

    captured_titles = {}

    def fake_export_capcut_project(video_path=None, tts_audio_path=None, srt_path=None, output_dir=None, timeline_manifest=None, variant="normal", draft_title=None, **kwargs):
        captured_titles[variant] = draft_title
        manifest_path = tmp_path / f"manifest.{variant}.json"
        manifest_path.write_text("{}", encoding="utf-8")
        return {
            "project_dir": str(tmp_path / f"{draft_title}_capcut_{variant}"),
            "archive_path": str(tmp_path / f"{draft_title}_capcut_{variant}.zip"),
            "manifest_path": str(manifest_path),
            "jianying_project_dir": "",
        }

    monkeypatch.setattr("appcore.runtime.resolve_jianying_project_root", lambda user_id: r"D:\JianyingDrafts")
    monkeypatch.setattr("pipeline.capcut.export_capcut_project", fake_export_capcut_project)

    runner = runtime.PipelineRunner(bus=_silent_bus(), user_id=123)
    runner._step_export("task-export-display-name", "video.mp4", str(tmp_path))

    assert captured_titles == {
        "normal": "example",
        "hook_cta": "example",
    }


def test_upload_artifacts_to_tos_keeps_new_artifacts_local_by_default(tmp_path, monkeypatch):
    task = store.create("task-upload-artifacts", "video.mp4", str(tmp_path), user_id=7)
    for variant in ("normal", "hook_cta"):
        soft_path = tmp_path / f"{variant}.soft.mp4"
        hard_path = tmp_path / f"{variant}.hard.mp4"
        srt_path = tmp_path / f"{variant}.subtitle.srt"
        capcut_path = tmp_path / f"{variant}.capcut.zip"
        soft_path.write_bytes(b"soft")
        hard_path.write_bytes(b"hard")
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        capcut_path.write_bytes(b"zip")
        task["variants"][variant]["result"] = {
            "soft_video": str(soft_path),
            "hard_video": str(hard_path),
        }
        task["variants"][variant]["srt_path"] = str(srt_path)
        task["variants"][variant]["exports"] = {"capcut_archive": str(capcut_path)}

    uploaded = []

    monkeypatch.setattr("appcore.runtime.tos_clients.is_tos_configured", lambda: True)
    monkeypatch.setattr(
        "appcore.runtime.tos_clients.upload_file",
        lambda local_path, object_key: uploaded.append((local_path, object_key)),
    )

    runtime._upload_artifacts_to_tos(task, "task-upload-artifacts")

    saved = store.get("task-upload-artifacts")["tos_uploads"]
    assert saved == {}
    assert uploaded == []


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

    monkeypatch.setattr(
        runtime.PipelineRunner,
        "_set_step",
        lambda self, tid, step, status, msg="": messages.append((step, status, msg))
        or __import__("appcore.task_state", fromlist=["set_step"]).set_step(tid, step, status),
    )
    monkeypatch.setattr(
        "pipeline.asr.transcribe_local_audio",
        lambda path, prefix="", **kwargs: [{"text": "hello there", "start_time": 0.0, "end_time": 1.0}],
    )
    monkeypatch.setattr(
        "pipeline.subtitle_alignment.align_subtitle_chunks_to_asr",
        lambda chunks, asr_result, total_duration=0.0: chunks,
    )
    monkeypatch.setattr(
        "pipeline.subtitle.build_srt_from_chunks",
        lambda chunks: "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
    )
    monkeypatch.setattr(
        "pipeline.subtitle.save_srt",
        lambda srt_content, output_dir, variant=None: str(tmp_path / f"subtitle.{variant}.srt"),
    )
    monkeypatch.setattr(
        "pipeline.compose.compose_video",
        lambda **kwargs: {
            "soft_video": str(tmp_path / f"soft.{kwargs.get('variant', 'normal')}.mp4"),
            "hard_video": str(tmp_path / f"hard.{kwargs.get('variant', 'normal')}.mp4"),
        },
    )

    def fake_export_capcut_project(video_path=None, tts_audio_path=None, srt_path=None, output_dir=None, timeline_manifest=None, variant="normal", **kwargs):
        manifest_path = tmp_path / f"manifest.{variant}.json"
        manifest_path.write_text("{}", encoding="utf-8")
        return {
            "project_dir": str(tmp_path / f"capcut_{variant}"),
            "archive_path": str(tmp_path / f"capcut_{variant}.zip"),
            "manifest_path": str(manifest_path),
            "jianying_project_dir": "",
        }

    monkeypatch.setattr("pipeline.capcut.export_capcut_project", fake_export_capcut_project)

    runner = runtime.PipelineRunner(bus=_silent_bus())
    runner._step_subtitle("task-tail-messages", str(tmp_path))
    runner._step_compose("task-tail-messages", "video.mp4", str(tmp_path))
    runner._step_export("task-tail-messages", "video.mp4", str(tmp_path))

    assert ("subtitle", "running", "正在根据英文音频校正字幕...") in messages
    assert ("subtitle", "done", "英文字幕生成完成") in messages
    assert ("compose", "running", "正在合成视频...") in messages
    assert ("compose", "done", "视频合成完成") in messages
    assert ("export", "running", "正在导出 CapCut 项目...") in messages
    assert ("export", "done", "CapCut 项目已导出") in messages


def test_video_creation_generate_uploads_local_assets_with_task_scoped_keys(tmp_path, monkeypatch):
    from web.routes.video_creation import _do_generate_v2

    video_path = tmp_path / "source.mp4"
    image_path = tmp_path / "cover.png"
    audio_path = tmp_path / "voice.wav"
    video_path.write_bytes(b"video")
    image_path.write_bytes(b"image")
    audio_path.write_bytes(b"audio")

    state = {
        "task_dir": str(tmp_path),
        "prompt": "demo prompt",
        "video_path": str(video_path),
        "image_paths": [str(image_path)],
        "audio_path": str(audio_path),
        "ratio": "9:16",
        "duration": 5,
        "generate_audio": True,
        "watermark": False,
        "user_id": 1,
    }

    monkeypatch.setattr("web.routes.video_creation._update_state", lambda task_id, updates: None)
    monkeypatch.setattr("web.routes.video_creation.db_execute", lambda sql, args: None)
    monkeypatch.setattr("web.routes.video_creation._emit_to_task", lambda task_id, event, payload: None)
    monkeypatch.setattr("web.routes.video_creation._shrink_image_if_oversize", lambda path: path)
    uploaded = []
    monkeypatch.setattr(
        "web.routes.video_creation.tos_upload",
        lambda local_path, object_key=None, expires=3600: uploaded.append((local_path, object_key, expires)) or f"https://example.com/{object_key}",
    )
    monkeypatch.setattr(
        "pipeline.seedance.generate_video_v2",
        lambda **kwargs: {"task_id": "seed-task-1", "video_url": ""},
    )

    _do_generate_v2("vc-public-source", "seedance-key", state)

    assert uploaded == [
        (str(video_path), "video-creation/1/vc-public-source/video/source.mp4", 86400),
        (str(image_path), "video-creation/1/vc-public-source/images/0-cover.png", 86400),
        (str(audio_path), "video-creation/1/vc-public-source/audio/voice.wav", 86400),
    ]


def test_start_route_defaults_interactive_review_to_false(authed_client_no_db, monkeypatch):
    store.create("task-start-auto", "video.mp4", "output/task-start-auto", user_id=1)
    captured = {}

    monkeypatch.setattr(
        "web.services.pipeline_runner.start",
        lambda task_id, user_id=None: captured.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post("/api/tasks/task-start-auto/start", json={})

    assert response.status_code == 200
    assert captured["task_id"] == "task-start-auto"
    assert store.get("task-start-auto")["interactive_review"] is False
