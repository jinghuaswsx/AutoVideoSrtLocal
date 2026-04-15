from __future__ import annotations

from appcore.events import EventBus
from appcore import task_state


def test_runtime_success_downloads_and_uploads_result(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task = task_state.create_subtitle_removal(
        "sr-runtime",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime",
        status="submitted",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime/source.mp4",
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", lambda **kwargs: "provider-task-1")
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.query_progress",
        lambda task_id: {
            "taskId": task_id,
            "status": "success",
            "emsg": "成功",
            "resultUrl": "https://provider.example/result.mp4",
            "position": "{\"l\":0,\"t\":0,\"w\":720,\"h\":1280}",
        },
    )
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime._download_result_file",
        lambda url, path: str(tmp_path / "result.cleaned.mp4"),
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.upload_file", lambda local_path, object_key: None)
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.tos_clients.build_artifact_object_key",
        lambda user_id, task_id, variant, filename: f"artifacts/{user_id}/{task_id}/{variant}/{filename}",
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime")

    saved = task_state.get("sr-runtime")
    assert saved["status"] == "done"
    assert saved["provider_task_id"] == "provider-task-1"
    assert saved["result_tos_key"].endswith("result.cleaned.mp4")


def test_download_result_file_streams_content_to_disk(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import _download_result_file

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            captured["chunk_size"] = chunk_size
            yield b"hello "
            yield b"world"

        @property
        def content(self):
            raise AssertionError("response.content should not be used")

    def fake_get(url, timeout=None, stream=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("appcore.subtitle_removal_runtime.requests.get", fake_get)

    result_path = _download_result_file("https://provider.example/result.mp4", str(tmp_path / "result.cleaned.mp4"))

    assert result_path.endswith("result.cleaned.mp4")
    assert captured["url"] == "https://provider.example/result.mp4"
    assert captured["timeout"] == 120
    assert captured["stream"] is True
    assert (tmp_path / "result.cleaned.mp4").read_bytes() == b"hello world"


def test_runtime_stops_without_rewriting_deleted_task(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-deleted",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-deleted",
        status="deleted",
        deleted_at="2026-04-16T12:00:00",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime-deleted/source.mp4",
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", lambda **kwargs: "provider-task-1")
    monkeypatch.setattr("appcore.subtitle_removal_runtime.query_progress", lambda task_id: (_ for _ in ()).throw(AssertionError("query_progress should not run for deleted task")))
    download_called = []
    monkeypatch.setattr("appcore.subtitle_removal_runtime._download_result_file", lambda url, path: download_called.append(url) or str(tmp_path / "result.cleaned.mp4"))
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.upload_file", lambda local_path, object_key: None)
    monkeypatch.setattr("appcore.subtitle_removal_runtime.tos_clients.build_artifact_object_key", lambda user_id, task_id, variant, filename: f"artifacts/{user_id}/{task_id}/{variant}/{filename}")

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime-deleted")

    saved = task_state.get("sr-runtime-deleted")
    assert saved["status"] == "deleted"
    assert saved.get("result_tos_key", "") == ""
    assert download_called == []


def test_runner_start_is_per_task_idempotent(monkeypatch):
    import web.services.subtitle_removal_runner as runner

    started = []

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
            self.daemon = daemon

        def start(self):
            started.append("thread")

    monkeypatch.setattr(runner, "_running_tasks", set())
    monkeypatch.setattr(runner.threading, "Thread", FakeThread)
    monkeypatch.setattr("web.services.subtitle_removal_runner.SubtitleRemovalRuntime.start", lambda self, task_id: None)

    assert runner.start("sr-gate", user_id=1) is True
    assert runner.start("sr-gate", user_id=1) is False
    assert started == ["thread"]


def test_resume_inflight_tasks_requeues_polling_rows(monkeypatch):
    import web.routes.subtitle_removal as subtitle_removal

    started = []

    monkeypatch.setattr(
        subtitle_removal,
        "db_query",
        lambda sql, args=(): [{
            "id": "sr-recover",
            "user_id": 1,
            "state_json": '{"id":"sr-recover","type":"subtitle_removal","status":"running","steps":{"prepare":"done","submit":"running","poll":"pending","download_result":"pending","upload_result":"pending"}}',
            "status": "running",
        }] if "FROM projects" in sql else [],
    )
    monkeypatch.setattr(
        subtitle_removal,
        "subtitle_removal_runner",
        type(
            "Runner",
            (),
            {
                "is_running": lambda self, task_id: False,
                "start": lambda self, task_id, user_id=None: started.append((task_id, user_id)) or True,
            },
        )(),
    )

    result = subtitle_removal.resume_inflight_tasks()

    assert result == ["sr-recover"]
    assert started == [("sr-recover", 1)]
    assert subtitle_removal.task_state.get("sr-recover")["status"] == "running"


def test_resume_inflight_tasks_skips_non_inflight_rows(monkeypatch):
    import web.routes.subtitle_removal as subtitle_removal

    started = []

    monkeypatch.setattr(
        subtitle_removal,
        "db_query",
        lambda sql, args=(): [{
            "id": "sr-finished",
            "user_id": 1,
            "state_json": '{"id":"sr-finished","type":"subtitle_removal","status":"running","steps":{"prepare":"done","submit":"done","poll":"done","download_result":"done","upload_result":"done"}}',
            "status": "running",
        }] if "FROM projects" in sql else [],
    )
    monkeypatch.setattr(
        subtitle_removal,
        "subtitle_removal_runner",
        type(
            "Runner",
            (),
            {
                "is_running": lambda self, task_id: False,
                "start": lambda self, task_id, user_id=None: started.append((task_id, user_id)) or True,
            },
        )(),
    )

    result = subtitle_removal.resume_inflight_tasks()

    assert result == []
    assert started == []
    assert subtitle_removal.task_state.get("sr-finished") is None


def test_resume_inflight_tasks_recovers_after_submit_before_poll(monkeypatch):
    import web.routes.subtitle_removal as subtitle_removal

    started = []

    monkeypatch.setattr(
        subtitle_removal,
        "db_query",
        lambda sql, args=(): [{
            "id": "sr-submit-window",
            "user_id": 1,
            "state_json": '{"id":"sr-submit-window","type":"subtitle_removal","status":"running","provider_task_id":"provider-task-1","steps":{"prepare":"done","submit":"done","poll":"pending","download_result":"pending","upload_result":"pending"}}',
            "status": "running",
        }] if "FROM projects" in sql else [],
    )
    monkeypatch.setattr(
        subtitle_removal,
        "subtitle_removal_runner",
        type(
            "Runner",
            (),
            {
                "is_running": lambda self, task_id: False,
                "start": lambda self, task_id, user_id=None: started.append((task_id, user_id)) or True,
            },
        )(),
    )

    result = subtitle_removal.resume_inflight_tasks()

    assert result == ["sr-submit-window"]
    assert started == [("sr-submit-window", 1)]
    assert subtitle_removal.task_state.get("sr-submit-window")["provider_task_id"] == "provider-task-1"


def test_resume_inflight_tasks_recovers_after_download_before_upload(monkeypatch):
    import web.routes.subtitle_removal as subtitle_removal

    started = []

    monkeypatch.setattr(
        subtitle_removal,
        "db_query",
        lambda sql, args=(): [{
            "id": "sr-download-window",
            "user_id": 1,
            "state_json": '{"id":"sr-download-window","type":"subtitle_removal","status":"running","result_video_path":"/tmp/result.cleaned.mp4","steps":{"prepare":"done","submit":"done","poll":"done","download_result":"done","upload_result":"pending"}}',
            "status": "running",
        }] if "FROM projects" in sql else [],
    )
    monkeypatch.setattr(
        subtitle_removal,
        "subtitle_removal_runner",
        type(
            "Runner",
            (),
            {
                "is_running": lambda self, task_id: False,
                "start": lambda self, task_id, user_id=None: started.append((task_id, user_id)) or True,
            },
        )(),
    )

    result = subtitle_removal.resume_inflight_tasks()

    assert result == ["sr-download-window"]
    assert started == [("sr-download-window", 1)]
    assert subtitle_removal.task_state.get("sr-download-window")["result_video_path"] == "/tmp/result.cleaned.mp4"
