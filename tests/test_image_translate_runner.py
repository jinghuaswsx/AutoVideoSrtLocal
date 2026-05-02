from unittest.mock import patch, MagicMock


def test_is_running_initially_false():
    from web.services import image_translate_runner as runner
    assert runner.is_running("nope") is False


def test_start_spawns_thread_and_tracks_running():
    from web.services import image_translate_runner as runner
    with patch.object(runner, "ImageTranslateRuntime") as Rt, \
         patch("threading.Thread") as Thr:
        instance = MagicMock()
        Rt.return_value = instance
        thread = MagicMock()
        Thr.return_value = thread
        ok = runner.start("tid-1", user_id=1)
        assert ok is True
        thread.start.assert_called_once()


def test_start_registers_active_task_metadata(monkeypatch):
    from appcore import runner_lifecycle
    from web.services import image_translate_runner as runner

    registered = []

    def fake_start_tracked_thread(**kwargs):
        registered.append(kwargs)
        return True

    monkeypatch.setattr(runner_lifecycle, "start_tracked_thread", fake_start_tracked_thread)
    monkeypatch.setattr(runner, "ImageTranslateRuntime", MagicMock())
    with runner._running_tasks_lock:
        runner._running_tasks.discard("tid-meta")

    try:
        assert runner.start("tid-meta", user_id=12) is True
    finally:
        with runner._running_tasks_lock:
            runner._running_tasks.discard("tid-meta")

    assert len(registered) == 1
    metadata = registered[0]
    assert metadata["project_type"] == "image_translate"
    assert metadata["task_id"] == "tid-meta"
    assert metadata["user_id"] == 12
    assert metadata["runner"] == "web.services.image_translate_runner.start"
    assert metadata["entrypoint"] == "image_translate.start"
    assert metadata["stage"] == "process"
    assert metadata["interrupt_policy"] == "cautious"


def test_start_delegates_thread_start_to_runner_lifecycle(monkeypatch):
    from appcore import runner_lifecycle
    from web.services import image_translate_runner as runner

    calls = []

    def fake_start_tracked_thread(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(runner_lifecycle, "start_tracked_thread", fake_start_tracked_thread)
    monkeypatch.setattr(runner, "ImageTranslateRuntime", MagicMock())
    with runner._running_tasks_lock:
        runner._running_tasks.discard("tid-lifecycle")

    try:
        assert runner.start("tid-lifecycle", user_id=9) is True
    finally:
        with runner._running_tasks_lock:
            runner._running_tasks.discard("tid-lifecycle")

    assert len(calls) == 1
    call = calls[0]
    assert call["project_type"] == "image_translate"
    assert call["task_id"] == "tid-lifecycle"
    assert call["daemon"] is True
    assert call["user_id"] == 9
    assert call["runner"] == "web.services.image_translate_runner.start"
    assert call["entrypoint"] == "image_translate.start"
    assert call["stage"] == "process"
    assert call["interrupt_policy"] == "cautious"


def test_start_ignores_duplicate():
    from web.services import image_translate_runner as runner
    runner._running_tasks.add("dup-1")
    try:
        assert runner.start("dup-1", user_id=1) is False
    finally:
        runner._running_tasks.discard("dup-1")


def test_resume_inflight_tasks_is_removed():
    """启动期自动拉起已被拆除；中断恢复改由 task_recovery 把任务标为 interrupted，
    用户手动点「重新生成」再入队。这里守住退化路径，避免被无意中加回来。"""
    from web.services import image_translate_runner as runner
    assert not hasattr(runner, "resume_inflight_tasks")
