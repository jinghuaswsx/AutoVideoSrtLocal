import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def restore_config_module(monkeypatch):
    yield
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.delenv("SCHEDULED_TASKS_ENABLED", raising=False)
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])


def _reload_config(monkeypatch, value=None):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    if value is None:
        monkeypatch.delenv("SCHEDULED_TASKS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCHEDULED_TASKS_ENABLED", value)
    sys.modules.pop("config", None)
    import config

    return importlib.reload(config)


def test_scheduled_tasks_switch_defaults_to_enabled(monkeypatch):
    config = _reload_config(monkeypatch)

    assert config.SCHEDULED_TASKS_ENABLED is True


def test_scheduled_tasks_switch_can_be_disabled_from_env(monkeypatch):
    config = _reload_config(monkeypatch, "0")

    assert config.SCHEDULED_TASKS_ENABLED is False


def test_start_scheduler_if_enabled_skips_scheduler_when_disabled(monkeypatch):
    from appcore import scheduler

    events = []
    monkeypatch.setattr(scheduler.config, "SCHEDULED_TASKS_ENABLED", False, raising=False)

    result = scheduler.start_scheduler_if_enabled(
        get_scheduler_fn=lambda: events.append("get_scheduler"),
        register_atexit_shutdown_fn=lambda: events.append("atexit"),
    )

    assert result is None
    assert events == []


def test_start_scheduler_if_enabled_starts_scheduler_when_enabled(monkeypatch):
    from appcore import scheduler

    events = []

    class FakeScheduler:
        def start(self):
            events.append("start")

    monkeypatch.setattr(scheduler.config, "SCHEDULED_TASKS_ENABLED", True, raising=False)

    result = scheduler.start_scheduler_if_enabled(
        get_scheduler_fn=lambda: FakeScheduler(),
        register_atexit_shutdown_fn=lambda: events.append("atexit"),
    )

    assert isinstance(result, FakeScheduler)
    assert events == ["start", "atexit"]


def test_run_if_enabled_respects_global_scheduled_task_switch(monkeypatch):
    from appcore import scheduled_tasks

    called = []
    monkeypatch.setattr(scheduled_tasks, "_global_scheduled_tasks_enabled", lambda: False)

    result = scheduled_tasks.run_if_enabled("cleanup", lambda: called.append("ran"))

    assert called == []
    assert result == {
        "skipped": True,
        "reason": "scheduled tasks globally disabled",
        "task_code": "cleanup",
    }


def test_voice_match_cleanup_thread_respects_global_scheduled_task_switch(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.setenv("SCHEDULED_TASKS_ENABLED", "0")
    sys.modules.pop("config", None)
    sys.modules.pop("appcore.voice_match_tasks", None)

    from appcore import voice_match_tasks

    assert voice_match_tasks._cleanup_thread is None


def test_medias_detail_fetch_cleanup_thread_respects_global_scheduled_task_switch(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.setenv("SCHEDULED_TASKS_ENABLED", "0")
    sys.modules.pop("config", None)
    sys.modules.pop("appcore.medias_detail_fetch_tasks", None)

    from appcore import medias_detail_fetch_tasks

    assert medias_detail_fetch_tasks._cleanup_thread is None
