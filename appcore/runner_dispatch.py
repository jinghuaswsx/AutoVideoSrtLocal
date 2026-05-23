from __future__ import annotations

from collections.abc import Callable

ImageStartFunc = Callable[[str, int | None], bool]
ImageRunningFunc = Callable[[str], bool]
MultiStartFunc = Callable[[str, int | None], object]
MultiResumeFunc = Callable[[str, str, int | None], object]
OmniStartFunc = Callable[[str, int | None], object]
OmniResumeFunc = Callable[[str, str, int | None], object]
JaStartFunc = Callable[[str, int | None], object]
JaResumeFunc = Callable[[str, str, int | None], object]

_image_translate_start: ImageStartFunc | None = None
_image_translate_is_running: ImageRunningFunc | None = None
_multi_translate_start: MultiStartFunc | None = None
_multi_translate_resume: MultiResumeFunc | None = None
_omni_translate_start: OmniStartFunc | None = None
_omni_translate_resume: OmniResumeFunc | None = None
_omni_translate_v2_start: OmniStartFunc | None = None
_omni_translate_v2_resume: OmniResumeFunc | None = None
_ja_translate_start: JaStartFunc | None = None
_ja_translate_resume: JaResumeFunc | None = None


def clear_runner_registry() -> None:
    global _image_translate_start, _image_translate_is_running
    global _multi_translate_start, _multi_translate_resume
    global _omni_translate_start, _omni_translate_resume
    global _omni_translate_v2_start, _omni_translate_v2_resume
    global _ja_translate_start, _ja_translate_resume
    _image_translate_start = None
    _image_translate_is_running = None
    _multi_translate_start = None
    _multi_translate_resume = None
    _omni_translate_start = None
    _omni_translate_resume = None
    _omni_translate_v2_start = None
    _omni_translate_v2_resume = None
    _ja_translate_start = None
    _ja_translate_resume = None


def register_image_translate_runner(
    *,
    start: ImageStartFunc,
    is_running: ImageRunningFunc | None = None,
) -> None:
    global _image_translate_start, _image_translate_is_running
    _image_translate_start = start
    _image_translate_is_running = is_running


def register_multi_translate_runner(
    *,
    start: MultiStartFunc,
    resume: MultiResumeFunc | None = None,
) -> None:
    global _multi_translate_start, _multi_translate_resume
    _multi_translate_start = start
    _multi_translate_resume = resume


def register_omni_translate_runner(
    *,
    start: OmniStartFunc,
    resume: OmniResumeFunc | None = None,
) -> None:
    global _omni_translate_start, _omni_translate_resume
    _omni_translate_start = start
    _omni_translate_resume = resume


def register_omni_v2_translate_runner(
    *,
    start: OmniStartFunc,
    resume: OmniResumeFunc | None = None,
) -> None:
    global _omni_translate_v2_start, _omni_translate_v2_resume
    _omni_translate_v2_start = start
    _omni_translate_v2_resume = resume


def register_ja_translate_runner(
    *,
    start: JaStartFunc | None = None,
    resume: JaResumeFunc | None = None,
) -> None:
    global _ja_translate_start, _ja_translate_resume
    _ja_translate_start = start
    _ja_translate_resume = resume


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


def resume_multi_translate_runner(
    task_id: str,
    start_step: str,
    user_id: int | None = None,
) -> object:
    if _multi_translate_resume is None:
        raise RuntimeError("multi_translate resume runner is not registered")
    return _multi_translate_resume(task_id, start_step, user_id)


def start_omni_translate_runner(task_id: str, user_id: int | None = None) -> object:
    if _omni_translate_start is None:
        raise RuntimeError("omni_translate runner is not registered")
    return _omni_translate_start(task_id, user_id)


def resume_omni_translate_runner(
    task_id: str,
    start_step: str,
    user_id: int | None = None,
) -> object:
    if _omni_translate_resume is None:
        raise RuntimeError("omni_translate resume runner is not registered")
    return _omni_translate_resume(task_id, start_step, user_id)


def start_omni_translate_v2_runner(task_id: str, user_id: int | None = None) -> object:
    if _omni_translate_v2_start is None:
        raise RuntimeError("omni_translate_v2 runner is not registered")
    return _omni_translate_v2_start(task_id, user_id)


def resume_omni_translate_v2_runner(
    task_id: str,
    start_step: str,
    user_id: int | None = None,
) -> object:
    if _omni_translate_v2_resume is None:
        raise RuntimeError("omni_translate_v2 resume runner is not registered")
    return _omni_translate_v2_resume(task_id, start_step, user_id)


def start_ja_translate_runner(task_id: str, user_id: int | None = None) -> object:
    if _ja_translate_start is None:
        raise RuntimeError("ja_translate runner is not registered")
    return _ja_translate_start(task_id, user_id)


def resume_ja_translate_runner(
    task_id: str,
    start_step: str,
    user_id: int | None = None,
) -> object:
    if _ja_translate_resume is None:
        raise RuntimeError("ja_translate resume runner is not registered")
    return _ja_translate_resume(task_id, start_step, user_id)
