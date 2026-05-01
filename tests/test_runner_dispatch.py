from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def preserve_runner_registry():
    from appcore import runner_dispatch

    snapshot = (
        runner_dispatch._image_translate_start,
        runner_dispatch._image_translate_is_running,
        runner_dispatch._multi_translate_start,
    )
    try:
        yield
    finally:
        (
            runner_dispatch._image_translate_start,
            runner_dispatch._image_translate_is_running,
            runner_dispatch._multi_translate_start,
        ) = snapshot
        if snapshot == (None, None, None):
            try:
                import web.routes.image_translate  # noqa: F401
                import web.services.multi_pipeline_runner  # noqa: F401
            except Exception:
                pass


def test_runner_dispatch_invokes_registered_image_translate_runner():
    from appcore import runner_dispatch

    calls = []
    runner_dispatch.clear_runner_registry()
    runner_dispatch.register_image_translate_runner(
        start=lambda task_id, user_id=None: calls.append((task_id, user_id)) or True,
        is_running=lambda task_id: task_id == "running-task",
    )

    assert runner_dispatch.start_image_translate_runner("task-1", user_id=7) is True
    assert calls == [("task-1", 7)]
    assert runner_dispatch.is_image_translate_running("running-task") is True


def test_runner_dispatch_requires_registered_runner():
    from appcore import runner_dispatch

    runner_dispatch.clear_runner_registry()

    with pytest.raises(RuntimeError, match="image_translate runner is not registered"):
        runner_dispatch.start_image_translate_runner("task-1", user_id=7)
