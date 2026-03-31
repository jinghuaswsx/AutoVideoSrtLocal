# Desktop GUI Implementation Plan

Based on spec: `docs/superpowers/specs/2026-04-01-desktop-gui-design.md`

## Context

AutoVideoSrt is a video subtitle/dubbing pipeline running as Flask+SocketIO. Goal: extract pipeline logic into `appcore/` shared runtime, build PySide6 native GUI on top, keep web version working.

### Key files

- `web/services/pipeline_runner.py` — pipeline executor; coupled to socketio + web/store
- `web/store.py` — in-memory task state
- `web/app.py` — Flask factory
- `pipeline/` — pure pipeline modules (no web coupling; do not change)
- `config.py` — env-based config
- `web/preview_artifacts.py` — artifact payload builders

### Current coupling problems

1. `pipeline_runner.py` calls `socketio.emit(...)` directly inside step functions
2. `pipeline_runner.py` imports `from web import store` and `from web.extensions import socketio`
3. `web/store.py` mixes business state with web-preview URL concerns

---

## Phase 1: Shared Runtime Extraction

### Task 1.1 — Create `appcore/events.py`

Define a framework-agnostic event bus.

**Create:** `appcore/__init__.py` (empty), `appcore/events.py`

```python
# appcore/events.py
from dataclasses import dataclass, field
from typing import Callable

@dataclass
class Event:
    type: str
    task_id: str
    payload: dict = field(default_factory=dict)

EventHandler = Callable[[Event], None]

class EventBus:
    def __init__(self):
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def publish(self, event: Event) -> None:
        for h in self._handlers:
            h(event)

# Event type constants
EVT_TASK_STARTED = "task_started"
EVT_STEP_UPDATE = "step_update"
EVT_ARTIFACT_READY = "artifact_ready"
EVT_ALIGNMENT_RESULT = "alignment_result"
EVT_TRANSLATE_RESULT = "translate_result"
EVT_TTS_SCRIPT_READY = "tts_script_ready"
EVT_ENGLISH_ASR_RESULT = "english_asr_result"
EVT_SUBTITLE_READY = "subtitle_ready"
EVT_CAPCUT_READY = "capcut_ready"
EVT_PIPELINE_DONE = "pipeline_done"
EVT_PIPELINE_ERROR = "pipeline_error"
```

**Tests:** `tests/test_appcore_events.py`
- Subscribing a handler and publishing calls handler with correct Event
- Multiple handlers all receive the event
- Publishing with no subscribers does not raise

**Commit:** `feat: add appcore/events.py with framework-agnostic event bus`

---

### Task 1.2 — Create `appcore/task_state.py`

Port business-state logic from `web/store.py` into pure Python with no web dependencies.

**Create:** `appcore/task_state.py`

**Copy from `web/store.py` (no changes to logic):**
- `_tasks: dict = {}`
- `_empty_variant_state()`
- `create()`
- `get()`
- `update()`
- `set_step()`
- `set_artifact()`
- `set_preview_file()`
- `set_variant_artifact()`
- `set_variant_preview_file()`
- `confirm_alignment()`
- `confirm_segments()`
- `get_all()`
- `_localized_translation_from_segments()`

**Do NOT copy:** Any function referencing socketio, Flask, HTTP URLs, or HTML.

**Tests:** `tests/test_appcore_task_state.py`
- `create()` initializes all expected keys with correct defaults
- `set_step()` updates step status for a known task
- `set_artifact()` stores payload under correct step key
- `confirm_alignment()` updates alignment dict and sets `_alignment_confirmed = True`
- `confirm_segments()` updates segments and sets `_segments_confirmed = True`
- `get()` returns None for missing task_id
- `set_variant_artifact()` stores payload under variant > step

**Commit:** `feat: add appcore/task_state.py — pure business state store`

---

### Task 1.3 — Create `appcore/runtime.py`

Move all `_step_*` functions from `web/services/pipeline_runner.py` into a `PipelineRunner` class that emits via `EventBus` instead of socketio.

**Create:** `appcore/runtime.py`

