"""视频翻译（测试）V2 流水线 runner 单元测试。"""
from __future__ import annotations

from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_v2 import PipelineRunnerV2


def test_runner_defines_seven_steps():
    bus = EventBus()
    runner = PipelineRunnerV2(bus=bus, user_id=1)
    names = [name for name, _fn in runner._build_steps(
        task_id="t1", video_path="/v.mp4", task_dir="/d",
    )]
    assert names == [
        "extract", "shot_decompose", "voice_match",
        "translate", "tts_verify", "subtitle", "compose",
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
         patch("appcore.runtime_v2.time.sleep"):
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
         patch("appcore.runtime_v2.time.sleep"):
        result = runner._await_voice_confirmation(
            "t1", [], poll_interval=0.0, timeout_seconds=0,
        )
    assert result is None
