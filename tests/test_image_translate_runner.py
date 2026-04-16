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


def test_resume_picks_up_queued_and_running_rows():
    from web.services import image_translate_runner as runner
    rows = [
        {"id": "a", "user_id": 1, "status": "queued",
         "state_json": '{"type":"image_translate","status":"queued","items":[{"status":"pending","idx":0}]}'},
        {"id": "b", "user_id": 2, "status": "running",
         "state_json": '{"type":"image_translate","status":"running","items":[{"status":"done","idx":0},{"status":"pending","idx":1}]}'},
    ]
    with patch.object(runner, "db_query", return_value=rows), \
         patch.object(runner, "start") as st:
        restored = runner.resume_inflight_tasks()
    assert set(restored) == {"a", "b"}
    assert st.call_count == 2


def test_resume_skips_task_with_all_items_finished():
    from web.services import image_translate_runner as runner
    rows = [
        {"id": "c", "user_id": 1, "status": "running",
         "state_json": '{"type":"image_translate","items":[{"status":"done","idx":0},{"status":"failed","idx":1}]}'},
    ]
    with patch.object(runner, "db_query", return_value=rows), \
         patch.object(runner, "start") as st:
        restored = runner.resume_inflight_tasks()
    assert restored == []
    assert st.call_count == 0
