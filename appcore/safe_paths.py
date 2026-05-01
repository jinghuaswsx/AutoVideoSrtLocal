from __future__ import annotations

import os
import shutil
from pathlib import Path


class PathSafetyError(ValueError):
    """Raised when a path escapes the configured storage roots."""


def _resolve(path: str | os.PathLike) -> Path:
    return Path(path).expanduser().resolve()


def resolve_under_allowed_roots(
    path: str | os.PathLike,
    allowed_roots: list[str | os.PathLike] | tuple[str | os.PathLike, ...],
) -> Path:
    candidate = _resolve(path)
    roots = [_resolve(root) for root in allowed_roots if str(root or "").strip()]
    for root in roots:
        if candidate == root or root in candidate.parents:
            return candidate
    raise PathSafetyError(f"path is outside allowed roots: {candidate}")


def remove_file_under_roots(
    path: str | os.PathLike,
    allowed_roots: list[str | os.PathLike] | tuple[str | os.PathLike, ...],
) -> bool:
    target = resolve_under_allowed_roots(path, allowed_roots)
    if not target.is_file():
        return False
    target.unlink()
    return True


def remove_tree_under_roots(
    path: str | os.PathLike,
    allowed_roots: list[str | os.PathLike] | tuple[str | os.PathLike, ...],
    *,
    ignore_errors: bool = True,
) -> bool:
    target = resolve_under_allowed_roots(path, allowed_roots)
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=ignore_errors)
    return True
