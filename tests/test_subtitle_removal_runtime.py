from __future__ import annotations

from appcore.events import EventBus
from appcore import task_state


def test_runtime_success_downloads_result_and_finishes_locally(monkeypatch, tmp_path):
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
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
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
    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime")

    saved = task_state.get("sr-runtime")
    assert saved["status"] == "done"
    assert saved["provider_task_id"] == "provider-task-1"
    assert saved["result_video_path"].endswith("result.cleaned.mp4")
    assert saved["result_tos_key"] == ""


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
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", lambda **kwargs: "provider-task-1")
    monkeypatch.setattr("appcore.subtitle_removal_runtime.query_progress", lambda task_id: (_ for _ in ()).throw(AssertionError("query_progress should not run for deleted task")))
    download_called = []
    monkeypatch.setattr("appcore.subtitle_removal_runtime._download_result_file", lambda url, path: download_called.append(url) or str(tmp_path / "result.cleaned.mp4"))
    monkeypatch.setattr("appcore.subtitle_removal_source_storage.tos_clients.upload_file", lambda local_path, object_key: None)

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


def _legacy_test_runtime_resumes_existing_result_upload_without_re_submitting_provider(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    result_path = tmp_path / "result.cleaned.mp4"
    result_path.write_bytes(b"result-video")

    task_state.create_subtitle_removal(
        "sr-runtime-resume-upload",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-resume-upload",
        status="running",
        provider_task_id="provider-task-1",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        result_video_path=str(result_path),
        steps={
            "prepare": "done",
            "submit": "done",
            "poll": "done",
            "download_result": "done",
            "upload_result": "pending",
        },
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.submit_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("submit_task should not run during upload resume")),
    )
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.query_progress",
        lambda task_id: (_ for _ in ()).throw(AssertionError("query_progress should not run during upload resume")),
    )
    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime-resume-upload")

    saved = task_state.get("sr-runtime-resume-upload")
    assert saved["status"] == "done"
    assert saved["result_video_path"] == str(result_path)
    assert saved["result_tos_key"] == ""
    assert saved["step_messages"]["upload_result"] == "结果已回传到TOS"


def test_runtime_resumes_existing_result_upload_without_re_submitting_provider(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    result_path = tmp_path / "result.cleaned.mp4"
    result_path.write_bytes(b"result-video")

    task_state.create_subtitle_removal(
        "sr-runtime-resume-upload",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-resume-upload",
        status="running",
        provider_task_id="provider-task-1",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        result_video_path=str(result_path),
        steps={
            "prepare": "done",
            "submit": "done",
            "poll": "done",
            "download_result": "done",
            "upload_result": "pending",
        },
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.submit_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("submit_task should not run during upload resume")),
    )
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.query_progress",
        lambda task_id: (_ for _ in ()).throw(AssertionError("query_progress should not run during upload resume")),
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner.start("sr-runtime-resume-upload")

    saved = task_state.get("sr-runtime-resume-upload")
    assert saved["status"] == "done"
    assert saved["result_video_path"] == str(result_path)
    assert saved["result_tos_key"] == ""
    assert saved["step_messages"]["upload_result"] == "结果已保存到本地，无需回传TOS"


def test_runtime_submit_passes_erase_text_type_text(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-text",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-text",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime-text/source.mp4",
        erase_text_type="text",
    )

    captured = {}

    def fake_submit_task(**kwargs):
        captured.update(kwargs)
        return "provider-task-text"

    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", fake_submit_task)
    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-runtime-text")

    assert captured.get("erase_text_type") == "text"


def test_runtime_submit_defaults_to_subtitle_when_field_missing(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-default",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-default",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime-default/source.mp4",
    )

    captured = {}

    def fake_submit_task(**kwargs):
        captured.update(kwargs)
        return "provider-task-default"

    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", fake_submit_task)
    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-runtime-default")

    assert captured.get("erase_text_type") == "subtitle"


