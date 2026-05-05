"""VACE Windows backend for hardcoded subtitle removal.

Public API: :class:`VaceWindowsSubtitleRemover` from :mod:`.remover`.

This package is intentionally side-effect free at import time:
- It does NOT import torch or check the GPU.
- It does NOT load VACE models.
- It does NOT verify the VACE repo or python venv exists.

All such checks happen lazily when :meth:`remove_subtitles` is invoked,
so unit tests can import this package without VACE being installed.
"""
from .remover import VaceWindowsSubtitleRemover

__all__ = ["VaceWindowsSubtitleRemover"]
