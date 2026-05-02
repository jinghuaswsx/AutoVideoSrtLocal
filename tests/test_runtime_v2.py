"""视频翻译（测试）V2 流水线 runner 单元测试。"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from appcore.cancellation import OperationCancelled
from appcore.events import EventBus
from appcore.runtime_v2 import PipelineRunnerV2


def test_runner_defines_nine_steps():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    names = [name for name, _fn in runner._build_steps(
        task_id="t1", video_path="/v.mp4", task_dir="/d",
    )]
    assert names == [
        "extract", "asr", "shot_decompose", "voice_match",
        "translate", "tts", "subtitle", "compose", "export",
    ]


def test_runner_project_type_is_translate_lab():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    assert runner.project_type == "translate_lab"


def test_await_voice_confirmation_returns_chosen_when_available():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)

    states = iter([
        {"pending_voice_choice": [{"voice_id": "a"}]},
        {"chosen_voice": {"voice_id": "a"}},
    ])

    with patch("appcore.runtime_v2.task_state.get",
               side_effect=lambda tid: next(states)), \
         patch("appcore.runtime_v2.task_state.update"), \
         patch("appcore.runtime_v2.cancellable_sleep"):
        result = runner._await_voice_confirmation(
            "t1", [{"voice_id": "a"}], poll_interval=0.0, timeout_seconds=5,
        )
    assert result == {"voice_id": "a"}


def test_await_voice_confirmation_times_out_to_none():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)

    with patch("appcore.runtime_v2.task_state.get",
               return_value={"chosen_voice": None}), \
         patch("appcore.runtime_v2.task_state.update"), \
         patch("appcore.runtime_v2.cancellable_sleep"):
        result = runner._await_voice_confirmation(
            "t1", [], poll_interval=0.0, timeout_seconds=0,
        )
    assert result is None


def test_await_voice_confirmation_exits_when_shutdown_requested():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)

    with patch("appcore.runtime_v2.task_state.get",
               return_value={"chosen_voice": None}), \
         patch("appcore.runtime_v2.task_state.update"), \
         patch("appcore.runtime_v2.cancellable_sleep",
               side_effect=OperationCancelled("shutdown requested")):
        with pytest.raises(OperationCancelled, match="shutdown requested"):
            runner._await_voice_confirmation(
                "t1", [], poll_interval=1.0, timeout_seconds=5,
            )


def test_step_voice_match_requests_top10_candidates():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    task = {
        "voice_match_mode": "auto",
        "target_language": "de",
        "voice_gender": "female",
    }

    with patch.object(runner, "_set_step"), \
         patch.object(runner, "_emit"), \
         patch("appcore.runtime_v2.task_state.get", return_value=task), \
         patch("appcore.runtime_v2.task_state.update"), \
         patch("pipeline.voice_match.match_for_video",
               return_value=[{"voice_id": "voice-1"}]) as m_match, \
         patch("pipeline.speech_rate_model.get_rate", return_value=None), \
         patch("appcore.runtime_v2.resolve_key", return_value=None):
        runner._step_voice_match("t1", "/tmp/demo.mp4", "/tmp/task")

    assert m_match.call_args.kwargs["top_k"] == 10
