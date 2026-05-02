from __future__ import annotations

import importlib

import pytest


class _FakeRunner:
    project_type = "translation"

    def start(self, task_id):
        return None

    def resume(self, task_id, start_step):
        return None


def test_pipeline_runner_start_prevents_duplicate_active_thread(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
    from web.services import pipeline_runner

    task_id = "runner-dup"
    task_recovery.unregister_active_task("translation", task_id)
    threads = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            threads.append(self)

    monkeypatch.setattr(pipeline_runner, "_make_runner", lambda task_id, user_id: _FakeRunner())
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)

    try:
        assert pipeline_runner.start(task_id, user_id=1) is True
        assert pipeline_runner.start(task_id, user_id=1) is False
        assert len(threads) == 1
        assert task_recovery.is_task_active("translation", task_id) is True
    finally:
        task_recovery.unregister_active_task("translation", task_id)


def test_image_translate_runner_registers_active_task_before_thread_runs(monkeypatch):
    from appcore import task_recovery
    from web.services import image_translate_runner

    task_id = "image-active"
    task_recovery.unregister_active_task("image_translate", task_id)
    with image_translate_runner._running_tasks_lock:
        image_translate_runner._running_tasks.discard(task_id)
    threads = []

    class FakeThread:
        def __init__(self, *, target, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            threads.append(self)

    monkeypatch.setattr(image_translate_runner.threading, "Thread", FakeThread)

    try:
        assert image_translate_runner.start(task_id, user_id=1) is True
        assert task_recovery.is_task_active("image_translate", task_id) is True
        assert len(threads) == 1
    finally:
        task_recovery.unregister_active_task("image_translate", task_id)
        with image_translate_runner._running_tasks_lock:
            image_translate_runner._running_tasks.discard(task_id)


def test_translate_lab_runner_start_uses_active_registry(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
    from web.services import translate_lab_runner

    task_id = "lab-active"
    task_recovery.unregister_active_task("translate_lab", task_id)
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            threads.append(self)

    class FakeLabRunner:
        project_type = "translate_lab"

        def __init__(self, *, bus, user_id=None):
            self.bus = bus
            self.user_id = user_id

        def start(self, task_id):
            return None

        def resume(self, task_id, start_step):
            return None

    monkeypatch.setattr(translate_lab_runner, "PipelineRunnerV2", FakeLabRunner)
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)

    try:
        assert translate_lab_runner.start(task_id, user_id=1) is True
        assert translate_lab_runner.start(task_id, user_id=1) is False
        assert task_recovery.is_task_active("translate_lab", task_id) is True
        assert len(threads) == 1
    finally:
        task_recovery.unregister_active_task("translate_lab", task_id)


def test_subtitle_removal_runner_registers_active_task_before_thread_runs(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
    from web.services import subtitle_removal_runner

    task_id = "sr-active"
    task_recovery.unregister_active_task("subtitle_removal", task_id)
    with subtitle_removal_runner._running_tasks_lock:
        subtitle_removal_runner._running_tasks.discard(task_id)
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            threads.append(self)

    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)
    monkeypatch.setattr(subtitle_removal_runner.threading, "Thread", FakeThread, raising=False)
    monkeypatch.setattr(
        subtitle_removal_runner.SubtitleRemovalRuntime,
        "start",
        lambda self, task_id: None,
    )

    try:
        assert subtitle_removal_runner.start(task_id, user_id=1) is True
        assert subtitle_removal_runner.start(task_id, user_id=1) is False
        assert task_recovery.is_task_active("subtitle_removal", task_id) is True
        assert len(threads) == 1
    finally:
        task_recovery.unregister_active_task("subtitle_removal", task_id)
        with subtitle_removal_runner._running_tasks_lock:
            subtitle_removal_runner._running_tasks.discard(task_id)


def test_link_check_runner_registers_active_task_before_thread_runs(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
    from web.services import link_check_runner

    task_id = "lc-active"
    task_recovery.unregister_active_task("link_check", task_id)
    with link_check_runner._lock:
        link_check_runner._running.discard(task_id)
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            threads.append(self)

    class FakeRuntime:
        def start(self, task_id):
            return None

    monkeypatch.setattr(link_check_runner, "LinkCheckRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)

    try:
        assert link_check_runner.start(task_id) is True
        assert link_check_runner.start(task_id) is False
        assert task_recovery.is_task_active("link_check", task_id) is True
        assert len(threads) == 1
    finally:
        task_recovery.unregister_active_task("link_check", task_id)
        with link_check_runner._lock:
            link_check_runner._running.discard(task_id)


def test_link_check_runner_rejects_duplicate_global_active_task(monkeypatch):
    from appcore import task_recovery
    from web.services import link_check_runner

    task_id = "lc-global-active"
    with link_check_runner._lock:
        link_check_runner._running.discard(task_id)
    task_recovery.register_active_task("link_check", task_id)
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(link_check_runner.threading, "Thread", FakeThread)

    try:
        assert link_check_runner.start(task_id) is False
        assert started == []
        with link_check_runner._lock:
            assert task_id not in link_check_runner._running
    finally:
        task_recovery.unregister_active_task("link_check", task_id)
        with link_check_runner._lock:
            link_check_runner._running.discard(task_id)


@pytest.mark.parametrize(
    ("module_name", "runner_attr", "project_type"),
    [
        ("web.services.pipeline_runner", "PipelineRunner", "translation"),
        ("web.services.de_pipeline_runner", "DeTranslateRunner", "de_translate"),
        ("web.services.fr_pipeline_runner", "FrTranslateRunner", "fr_translate"),
    ],
)
def test_run_analysis_registers_active_task(monkeypatch, module_name, runner_attr, project_type):
    from appcore import active_tasks, runner_lifecycle

    module = importlib.import_module(module_name)
    task_id = f"{project_type}-analysis-active"
    active_tasks.clear_active_tasks_for_tests()
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = daemon

        def start(self):
            threads.append(self)

    class FakeAnalysisRunner:
        def __init__(self, *args, **kwargs):
            self.project_type = project_type

    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)
    if hasattr(module, "threading"):
        monkeypatch.setattr(module.threading, "Thread", FakeThread)
    monkeypatch.setattr(module, runner_attr, FakeAnalysisRunner)
    if hasattr(module, "_make_runner"):
        monkeypatch.setattr(module, "_make_runner", lambda task_id, user_id=None: FakeAnalysisRunner())

    try:
        assert module.run_analysis(task_id, user_id=7) is True
        assert active_tasks.is_active(project_type, task_id) is True
        task = active_tasks.list_active_tasks()[0]
        assert task.project_type == project_type
        assert task.task_id == task_id
        assert task.runner == "appcore.runtime.run_analysis_only"
        assert task.stage == "analysis"
        assert task.details["action"] == "manual_analysis"
        assert len(threads) == 1
    finally:
        active_tasks.clear_active_tasks_for_tests()


@pytest.mark.parametrize(
    ("module_name", "runner_attr", "project_type"),
    [
        ("web.services.pipeline_runner", "PipelineRunner", "translation"),
        ("web.services.de_pipeline_runner", "DeTranslateRunner", "de_translate"),
        ("web.services.fr_pipeline_runner", "FrTranslateRunner", "fr_translate"),
    ],
)
def test_run_analysis_rejects_duplicate_active_task(monkeypatch, module_name, runner_attr, project_type):
    from appcore import active_tasks, task_recovery

    module = importlib.import_module(module_name)
    task_id = f"{project_type}-analysis-dup"
    active_tasks.clear_active_tasks_for_tests()
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = daemon

        def start(self):
            threads.append(self)

    class FakeAnalysisRunner:
        def __init__(self, *args, **kwargs):
            self.project_type = project_type

    if hasattr(module, "threading"):
        monkeypatch.setattr(module.threading, "Thread", FakeThread)
    monkeypatch.setattr(module, runner_attr, FakeAnalysisRunner)
    if hasattr(module, "_make_runner"):
        monkeypatch.setattr(module, "_make_runner", lambda task_id, user_id=None: FakeAnalysisRunner())
    task_recovery.register_active_task(project_type, task_id)

    try:
        assert module.run_analysis(task_id, user_id=7) is False
        assert threads == []
    finally:
        active_tasks.clear_active_tasks_for_tests()


def test_start_tracked_thread_records_active_task_metadata(monkeypatch):
    from appcore import active_tasks, runner_lifecycle

    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: False)
    active_tasks.clear_active_tasks_for_tests()
    threads = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            threads.append(self)

    def target():
        return None

    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)

    try:
        assert runner_lifecycle.start_tracked_thread(
            project_type="multi_translate",
            task_id="mt-meta",
            target=target,
            user_id=42,
            runner="web.services.multi_pipeline_runner.start",
            entrypoint="multi_translate.start",
            details={"source": "test"},
        ) is True

        listed = active_tasks.list_active_tasks()
        assert len(listed) == 1
        task = listed[0]
        assert task.project_type == "multi_translate"
        assert task.task_id == "mt-meta"
        assert task.user_id == 42
        assert task.runner == "web.services.multi_pipeline_runner.start"
        assert task.entrypoint == "multi_translate.start"
        assert task.details["source"] == "test"
        assert task.interrupt_policy == "block_restart"
        assert len(threads) == 1
    finally:
        active_tasks.clear_active_tasks_for_tests()