Only imports: `appcore.events`, `appcore.task_state`, `pipeline.*`, stdlib. Zero `web.*` imports.

```python
import threading
from appcore.events import EventBus, Event, EVT_STEP_UPDATE, EVT_PIPELINE_DONE, EVT_PIPELINE_ERROR  # etc.
import appcore.task_state as _default_state

class PipelineRunner:
    def __init__(self, bus: EventBus, state=None):
        self.bus = bus
        self.state = state or _default_state

    def _emit(self, task_id: str, event_type: str, payload: dict):
        self.bus.publish(Event(type=event_type, task_id=task_id, payload=payload))

    def _set_step(self, task_id: str, step: str, status: str, message: str = ""):
        self.state.set_step(task_id, step, status)
        self._emit(task_id, EVT_STEP_UPDATE, {"step": step, "status": status, "message": message})

    def run(self, task_id: str):
        t = threading.Thread(target=self._run_pipeline, args=(task_id,), daemon=True)
        t.start()

    def _run_pipeline(self, task_id: str):
        try:
            task = self.state.get(task_id)
            task_dir = task["task_dir"]
            self._step_extract(task_id, task_dir)
            self._step_asr(task_id, task_dir)
            self._step_alignment(task_id, task_dir)
            self._step_translate(task_id, task_dir)
            self._step_tts(task_id, task_dir)
            self._step_subtitle(task_id, task_dir)
            self._step_compose(task_id, task_dir)
            self._step_capcut_export(task_id, task_dir)
            self._emit(task_id, EVT_PIPELINE_DONE, {"task_id": task_id})
        except Exception as e:
            self._emit(task_id, EVT_PIPELINE_ERROR, {"error": str(e)})
```

Migrate each `_step_*` from `pipeline_runner.py` as a method, replacing:
- `emit(task_id, ...)` → `self._emit(task_id, ...)`
- `store.*` → `self.state.*`
- `set_step(...)` → `self._set_step(...)`

**Tests:** `tests/test_appcore_runtime.py`

Use `unittest.mock.patch` for all `pipeline.*` imports. No real pipeline calls, no file I/O.

