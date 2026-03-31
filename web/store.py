"""
任务状态存储 — appcore.task_state 的 web 层 facade。

所有状态操作委托给 appcore.task_state；本模块仅作为向后兼容的命名空间。
"""
from appcore.task_state import (
    confirm_alignment,
    confirm_segments,
    create,
    get,
    get_all,
    set_artifact,
    set_preview_file,
    set_step,
    set_variant_artifact,
    set_variant_preview_file,
    update,
    update_variant,
)

__all__ = [
    "confirm_alignment",
    "confirm_segments",
    "create",
    "get",
    "get_all",
    "set_artifact",
    "set_preview_file",
    "set_step",
    "set_variant_artifact",
    "set_variant_preview_file",
    "update",
    "update_variant",
]
