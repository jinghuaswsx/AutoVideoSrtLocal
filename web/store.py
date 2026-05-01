"""Backward-compatible module alias for ``appcore.task_state``.

Historically Web code imported ``web.store`` while runtime code used the same
task-state functions directly. Keep the old module path, but make it the exact
same module object so tests and legacy monkeypatches affect both entry points.
"""
from __future__ import annotations

import sys

from appcore import task_state as _task_state

sys.modules[__name__] = _task_state