- `PipelineRunner._set_step()` calls `state.set_step()` AND publishes `EVT_STEP_UPDATE` event
- `run()` starts a thread (patch threading.Thread, verify it's started)
- `_run_pipeline()` calls steps in order (mock each `_step_*` method)
- When a step raises, `EVT_PIPELINE_ERROR` is published with error string
- A subscribed handler receives all events in correct order for a mocked happy-path run

**Commit:** `feat: add appcore/runtime.py — framework-agnostic pipeline runner`

---

### Task 1.4 — Refactor web layer to delegate to appcore

**Modify:** `web/services/pipeline_runner.py`

Replace the current implementation with a thin SocketIO adapter:

```python
from appcore.events import EventBus, Event
from appcore.runtime import PipelineRunner
from web.extensions import socketio
import appcore.task_state as task_state

def _make_socketio_handler(task_id: str):
    def handler(event: Event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler

def start_pipeline(task_id: str):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, state=task_state)
    runner.run(task_id)
```

Preserve all existing socketio event names (event.type maps directly; payload structure preserved from runtime).

**Modify:** `web/store.py`

Replace internal `_tasks` dict and all state functions with delegation to `appcore.task_state`:

```python
# web/store.py becomes a facade
from appcore.task_state import (
    create, get, update, set_step, set_artifact,
    set_preview_file, set_variant_artifact, set_variant_preview_file,
    confirm_alignment, confirm_segments, get_all,
)
```

Any web-only functions (e.g., URL-building helpers used only in routes) stay in `web/store.py`.

**Verify:** Run all existing tests — they must pass without modification:
- `tests/test_pipeline_runner.py`
- `tests/test_web_routes.py`
- `tests/test_preview_artifacts.py`

If any break, fix the adapter, not the tests.

**Commit:** `refactor: wire web pipeline runner and store to appcore runtime`

---

## Phase 2: Desktop Shell

### Task 2.1 — Add PySide6 dependency

Add `PySide6>=6.6.0` to `requirements.txt`.

Verify: `python -c "from PySide6.QtWidgets import QApplication; print('ok')"` succeeds.

**Commit:** `chore: add PySide6 dependency`

---

### Task 2.2 — Create `desktop/main.py` entry point

**Create:** `desktop/__init__.py` (empty), `desktop/main.py`

```python
# desktop/main.py
import sys
from dotenv import load_dotenv
load_dotenv()

from PySide6.QtWidgets import QApplication, QMessageBox
from config import validate_runtime_config
from desktop.window import MainWindow

def main():
    app = QApplication(sys.argv)
    try:
        validate_runtime_config()
    except RuntimeError as e:
        QMessageBox.critical(None, "Configuration Error", str(e))
        sys.exit(1)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

**Tests:** `tests/test_desktop_main.py`

Patch `QApplication`, `MainWindow`, `validate_runtime_config`:
- When `validate_runtime_config` raises `RuntimeError`, `QMessageBox.critical` is called and `sys.exit(1)` is called
- When config is valid, `MainWindow()` is instantiated, `show()` is called, `app.exec()` is called

**Commit:** `feat: add desktop/main.py with config validation`

---

### Task 2.3 — Create `desktop/window.py` — MainWindow

**Create:** `desktop/window.py`

Layout: `QMainWindow` containing a horizontal `QSplitter`:
- Left panel (~280px): `TaskConfigPanel`
- Center panel (~320px): `StepListWidget`
- Right panel (expanding): `ArtifactPreviewWidget`

```python
from PySide6.QtWidgets import QMainWindow, QSplitter
from PySide6.QtCore import Qt
from appcore.events import EventBus
from appcore.runtime import PipelineRunner
import appcore.task_state as task_state
from desktop.widgets.task_config import TaskConfigPanel
from desktop.widgets.step_list import StepListWidget
from desktop.widgets.artifact_preview import ArtifactPreviewWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoVideoSrt")
        self.resize(1280, 800)
        self.current_task_id: str | None = None
        self.bus = EventBus()
        self.runner = PipelineRunner(bus=self.bus)

        splitter = QSplitter(Qt.Horizontal)
        self.config_panel = TaskConfigPanel()
        self.step_list = StepListWidget()
        self.preview = ArtifactPreviewWidget()

        splitter.addWidget(self.config_panel)
        splitter.addWidget(self.step_list)
        splitter.addWidget(self.preview)
        splitter.setSizes([280, 320, 680])
        self.setCentralWidget(splitter)

        self.config_panel.start_requested.connect(self._on_start)
        self.bus.subscribe(self._on_event)

    def _on_start(self, video_path: str, voice_name: str, subtitle_position: str):
        import uuid, os, shutil
        from config import OUTPUT_DIR
        task_id = uuid.uuid4().hex[:12]
        task_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)
        dest = os.path.join(task_dir, os.path.basename(video_path))
        shutil.copy2(video_path, dest)
        task_state.create(task_id, dest, task_dir, os.path.basename(video_path))
        task_state.update(task_id, voice_name=voice_name, subtitle_position=subtitle_position)
        self.current_task_id = task_id
        self.step_list.reset()
        self.runner.run(task_id)

    def _on_event(self, event):
        # Qt signal dispatch must happen on the main thread
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(
            self, "_handle_event_main_thread",
            Qt.QueuedConnection,
            # pass event via a lambda approach or use a Qt signal bridge
        )
```

**Thread safety note:** `EventBus.publish()` is called from the pipeline worker thread. All Qt widget updates must happen on the main thread. Use a `QObject`-based signal bridge:

**Create:** `desktop/bridge.py`

```python
from PySide6.QtCore import QObject, Signal
from appcore.events import Event

class EventBridge(QObject):
    event_received = Signal(object)  # emits Event instances

    def emit_event(self, event: Event):
        self.event_received.emit(event)
