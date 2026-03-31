"""Qt signal bridge for dispatching EventBus events to the main thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from appcore.events import Event


class EventBridge(QObject):
    """Receives events from pipeline worker thread and re-emits on the main thread."""

    event_received = Signal(object)  # carries an Event instance

    def emit_event(self, event: Event) -> None:
        """Called from any thread; safely delivers to Qt main thread."""
        self.event_received.emit(event)
