"""make_tts_progress_emitter helper 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from appcore.runtime._helpers import make_tts_progress_emitter


def _snap(state, total, active, queued, done, info=None):
    return {
        "state": state, "total": total, "active": active,
        "queued": queued, "done": done, "info": info or {},
    }


def test_emitter_says_queued_when_no_active_no_done():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="西班牙语")
    emitter(_snap("submitted", total=70, active=0, queued=70, done=0))
    runner._emit_substep_msg.assert_called_once()
    args = runner._emit_substep_msg.call_args[0]
    assert args[0] == "task-1"
    assert args[1] == "tts"
    assert "西班牙语配音" in args[2]
    assert "排队中" in args[2]
    assert "70 段待派发" in args[2]


def test_emitter_says_progress_after_first_started():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="西班牙语")
    emitter(_snap("started", total=70, active=1, queued=69, done=0))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "西班牙语配音" in msg
    assert "0/70" in msg
    assert "活跃 1 路" in msg
    assert "排队中" not in msg


def test_emitter_says_progress_during_completion():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="德语")
    emitter(_snap("completed", total=70, active=11, queued=47, done=12))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "12/70" in msg
    assert "活跃 11 路" in msg


def test_emitter_uses_round_label():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="法语", round_label="第 2 轮",
    )
    emitter(_snap("started", total=10, active=1, queued=9, done=0))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "法语配音" in msg
    assert "第 2 轮" in msg


def test_emitter_calls_extra_state_update():
    runner = MagicMock()
    extra_calls = []
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="西班牙语",
        extra_state_update=lambda snap: extra_calls.append(snap["done"]),
    )
    emitter(_snap("completed", total=10, active=5, queued=4, done=1))
    emitter(_snap("completed", total=10, active=4, queued=3, done=2))
    assert extra_calls == [1, 2]


def test_emitter_swallows_extra_update_exception():
    runner = MagicMock()
    def boom(snap): raise RuntimeError("kaboom")
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="西班牙语",
        extra_state_update=boom,
    )
    # 不抛错就 OK
    emitter(_snap("completed", total=10, active=5, queued=4, done=1))
