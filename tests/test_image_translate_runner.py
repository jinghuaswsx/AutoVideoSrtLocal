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