def test_runtime_submit_uses_backup_tos_signed_source_url(monkeypatch, tmp_path):
    from appcore import tos_backup_storage
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-backup-source",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-backup-source",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="FILES/test/subtitle_removal/uploads/1/sr-runtime-backup-source/source.mp4",
        source_object_info={
            "public_source_storage_backend": "tos_backup",
            "public_source_key": "FILES/test/subtitle_removal/uploads/1/sr-runtime-backup-source/source.mp4",
        },
    )

    captured = {}

    def fake_submit_task(**kwargs):
        captured.update(kwargs)
        return "provider-task-backup"

    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", fake_submit_task)
    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy TOS should not be used")),
    )
    monkeypatch.setattr(
        tos_backup_storage,
        "generate_signed_download_url",
        lambda object_key, expires=86400: f"https://backup.example/{object_key}",
        raising=False,
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-runtime-backup-source")

    assert captured["source_url"] == (
        "https://backup.example/FILES/test/subtitle_removal/uploads/1/sr-runtime-backup-source/source.mp4"
    )


def test_vod_runtime_submit_stages_public_source_on_demand(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime_vod import SubtitleRemovalVodRuntime

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")
    task_state.create_subtitle_removal(
        "sr-vod-public-source",
        str(source_video),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-vod-public-source",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="",
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.object_keys.build_source_object_key",
        lambda user_id, task_id, original_filename: f"uploads/{user_id}/{task_id}/{original_filename}",
    )
    uploaded = []
    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.upload_file",
        lambda local_path, object_key: uploaded.append((local_path, object_key)),
    )
    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda object_key, expires=86400: f"https://example.com/{object_key}",
    )
    captured = {}
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime_vod.upload_media_by_url",
        lambda source_url, title="": captured.setdefault("source_url", source_url) or "job-1",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime_vod.wait_for_upload", lambda job_id, timeout_seconds=None: "vid-1")
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime_vod.start_erase_execution",
        lambda **kwargs: captured.setdefault("start_kwargs", kwargs) or "run-1",
    )

    runner = SubtitleRemovalVodRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-vod-public-source")

    saved = task_state.get("sr-vod-public-source")
    assert uploaded == [(str(source_video), "uploads/1/sr-vod-public-source/source.mp4")]
    assert saved["source_tos_key"] == "uploads/1/sr-vod-public-source/source.mp4"
    assert captured["source_url"] == "https://example.com/uploads/1/sr-vod-public-source/source.mp4"
    assert captured["start_kwargs"]["vid"] == "vid-1"


def test_vod_runtime_uses_backup_tos_signed_source_url(monkeypatch, tmp_path):
    from appcore import tos_backup_storage
    from appcore.subtitle_removal_runtime_vod import SubtitleRemovalVodRuntime

    task_state.create_subtitle_removal(
        "sr-vod-backup-source",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-vod-backup-source",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="FILES/test/subtitle_removal/uploads/1/sr-vod-backup-source/source.mp4",
        source_object_info={
            "public_source_storage_backend": "tos_backup",
            "public_source_key": "FILES/test/subtitle_removal/uploads/1/sr-vod-backup-source/source.mp4",
        },
    )

    monkeypatch.setattr(
        "appcore.subtitle_removal_source_storage.tos_clients.generate_signed_download_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy TOS should not be used")),
    )
    monkeypatch.setattr(
        tos_backup_storage,
        "generate_signed_download_url",
        lambda object_key, expires=86400: f"https://backup.example/{object_key}",
        raising=False,
    )
    captured = {}
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime_vod.upload_media_by_url",
        lambda source_url, title="": captured.setdefault("source_url", source_url) or "job-1",
    )
    monkeypatch.setattr("appcore.subtitle_removal_runtime_vod.wait_for_upload", lambda job_id, timeout_seconds=None: "vid-1")
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime_vod.start_erase_execution",
        lambda **kwargs: captured.setdefault("start_kwargs", kwargs) or "run-1",
    )

    runner = SubtitleRemovalVodRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-vod-backup-source")

    assert captured["source_url"] == (
        "https://backup.example/FILES/test/subtitle_removal/uploads/1/sr-vod-backup-source/source.mp4"
    )
