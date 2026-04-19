"""translate_lab V2 流水线端到端集成测试。

所有外部副作用（ffmpeg / Gemini / TOS / ElevenLabs / resemblyzer /
DB / compose_video）全部 mock，验证 7 步全跑通 + 关键事件正确发射。
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from appcore.events import EventBus
from appcore.runtime_v2 import PipelineRunnerV2


def _build_task_state():
    """返回 (get_fn, update_fn, state_dict) 三元组，模拟 task_state。"""
    state: dict = {
        "id": "t1",
        "type": "translate_lab",
        "_user_id": 1,
        "video_path": "",
        "task_dir": "",
        "source_language": "zh",
        "target_language": "en",
        "voice_match_mode": "auto",
        "steps": {
            "extract": "pending",
            "asr": "pending",
            "shot_decompose": "pending",
            "voice_match": "pending",
            "translate": "pending",
            "tts": "pending",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
        "step_messages": {},
    }

    def fake_get(task_id):
        return state

    def fake_update(task_id, **fields):
        state.update(fields)

    def fake_set_step(task_id, step, status):
        state.setdefault("steps", {})[step] = status

    def fake_set_step_message(task_id, step, message):
        state.setdefault("step_messages", {})[step] = message

    def fake_set_preview_file(task_id, name, path):
        state.setdefault("preview_files", {})[name] = path

    def fake_set_expires_at(task_id, project_type):
        state["_expires_for"] = project_type

    return state, {
        "get": fake_get,
        "update": fake_update,
        "set_step": fake_set_step,
        "set_step_message": fake_set_step_message,
        "set_preview_file": fake_set_preview_file,
        "set_expires_at": fake_set_expires_at,
    }


def test_full_pipeline_integration(tmp_path):
    """端到端：全部外部调用 mock，验证 7 步全跑通 + 事件正确。"""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    bus = EventBus()
    events: list[str] = []
    bus.subscribe(lambda e: events.append(e.type))

    state, ts = _build_task_state()
    state["video_path"] = str(video)
    state["task_dir"] = str(task_dir)

    shot = {
        "index": 1, "start": 0.0, "end": 30.0, "duration": 30.0,
        "description": "开场", "source_text": "原文",
        "asr_segments": [], "silent": False,
    }

    def fake_decompose(video_path, *, user_id, duration_seconds):
        return [dict(shot)]

    def fake_align(shots, asr_segments):
        return [dict(shot)]

    def fake_match(video_path, *, language, gender=None, top_k=3, out_dir):
        return [{
            "voice_id": "v1", "name": "A", "gender": "female",
            "language": language, "accent": "US", "preview_url": "",
            "similarity": 0.9,
        }]

    def fake_translate_shot(shot, *, target_language, char_limit,
                             prev_translation, next_source, user_id,
                             max_retries=2):
        return {
            "shot_index": shot["index"],
            "translated_text": "Hello.",
            "char_count": 6,
            "over_limit": False,
            "retries": 0,
        }

    audio_path = str(task_dir / "tts_v2" / "shot_1.mp3")

    def fake_generate_and_verify(shot, *, translated_text, voice_id,
                                  api_key, language, user_id,
                                  out_dir, max_retries=3):
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"shot_{shot['index']}.mp3"),
                  "wb") as fh:
            fh.write(b"fake-mp3")
        return {
            "shot_index": shot["index"],
            "final_text": translated_text,
            "final_char_count": len(translated_text),
            "final_duration": 2.0,
            "audio_path": os.path.join(out_dir, f"shot_{shot['index']}.mp3"),
            "retry_count": 0,
            "over_tolerance": False,
        }

    with patch("appcore.runtime_v2.task_state.get",
               side_effect=ts["get"]), \
         patch("appcore.runtime_v2.task_state.update",
               side_effect=ts["update"]), \
         patch("appcore.runtime_v2.task_state.set_step",
               side_effect=ts["set_step"]), \
         patch("appcore.runtime_v2.task_state.set_step_message",
               side_effect=ts["set_step_message"]), \
         patch("appcore.runtime_v2.task_state.set_preview_file",
               side_effect=ts["set_preview_file"]), \
         patch("appcore.runtime_v2.task_state.set_expires_at",
               side_effect=ts["set_expires_at"]), \
         patch("pipeline.extract.extract_audio",
               return_value=str(task_dir / "audio.wav")), \
         patch("pipeline.ffutil.probe_media_info",
               return_value={"duration": 30.0,
                             "width": 1920, "height": 1080}), \
         patch("pipeline.shot_decompose.decompose_shots",
               side_effect=fake_decompose), \
         patch("pipeline.shot_decompose.align_asr_to_shots",
               side_effect=fake_align), \
         patch("pipeline.storage.upload_file",
               return_value="http://example.com/a.wav"), \
         patch("pipeline.storage.delete_file"), \
         patch("pipeline.asr.transcribe",
               return_value=[{"start_time": 0.0, "end_time": 30.0,
                              "text": "原文"}]), \
         patch("pipeline.voice_match.match_for_video",
               side_effect=fake_match), \
         patch("pipeline.speech_rate_model.get_rate", return_value=15.0), \
         patch("pipeline.speech_rate_model.initialize_baseline",
               return_value=15.0), \
         patch("pipeline.translate_v2.translate_shot",
               side_effect=fake_translate_shot), \
         patch("pipeline.tts_v2.generate_and_verify_shot",
               side_effect=fake_generate_and_verify), \
         patch("pipeline.audio_stitch.subprocess.run"), \
         patch("pipeline.compose.compose_video",
               return_value={
                   "soft_video": str(task_dir / "soft.mp4"),
                   "hard_video": str(task_dir / "hard.mp4"),
                   "srt": str(task_dir / "subtitles.srt"),
               }), \
         patch("appcore.runtime_v2.resolve_key", return_value="k"):
        runner = PipelineRunnerV2(bus=bus, user_id=1)
        runner.start("t1")

    # 关键事件都发生
    assert "lab_shot_decompose_result" in events, events
    assert "lab_voice_match_candidates" in events, events
    assert "lab_voice_confirmed" in events, events
    assert "lab_translate_progress" in events, events
    assert "lab_pipeline_done" in events, events
    # 没有触发错误事件
    assert "lab_pipeline_error" not in events, events

    # 9 个步骤都被置为 done
    for name in ["extract", "asr", "shot_decompose", "voice_match",
                 "translate", "tts", "subtitle", "compose", "export"]:
        assert state["steps"].get(name) == "done", (name, state["steps"])

    # 最终视频路径被回写
    assert state.get("final_video")
    assert state.get("compose_result", {}).get("hard_video")


def test_pipeline_emits_error_event_on_failure(tmp_path):
    """管线任意步骤抛错时应发 lab_pipeline_error 事件，不再继续后续步骤。"""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    bus = EventBus()
    events: list[str] = []
    bus.subscribe(lambda e: events.append(e.type))

    state, ts = _build_task_state()
    state["video_path"] = str(video)
    state["task_dir"] = str(task_dir)

    with patch("appcore.runtime_v2.task_state.get",
               side_effect=ts["get"]), \
         patch("appcore.runtime_v2.task_state.update",
               side_effect=ts["update"]), \
         patch("appcore.runtime_v2.task_state.set_step",
               side_effect=ts["set_step"]), \
         patch("appcore.runtime_v2.task_state.set_step_message",
               side_effect=ts["set_step_message"]), \
         patch("appcore.runtime_v2.task_state.set_expires_at",
               side_effect=ts["set_expires_at"]), \
         patch("pipeline.extract.extract_audio",
               side_effect=RuntimeError("boom")), \
         patch("pipeline.ffutil.probe_media_info",
               return_value={"duration": 30.0}):
        runner = PipelineRunnerV2(bus=bus, user_id=1)
        runner.start("t1")

    assert "lab_pipeline_error" in events
    assert state.get("status") == "error"
    assert "boom" in (state.get("error") or "")
