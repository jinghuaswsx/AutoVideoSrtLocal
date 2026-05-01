from __future__ import annotations

from collections.abc import Callable

ImageStartFunc = Callable[[str, int | None], bool]
ImageRunningFunc = Callable[[str], bool]
MultiStartFunc = Callable[[str, int | None], object]

_image_translate_start: ImageStartFunc | None = None
_image_translate_is_running: ImageRunningFunc | None = None
_multi_translate_start: MultiStartFunc | None = None


def clear_runner_registry() -> None:
    global _image_translate_start, _image_translate_is_running, _multi_translate_start
    _image_translate_start = None
    _image_translate_is_running = None
    _multi_translate_start = None


def register_image_translate_runner(
    *,
    start: ImageStartFunc,
    is_running: ImageRunningFunc | None = None,
) -> None:
    global _image_translate_start, _image_translate_is_running
    _image_translate_start = start
    _image_translate_is_running = is_running


def register_multi_translate_runner(*, start: MultiStartFunc) -> None:
    global _multi_translate_start
    _multi_translate_start = start


def start_image_translate_runner(task_id: str, user_id: int | None = None) -> bool:
    if _image_translate_start is None:
        raise RuntimeError("image_translate runner is not registered")
    return bool(_image_translate_start(task_id, user_id))


def is_image_translate_running(task_id: str) -> bool:
    if _image_translate_is_running is None:
        return False
    return bool(_image_translate_is_running(task_id))


def start_multi_translate_runner(task_id: str, user_id: int | None = None) -> object:
    if _multi_translate_start is None:
        raise RuntimeError("multi_translate runner is not registered")
    return _multi_translate_start(task_id, user_id)
