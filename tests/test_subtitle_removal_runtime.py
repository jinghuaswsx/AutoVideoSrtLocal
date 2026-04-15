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
