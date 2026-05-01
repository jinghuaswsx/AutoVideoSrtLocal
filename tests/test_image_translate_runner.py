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
    from web.services import image_translate_runner as runner

    registered = []
    thread = MagicMock()

    monkeypatch.setattr(
        runner,
        "try_register_active_task",
        lambda project_type, task_id, **metadata: registered.append((project_type, task_id, metadata)) or True,
    )
    monkeypatch.setattr(runner, "unregister_active_task", lambda *args: None)
    monkeypatch.setattr(runner, "ImageTranslateRuntime", MagicMock())
    monkeypatch.setattr(runner.threading, "Thread", lambda target=None, daemon=None: thread)
    with runner._running_tasks_lock:
        runner._running_tasks.discard("tid-meta")

    try:
        assert runner.start("tid-meta", user_id=12) is True
    finally:
        with runner._running_tasks_lock:
            runner._running_tasks.discard("tid-meta")

    assert registered
    project_type, task_id, metadata = registered[0]
    assert project_type == "image_translate"
    assert task_id == "tid-meta"
    assert metadata["user_id"] == 12
    assert metadata["runner"] == "web.services.image_translate_runner.start"
    assert metadata["entrypoint"] == "image_translate.start"
    assert metadata["stage"] == "process"
    assert metadata["interrupt_policy"] == "cautious"


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
