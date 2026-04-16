from unittest.mock import patch, MagicMock
import pytest


def _fake_task(items):
    return {
        "id": "t-img-1",
        "type": "image_translate",
        "status": "queued",
        "task_dir": "/tmp/t-img-1",
        "preset": "cover",
        "target_language": "de",
        "target_language_name": "德语",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "...",
        "items": items,
        "progress": {"total": len(items), "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "pending"},
        "step_messages": {"prepare": "", "process": ""},
        "error": "",
        "_user_id": 1,
    }


def _item(idx, src=None, status="pending"):
    return {
        "idx": idx, "filename": f"a{idx}.jpg",
        "src_tos_key": src or f"src/{idx}.jpg",
        "dst_tos_key": "", "status": status, "attempts": 0, "error": "",
    }


def test_runtime_processes_all_items_successfully(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0), _item(1)])

    # 把 download_file 做成"写入空 bytes 到 local_path"；upload_file 无操作
    def fake_download(key, local_path):
        open(local_path, "wb").write(b"IMG-" + key.encode())
        return local_path
    def fake_upload(local_path, key):
        pass
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", side_effect=fake_upload), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")) as gen:
        bus = MagicMock()
        rt.ImageTranslateRuntime(bus=bus, user_id=1).start("t-img-1")

    assert gen.call_count == 2
    assert task["items"][0]["status"] == "done"
    assert task["items"][1]["status"] == "done"
    assert task["progress"]["done"] == 2


def test_runtime_retries_on_retryable_error(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(0)])
    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image",
                      side_effect=[GeminiImageRetryable("429"),
                                   GeminiImageRetryable("500"),
                                   (b"OK", "image/png")]):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "done"
    assert task["items"][0]["attempts"] == 3


def test_runtime_gives_up_after_3_retries(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(0)])
    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image",
                      side_effect=GeminiImageRetryable("timeout")):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "failed"
    assert task["items"][0]["attempts"] == 3


def test_runtime_non_retryable_marks_failed_immediately(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageError

    task = _fake_task([_item(0), _item(1)])
    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp
    calls = []
    def fake_gen(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise GeminiImageError("SAFETY")
        return b"OUT", "image/png"
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "failed"
    assert task["items"][0]["attempts"] == 1  # 无重试
    assert task["items"][1]["status"] == "done"
