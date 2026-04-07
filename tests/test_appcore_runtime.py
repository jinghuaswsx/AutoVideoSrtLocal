"""Tests for appcore/runtime.py PipelineRunner.

All pipeline steps are mocked — runtime logic only.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import appcore.task_state as task_state
from appcore.events import EVT_PIPELINE_ERROR, EVT_STEP_UPDATE, Event, EventBus
from appcore.runtime import PipelineRunner


def _make_task(task_id: str) -> None:
    task_state.create(task_id, "/video.mp4", "/task_dir", "video.mp4")


def _make_runner() -> tuple[PipelineRunner, list[Event]]:
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(lambda e: events.append(e))
    runner = PipelineRunner(bus=bus)
    return runner, events


def test_set_step_publishes_step_update_event():
    task_id = "test_set_step"
    _make_task(task_id)
    runner, events = _make_runner()
    runner._set_step(task_id, "asr", "running", "testing")
    assert any(
        e.type == EVT_STEP_UPDATE and e.payload["step"] == "asr" and e.payload["status"] == "running"
        for e in events
    )
    assert task_state.get(task_id)["steps"]["asr"] == "running"


def test_run_calls_all_steps_in_order():
    task_id = "test_run_order"
    _make_task(task_id)
    runner, events = _make_runner()

    call_order = []

    runner._step_extract = lambda *a: call_order.append("extract")
    runner._step_asr = lambda *a: call_order.append("asr")
    runner._step_alignment = lambda *a: call_order.append("alignment")
    runner._step_translate = lambda *a: call_order.append("translate")
    runner._step_tts = lambda *a: call_order.append("tts")
    runner._step_subtitle = lambda *a: call_order.append("subtitle")
    runner._step_compose = lambda *a: call_order.append("compose")
    runner._step_export = lambda *a: call_order.append("export")

    runner._run(task_id)

    assert call_order == ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]


def test_run_publishes_pipeline_error_on_exception():
    task_id = "test_run_error"
    _make_task(task_id)
    runner, events = _make_runner()

    runner._step_extract = MagicMock(side_effect=RuntimeError("boom"))
    runner._step_asr = MagicMock()

    runner._run(task_id)

    error_events = [e for e in events if e.type == EVT_PIPELINE_ERROR]
    assert len(error_events) == 1
    assert "boom" in error_events[0].payload["error"]
    assert task_state.get(task_id)["status"] == "error"


def test_no_flask_or_socketio_imports():
    """Ensure runtime.py never imports Flask or socketio."""
    import importlib
    import sys

    # Remove cached module to re-check imports cleanly
    mod_name = "appcore.runtime"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Temporarily block flask/socketio
    import builtins
    real_import = builtins.__import__

    forbidden = []

    def guarded_import(name, *args, **kwargs):
        if name in ("flask", "flask_socketio", "web.extensions"):
            forbidden.append(name)
        return real_import(name, *args, **kwargs)

    builtins.__import__ = guarded_import
    try:
        import appcore.runtime  # noqa: F401
    finally:
        builtins.__import__ = real_import

    assert not forbidden, f"appcore.runtime imported forbidden modules: {forbidden}"


def test_runtime_modules_do_not_import_web_modules():
    module_paths = [
        Path("appcore/runtime.py"),
        Path("appcore/runtime_fr.py"),
        Path("appcore/runtime_de.py"),
    ]

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        forbidden = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                forbidden.extend(alias.name for alias in node.names if alias.name.startswith("web"))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("web"):
                    forbidden.append(node.module or "")

        assert not forbidden, f"{module_path} imported forbidden web modules: {forbidden}"
