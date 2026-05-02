import io
import importlib.util
import json
import signal
from pathlib import Path

import pytest


def _make_file(filename: str, content: bytes = b"fake-video-data"):
    return (io.BytesIO(content), filename)


@pytest.fixture
def authed_client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)

    fake_user = {"id": 1, "username": "test-admin", "role": "admin", "is_active": 1}
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def test_background_helper_proxies_to_socketio(monkeypatch):
    calls = []

    class FakeSocketIO:
        def start_background_task(self, target, *args, **kwargs):
            calls.append((target, args, kwargs))
            return "task-handle"

    from web.background import start_background_task
    import web.background

    fake_socketio = FakeSocketIO()
    monkeypatch.setattr("web.extensions.socketio", fake_socketio)
    monkeypatch.setattr(web.background, "socketio", fake_socketio)

    def runner():
        return None

    result = start_background_task(runner, 1, mode="demo")

    assert result == "task-handle"
    assert calls == [(runner, (1,), {"mode": "demo"})]


def test_copywriting_generate_uses_background_helper(authed_client, monkeypatch):
    from appcore import task_state

    task_state._tasks["cw-task"] = {"id": "cw-task", "_user_id": 1, "type": "copywriting"}
    background_calls = []
    active_registrations = []

    monkeypatch.setattr(
        "web.routes.copywriting.try_register_active_task",
        lambda *args, **kwargs: active_registrations.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.copywriting.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    class FakeRunner:
        def __init__(self, bus, user_id):
            self.generate_copy = lambda task_id: None

    monkeypatch.setattr("web.routes.copywriting.CopywritingRunner", FakeRunner)

    response = authed_client.post("/api/copywriting/cw-task/generate", json={})

    assert response.status_code == 200
    assert len(background_calls) == 1
    assert background_calls[0][1][0] == "cw-task"
    assert active_registrations == [
        (
            ("copywriting", "cw-task"),
            {
                "user_id": 1,
                "runner": "web.routes.copywriting._run_copywriting_with_tracking",
                "entrypoint": "copywriting.generate",
                "stage": "queued_generate",
                "details": {"action": "generate"},
            },
        )
    ]


def test_copywriting_generate_rejects_duplicate_active_task(authed_client, monkeypatch):
    from appcore import task_state

    task_state._tasks["cw-duplicate"] = {
        "id": "cw-duplicate",
        "_user_id": 1,
        "type": "copywriting",
    }
    background_calls = []

    monkeypatch.setattr(
        "web.routes.copywriting.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.copywriting.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    class FakeRunner:
        def __init__(self, bus, user_id):
            self.generate_copy = lambda task_id: None

    monkeypatch.setattr("web.routes.copywriting.CopywritingRunner", FakeRunner)

    response = authed_client.post("/api/copywriting/cw-duplicate/generate", json={})

    assert response.status_code == 200
    assert response.get_json()["status"] == "already_running"
    assert background_calls == []


def test_copywriting_tts_uses_active_guard(authed_client, monkeypatch):
    from appcore import task_state

    task_state._tasks["cw-tts"] = {"id": "cw-tts", "_user_id": 1, "type": "copywriting"}
    background_calls = []
    active_registrations = []

    monkeypatch.setattr(
        "web.routes.copywriting.try_register_active_task",
        lambda *args, **kwargs: active_registrations.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.copywriting.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    class FakeRunner:
        def __init__(self, bus, user_id):
            self.start_tts_compose = lambda task_id: None

    monkeypatch.setattr("web.routes.copywriting.CopywritingRunner", FakeRunner)

    response = authed_client.post("/api/copywriting/cw-tts/tts", json={"voice_id": "voice-1"})

    assert response.status_code == 200
    assert len(background_calls) == 1
    assert background_calls[0][1][0] == "cw-tts"
    assert active_registrations == [
        (
            ("copywriting", "cw-tts"),
            {
                "user_id": 1,
                "runner": "web.routes.copywriting._run_copywriting_with_tracking",
                "entrypoint": "copywriting.tts",
                "stage": "queued_tts",
                "details": {"action": "tts", "voice_id": "voice-1"},
            },
        )
    ]


def test_copywriting_tts_rejects_duplicate_active_task(authed_client, monkeypatch):
    from appcore import task_state

    task_state._tasks["cw-tts-duplicate"] = {
        "id": "cw-tts-duplicate",
        "_user_id": 1,
        "type": "copywriting",
    }
    background_calls = []

    monkeypatch.setattr(
        "web.routes.copywriting.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.copywriting.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    class FakeRunner:
        def __init__(self, bus, user_id):
            self.start_tts_compose = lambda task_id: None

    monkeypatch.setattr("web.routes.copywriting.CopywritingRunner", FakeRunner)

    response = authed_client.post("/api/copywriting/cw-tts-duplicate/tts", json={})

    assert response.status_code == 200
    assert response.get_json()["status"] == "already_running"
    assert background_calls == []


def test_video_creation_upload_uses_background_helper(authed_client, monkeypatch, tmp_path):
    background_calls = []
    active_registrations = []

    monkeypatch.setattr(
        "web.routes.video_creation._resolve_seedance_config",
        lambda: {
            "api_key": "seedance-key",
            "base_url": "https://seedance.example.test",
            "model_id": "seedance-test",
        },
    )
    monkeypatch.setattr(
        "web.routes.video_creation.try_register_active_task",
        lambda *args, **kwargs: active_registrations.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr("web.routes.video_creation.get_retention_hours", lambda *_: 24)
    monkeypatch.setattr(
        "web.routes.video_creation.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )
    monkeypatch.setattr("web.routes.video_creation.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.video_creation.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.video_creation.UPLOAD_DIR", str(tmp_path / "uploads"))

    response = authed_client.post(
        "/api/video-creation/upload",
        data={"prompt": "make a nice ad video"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    task_id = response.get_json()["id"]
    assert len(background_calls) == 1
    assert background_calls[0][1][0] == task_id
    assert len(background_calls[0][1]) == 5
    assert active_registrations[0][0] == ("video_creation", task_id)
    assert active_registrations[0][1]["user_id"] == 1
    assert active_registrations[0][1]["runner"] == "web.routes.video_creation._run_generate_with_tracking"
    assert active_registrations[0][1]["entrypoint"] == "video_creation.upload"
    assert active_registrations[0][1]["stage"] == "queued_generate"
    assert active_registrations[0][1]["details"]["model_id"] == "seedance-test"


def test_video_creation_regenerate_uses_active_guard(authed_client, monkeypatch):
    background_calls = []
    active_registrations = []
    state = {
        "task_dir": "output/vc-regenerate",
        "prompt": "make a new ad",
        "steps": {"generate": "done"},
        "ratio": "16:9",
        "duration": 8,
        "generate_audio": False,
        "watermark": False,
        "result_video_url": "https://old.example/video.mp4",
        "result_video_path": "old.mp4",
        "seedance_task_id": "old-task",
    }

    monkeypatch.setattr("web.routes.video_creation.recover_project_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_creation._resolve_seedance_config",
        lambda: {
            "api_key": "seedance-key",
            "base_url": "https://seedance.example.test",
            "model_id": "seedance-test",
        },
    )
    monkeypatch.setattr(
        "web.routes.video_creation.db_query_one",
        lambda *args, **kwargs: {"state_json": json.dumps(state, ensure_ascii=False)},
    )
    monkeypatch.setattr("web.routes.video_creation.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_creation.try_register_active_task",
        lambda *args, **kwargs: active_registrations.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.video_creation.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    response = authed_client.post("/api/video-creation/vc-regenerate/regenerate")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert len(background_calls) == 1
    assert background_calls[0][1][0] == "vc-regenerate"
    assert active_registrations[0][0] == ("video_creation", "vc-regenerate")
    assert active_registrations[0][1]["user_id"] == 1
    assert active_registrations[0][1]["entrypoint"] == "video_creation.regenerate"
    assert active_registrations[0][1]["stage"] == "queued_generate"
    assert active_registrations[0][1]["details"]["model_id"] == "seedance-test"


def test_video_creation_regenerate_rejects_duplicate_active_task(authed_client, monkeypatch):
    background_calls = []
    state = {
        "task_dir": "output/vc-duplicate",
        "prompt": "make a new ad",
        "steps": {"generate": "done"},
    }

    monkeypatch.setattr("web.routes.video_creation.recover_project_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_creation._resolve_seedance_config",
        lambda: {
            "api_key": "seedance-key",
            "base_url": "https://seedance.example.test",
            "model_id": "seedance-test",
        },
    )
    monkeypatch.setattr(
        "web.routes.video_creation.db_query_one",
        lambda *args, **kwargs: {"state_json": json.dumps(state, ensure_ascii=False)},
    )
    monkeypatch.setattr("web.routes.video_creation.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_creation.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.video_creation.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    response = authed_client.post("/api/video-creation/vc-duplicate/regenerate")

    assert response.status_code == 200
    assert response.get_json()["status"] == "already_running"
    assert background_calls == []


def test_video_review_start_review_uses_background_helper(authed_client, monkeypatch, tmp_path):
    video_path = tmp_path / "review.mp4"
    video_path.write_bytes(b"video-bytes")
    background_calls = []
    active_registrations = []

    row = {
        "id": "vr-task",
        "user_id": 1,
        "type": "video_review",
        "deleted_at": None,
        "state_json": json.dumps(
            {
                "video_path": str(video_path),
                "model": "gemini-3.1-pro-preview",
                "steps": {"review": "pending"},
            },
            ensure_ascii=False,
        ),
    }

    monkeypatch.setattr("web.routes.video_review.db_query_one", lambda *args, **kwargs: row)
    monkeypatch.setattr("web.routes.video_review._update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.video_review.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_review.try_register_active_task",
        lambda *args, **kwargs: active_registrations.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.video_review.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    response = authed_client.post("/api/video-review/vr-task/review", json={})

    assert response.status_code == 200
    assert response.get_json()["status"] == "started"
    assert len(background_calls) == 1
    assert background_calls[0][1][0] == "vr-task"
    assert active_registrations == [
        (
            ("video_review", "vr-task"),
            {
                "user_id": 1,
                "runner": "web.routes.video_review._run_review_with_tracking",
                "entrypoint": "video_review.review",
                "stage": "queued_review",
                "details": {
                    "model": "gemini-3.1-pro-preview",
                    "prompt_lang": "en",
                },
            },
        )
    ]


def test_video_review_start_review_rejects_duplicate_active_task(authed_client, monkeypatch, tmp_path):
    video_path = tmp_path / "review.mp4"
    video_path.write_bytes(b"video-bytes")
    background_calls = []

    row = {
        "id": "vr-duplicate",
        "user_id": 1,
        "type": "video_review",
        "deleted_at": None,
        "state_json": json.dumps(
            {
                "video_path": str(video_path),
                "model": "gemini-3.1-pro-preview",
                "steps": {"review": "running"},
            },
            ensure_ascii=False,
        ),
    }

    monkeypatch.setattr("web.routes.video_review.db_query_one", lambda *args, **kwargs: row)
    monkeypatch.setattr("web.routes.video_review._update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.video_review.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.video_review.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.video_review.start_background_task",
        lambda fn, *args: background_calls.append((fn, args)),
    )

    response = authed_client.post("/api/video-review/vr-duplicate/review", json={})

    assert response.status_code == 200
    assert response.get_json()["status"] == "already_running"
    assert background_calls == []


def test_gunicorn_service_uses_threaded_config():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "autovideosrt.service").read_text(encoding="utf-8")
    config = (root / "deploy" / "gunicorn.conf.py").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    assert "eventlet" not in service
    assert "gunicorn.conf.py" in service
    assert 'worker_class = "gthread"' in config
    assert "workers = 1" in config
    assert "threads = 32" in config
    assert 'AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT", "240"' in config
    assert "TimeoutStopSec=300" in service
    assert "simple-websocket" in requirements
    assert "\neventlet" not in requirements


def test_gunicorn_signal_handler_stops_scheduler_before_delegating(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    config_path = root / "deploy" / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("autovideosrt_gunicorn_conf_signal_test", config_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    events = []
    monkeypatch.setattr(
        "appcore.shutdown_coordinator.request_shutdown",
        lambda reason: events.append(("shutdown", reason)),
    )
    monkeypatch.setattr(
        "appcore.scheduler.shutdown_scheduler",
        lambda wait=False: events.append(("scheduler", wait)),
    )

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    def original_term(signum, frame):
        events.append(("original", signum))

    try:
        signal.signal(signal.SIGTERM, original_term)
        module.post_worker_init(None)

        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    assert events == [
        ("shutdown", f"signal={signal.SIGTERM}"),
        ("scheduler", False),
        ("original", signal.SIGTERM),
    ]


def test_gunicorn_worker_exit_logs_active_task_details(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    config_path = root / "deploy" / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("autovideosrt_gunicorn_conf_test", config_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from appcore import active_tasks

    task = active_tasks.ActiveTask(
        project_type="video_creation",
        task_id="vc-shutdown",
        runner="web.routes.video_creation.generate",
        stage="compose",
    )
    monkeypatch.setattr(active_tasks, "list_active_tasks", lambda: [task])
    monkeypatch.setattr(
        active_tasks,
        "snapshot_active_tasks",
        lambda reason, tasks=None: {"count": len(tasks or []), "target": "database"},
    )
    monkeypatch.setattr(
        "appcore.shutdown_coordinator.wait_for_active_tasks",
        lambda timeout: 1,
    )
    monkeypatch.setattr(
        "appcore.scheduler.shutdown_scheduler",
        lambda wait=False: None,
    )

    messages = []

    class FakeLog:
        def info(self, message, *args):
            messages.append(message % args if args else message)

        def warning(self, message, *args):
            messages.append(message % args if args else message)

    class FakeWorker:
        log = FakeLog()

    module.worker_exit(None, FakeWorker())

    joined = "\n".join(messages)
    assert "active unfinished task" in joined
    assert "video_creation:vc-shutdown" in joined
    assert "stage=compose" in joined
    assert "runner=web.routes.video_creation.generate" in joined