```

In `MainWindow.__init__`, create `self.bridge = EventBridge()`, connect `self.bridge.event_received.connect(self._handle_event)`, then pass `self.bridge.emit_event` as the event bus subscriber.

---

## Phase 3: Remaining Widgets (CapCut deploy, preview panels)

### Task 3.1 — CapcutExportWidget

**Create:** `desktop/widgets/capcut_export.py`

Two buttons: "部署普通版到剪映" and "部署黄金3秒+CTA版到剪映". On click, call `pipeline.capcut.deploy_to_jianying(draft_path, variant)` (or the equivalent function from the pipeline layer). Buttons disabled until `pipeline_done` event received. Show status label per variant.

**Test:** `tests/test_desktop_capcut_widget.py` — mock the deploy call, verify button enable/disable logic.

### Task 3.2 — ArtifactPreviewWidget

**Create:** `desktop/widgets/artifact_preview.py`

Dispatch to appropriate sub-widget based on artifact type:
- `text` → `QPlainTextEdit` (read-only)
- `audio` → `AudioPreviewWidget` (`QMediaPlayer + QAudioOutput + play/pause button`)
- `video` → `VideoPreviewWidget` (`QMediaPlayer + QVideoWidget`)
- `srt` → `QPlainTextEdit` (read-only)

**Test:** `tests/test_desktop_artifact_preview.py` — instantiate each variant, verify correct sub-widget is shown.

### Task 3.3 — VariantCompareWidget

**Create:** `desktop/widgets/variant_compare.py`

Side-by-side `QSplitter` with two `ArtifactPreviewWidget` instances labelled `normal` and `hook_cta`. Only visible when the current step has dual-variant content.

**Test:** `tests/test_desktop_variant_compare.py` — set both variants, verify both panels populated.

---

## Phase 4: Entry Point, Packaging, Verification

### Task 4.1 — Desktop entry point

**Create:** `desktop/main.py`

```python
import sys
from PySide6.QtWidgets import QApplication
from desktop.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

### Task 4.2 — PyInstaller spec

**Create:** `desktop.spec`

Key inclusions:
- `datas`: `voices/voices.json`, `capcut_example/`, `.env` (if present)
- `hiddenimports`: all `pipeline.*`, `appcore.*`, `config`
- PySide6 multimedia plugins: add `--collect-all PySide6` or explicit plugin paths
- `console=False`, `onefile=True`

Verify packaging with:
```
pyinstaller desktop.spec
dist/desktop.exe  # smoke test: open app, pick a video, start pipeline
```

### Task 4.3 — Smoke test checklist (manual)

In the built `.exe`:
1. App opens without console errors
2. File picker works, video loads
3. Voice + subtitle position selectable
4. Start button triggers pipeline, steps update
5. After pipeline: audio/video/text artifacts preview
6. normal / hook_cta comparison visible
7. CapCut deploy buttons copy draft to JianyingPro directory
8. Web version (`python main.py`) still works unchanged

---

## Commit Strategy

- After Task 1.1–1.2: `feat: extract appcore runtime (task_state, events)`
- After Task 1.3: `refactor: web adapts to appcore runtime`
- After Task 1.4: `feat: appcore pipeline runner with EventBus`
- After Task 2.1–2.2: `feat: desktop main window scaffold + step list widget`
- After Task 2.3: `feat: desktop task config panel`
- After Task 3.1: `feat: desktop capcut export widget`
- After Task 3.2–3.3: `feat: desktop artifact preview + variant compare`
- After Task 4.1: `feat: desktop entry point`
- After Task 4.2: `feat: pyinstaller packaging spec`

---

## Testing Strategy

All new `appcore` and `desktop` code must have unit tests before implementation (TDD). Use `pytest` with `PySide6` test support (`pytest-qt` if available, else `QApplication` fixture in `conftest.py`). Mock all pipeline calls in desktop widget tests — widgets must never call real pipeline functions. The `appcore` runtime tests must not import Flask or socketio.

---

## Execution Options

**Subagent-Driven (recommended):** Dispatch a fresh subagent per task using `superpowers:subagent-driven-development`. Review output between tasks.

**Inline Execution:** Execute tasks in this session using `superpowers:executing-plans`.

Which approach would you like?
