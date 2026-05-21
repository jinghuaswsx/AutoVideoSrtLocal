from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def preserve_runner_registry():
    from appcore import runner_dispatch

    snapshot = (
        runner_dispatch._image_translate_start,
        runner_dispatch._image_translate_is_running,
        runner_dispatch._multi_translate_start,
        getattr(runner_dispatch, "_multi_translate_resume", None),
        getattr(runner_dispatch, "_omni_translate_start", None),
        getattr(runner_dispatch, "_omni_translate_resume", None),
        getattr(runner_dispatch, "_ja_translate_start", None),
        getattr(runner_dispatch, "_ja_translate_resume", None),
    )
    try:
        yield
    finally:
        (
            runner_dispatch._image_translate_start,
            runner_dispatch._image_translate_is_running,
            runner_dispatch._multi_translate_start,
            multi_translate_resume,
            omni_translate_start,
            omni_translate_resume,
            ja_translate_start,
            ja_translate_resume,
        ) = snapshot
        if hasattr(runner_dispatch, "_multi_translate_resume"):
            runner_dispatch._multi_translate_resume = multi_translate_resume
        if hasattr(runner_dispatch, "_omni_translate_start"):
            runner_dispatch._omni_translate_start = omni_translate_start
        if hasattr(runner_dispatch, "_omni_translate_resume"):
            runner_dispatch._omni_translate_resume = omni_translate_resume
        if hasattr(runner_dispatch, "_ja_translate_start"):
            runner_dispatch._ja_translate_start = ja_translate_start
        if hasattr(runner_dispatch, "_ja_translate_resume"):
            runner_dispatch._ja_translate_resume = ja_translate_resume
        if snapshot == (None, None, None, None, None, None, None, None):
            try:
                import web.routes.image_translate  # noqa: F401
                import web.services.multi_pipeline_runner  # noqa: F401
                import web.services.omni_pipeline_runner  # noqa: F401
                import web.services.ja_pipeline_runner  # noqa: F401
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


def test_runner_dispatch_invokes_registered_omni_translate_runner():
    from appcore import runner_dispatch

    calls = []
    runner_dispatch.clear_runner_registry()
    runner_dispatch.register_omni_translate_runner(
        start=lambda task_id, user_id=None: calls.append((task_id, user_id)) or True,
    )

    assert runner_dispatch.start_omni_translate_runner("omni-1", user_id=9) is True
    assert calls == [("omni-1", 9)]


def test_runner_dispatch_invokes_registered_resume_runners():
    from appcore import runner_dispatch

    calls = []
    runner_dispatch.clear_runner_registry()
    runner_dispatch.register_multi_translate_runner(
        start=lambda task_id, user_id=None: True,
        resume=lambda task_id, start_step, user_id=None: calls.append(
            ("multi", task_id, start_step, user_id)
        )
        or True,
    )
    runner_dispatch.register_omni_translate_runner(
        start=lambda task_id, user_id=None: True,
        resume=lambda task_id, start_step, user_id=None: calls.append(
            ("omni", task_id, start_step, user_id)
        )
        or True,
    )
    runner_dispatch.register_ja_translate_runner(
        start=lambda task_id, user_id=None: True,
        resume=lambda task_id, start_step, user_id=None: calls.append(
            ("ja", task_id, start_step, user_id)
        )
        or True,
    )

    assert runner_dispatch.resume_multi_translate_runner("multi-1", "alignment", user_id=3) is True
    assert runner_dispatch.resume_omni_translate_runner("omni-1", "alignment", user_id=4) is True
    assert runner_dispatch.resume_ja_translate_runner("ja-1", "alignment", user_id=5) is True
    assert calls == [
        ("multi", "multi-1", "alignment", 3),
        ("omni", "omni-1", "alignment", 4),
        ("ja", "ja-1", "alignment", 5),
    ]


def test_runner_dispatch_requires_registered_omni_translate_runner():
    from appcore import runner_dispatch

    runner_dispatch.clear_runner_registry()

    with pytest.raises(RuntimeError, match="omni_translate runner is not registered"):
        runner_dispatch.start_omni_translate_runner("omni-1", user_id=9)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("resume_multi_translate_runner", "multi_translate resume runner is not registered"),
        ("resume_omni_translate_runner", "omni_translate resume runner is not registered"),
        ("resume_ja_translate_runner", "ja_translate resume runner is not registered"),
    ],
)
def test_runner_dispatch_requires_registered_resume_runners(call, message):
    from appcore import runner_dispatch

    runner_dispatch.clear_runner_registry()

    with pytest.raises(RuntimeError, match=message):
        getattr(runner_dispatch, call)("task-1", "alignment", user_id=9)
