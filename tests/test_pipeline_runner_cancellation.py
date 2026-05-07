"""PipelineRunner per-task cancellation 行为测试。

bulk_translate 父任务 cancel 时给子任务 task_state 设 `_cancel_requested=True`，
子 runner 主 loop 在下一个 step 边界检测到标志，抛 OperationCancelled，状态
收为 'cancelled'（与 systemd SIGTERM 的 'interrupted' 区分）。
"""
from __future__ import annotations

import pytest

from appcore import task_state
from appcore.cancellation import OperationCancelled
from appcore.events import EventBus
from appcore.runtime._pipeline_runner import PipelineRunner


class _StubRunner(PipelineRunner):
    """最小化 PipelineRunner 子类，把 step 序列暴露成可注入。"""

    project_type = "test_translation"

    def __init__(self, bus: EventBus, steps_provider):
        super().__init__(bus)
        self._steps_provider = steps_provider

    def _get_pipeline_steps(self, task_id, video_path, task_dir):
        return self._steps_provider(task_id)


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """构建一个内存态 task_state + 跳过 source video 校验的 runner 环境。

    传 user_id=None 让 task_state.create / update 都不写 DB（pure in-memory）。
    详见 appcore/task_state.py:_sync_task_to_db 和 _db_upsert 的 user_id is None 短路。
    """
    monkeypatch.setattr(
        "appcore.source_video.ensure_local_source_video",
        lambda task_id: None,
    )
    bus = EventBus()
    task_id = "task-cancel-1"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), original_filename="x.mp4", user_id=None)
    return bus, task_id


def test_run_loop_raises_on_per_task_cancel_flag(runner_env, monkeypatch):
    """user 设 _cancel_requested 后，runner 在下一个 step 边界停下，状态 cancelled。"""
    bus, task_id = runner_env
    executed: list[str] = []

    def step_extract():
        executed.append("extract")
        # extract 跑完后用户 cancel；下一次 step 边界应当抛 OperationCancelled
        task_state.update(task_id, _cancel_requested=True)

    def step_asr():
        executed.append("asr")  # 不应该执行

    # 使用 task_state.create 里预置的标准 step 名称（extract / asr），
    # 这样 _mark_pipeline_cancelled 遍历 steps 字典时能找到并标记 'cancelled'。
    runner = _StubRunner(bus, lambda tid: [("extract", step_extract), ("asr", step_asr)])
    with pytest.raises(OperationCancelled):
        runner._run(task_id, start_step="extract")

    assert executed == ["extract"]
    state = task_state.get(task_id)
    assert state["status"] == "cancelled"
    assert state["error"] == "task cancelled by user"
    # 关键：未跑完的 step 标 cancelled，而不是 interrupted
    assert state["steps"]["asr"] == "cancelled"


def test_run_loop_uses_interrupted_on_global_shutdown(runner_env, monkeypatch):
    """SIGTERM 触发的 OperationCancelled 走 'interrupted' 收尾，与用户 cancel 区分。"""
    from appcore import shutdown_coordinator
    bus, task_id = runner_env

    def step_extract():
        # 模拟 systemd 在 step extract 中途请求 shutdown
        shutdown_coordinator.request_shutdown("test sigterm")

    def step_asr():
        pytest.fail("step_asr should not run after shutdown")

    runner = _StubRunner(bus, lambda tid: [("extract", step_extract), ("asr", step_asr)])
    try:
        with pytest.raises(OperationCancelled):
            runner._run(task_id, start_step="extract")
        state = task_state.get(task_id)
        # SIGTERM 走 _mark_pipeline_interrupted（保留原行为，UI 显示"等服务恢复后重试"）
        assert state["status"] == "interrupted"
    finally:
        # 清理全局 shutdown 标志，避免污染其他测试
        shutdown_coordinator.reset()
