from unittest.mock import patch, MagicMock

import pytest


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


def test_start_ignores_duplicate(monkeypatch):
    from web.services import image_translate_runner as runner

    monkeypatch.setattr(runner.time, "monotonic", lambda: 1500.0)
    with runner._running_tasks_lock:
        runner._running_tasks["dup-1"] = {"instance": "inst-existing", "last": 1000.0}

    assert runner.start("dup-1", user_id=1) is False


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


@pytest.fixture(autouse=True)
def _reset_runner_state():
    """每个测试后清 _running_tasks，避免互相污染。"""
    from web.services import image_translate_runner as r

    yield

    with r._running_tasks_lock:
        r._running_tasks.clear()


def test_is_running_false_when_no_slot():
    from web.services import image_translate_runner as r

    assert r.is_running("no-such-task") is False


def test_is_running_true_when_slot_fresh(monkeypatch):
    from web.services import image_translate_runner as r

    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": now[0]}
    now[0] = 1100.0

    assert r.is_running("t1") is True


def test_is_running_false_when_slot_expired(monkeypatch):
    from web.services import image_translate_runner as r

    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": now[0]}
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC
    assert r.is_running("t1") is False
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC + 1

    assert r.is_running("t1") is False


def test_touch_heartbeat_matching_instance(monkeypatch):
    from web.services import image_translate_runner as r

    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": 1000.0}
    now[0] = 1500.0

    assert r._touch_heartbeat("t1", "inst-a") is True
    assert r._running_tasks["t1"]["last"] == 1500.0


def test_touch_heartbeat_wrong_instance_returns_false():
    from web.services import image_translate_runner as r

    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-new", "last": 9999.0}

    assert r._touch_heartbeat("t1", "inst-old") is False
    assert r._running_tasks["t1"]["last"] == 9999.0


def test_touch_heartbeat_missing_slot_returns_false():
    from web.services import image_translate_runner as r

    assert r._touch_heartbeat("t-missing", "any") is False


def test_start_returns_false_when_active_slot_exists(monkeypatch):
    """runtime 线程实际不跑（monkeypatch 掉 thread.start）。"""
    from web.services import image_translate_runner as r

    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-existing", "last": 1000.0}
    now[0] = 1500.0
    monkeypatch.setattr(
        r.threading,
        "Thread",
        lambda target, daemon: type("T", (), {"start": lambda self: None})(),
    )

    assert r.start("t1", user_id=1) is False


def test_start_preempts_expired_slot_with_new_instance(monkeypatch):
    from web.services import image_translate_runner as r

    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-zombie", "last": 1000.0}
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC + 1
    monkeypatch.setattr(
        r.threading,
        "Thread",
        lambda target, daemon: type("T", (), {"start": lambda self: None})(),
    )
    monkeypatch.setattr(
        r,
        "ImageTranslateRuntime",
        lambda **kw: type("Rt", (), {"start": lambda self, tid: None})(),
    )

    assert r.start("t1", user_id=1) is True
    slot = r._running_tasks["t1"]
    assert slot["instance"] != "inst-zombie"
    assert slot["last"] == now[0]
