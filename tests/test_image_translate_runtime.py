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


def test_runtime_downloads_media_bucket_source_and_auto_applies(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([
        {
            **_item(0, src="1/medias/1/en_1.jpg"),
            "source_bucket": "media",
            "source_detail_image_id": 11,
        }
    ])
    task["preset"] = "detail"
    task["medias_context"] = {
        "entry": "medias_edit_detail",
        "product_id": 100,
        "source_lang": "en",
        "target_lang": "de",
        "source_bucket": "media",
        "source_detail_image_ids": [11],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
    }

    applied = {}

    def fake_download_media(key, local_path):
        open(local_path, "wb").write(b"EN")
        return local_path

    def fake_download(key, local_path):
        if key.startswith("1/medias/"):
            raise AssertionError("should not use upload bucket downloader for media source")
        open(local_path, "wb").write(b"OUT")
        return local_path

    def fake_upload_media_object(object_key, data, content_type=None, bucket=None):
        applied["uploaded_key"] = object_key
        applied["uploaded_type"] = content_type

    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, images)
        return [901]

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_media_file", side_effect=fake_download_media), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.tos_clients, "upload_media_object", side_effect=fake_upload_media_object), \
         patch.object(rt.tos_clients, "build_media_object_key", return_value="1/medias/100/de_1.png"), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")), \
         patch.object(rt.medias, "replace_detail_images_for_lang", side_effect=fake_replace):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert applied["replace"][0] == 100
    assert applied["replace"][1] == "de"
    assert applied["replace"][2][0]["origin_type"] == "image_translate"
    assert applied["replace"][2][0]["source_detail_image_id"] == 11
    assert task["medias_context"]["apply_status"] == "applied"
    assert task["medias_context"]["applied_detail_image_ids"] == [901]


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


def test_runtime_circuit_breaks_under_429_storm(tmp_path):
    """持续 429 时应熔断：剩余 items 不再发起 generate_image，全部置 failed。

    生产事故复现：boot -1 死亡前 46 秒里 gemini-3-pro 收到 22+ 次 429，
    runtime 没有任务级熔断，每个 item 都跑完 3 次 retry，触发硬件 watchdog。
    """
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(i) for i in range(5)])

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    call_count = [0]

    def fake_gen(*a, **kw):
        call_count[0] += 1
        raise GeminiImageRetryable("429 Too Many Requests")

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    # 没有熔断时应该是 5 items × 3 attempts = 15 次。熔断后远低于此。
    assert call_count[0] < 10, (
        f"circuit breaker should fire and stop further calls; got {call_count[0]} calls"
    )
    # 所有 items 必须是 failed（含未来得及尝试的 items）
    for i in range(5):
        assert task["items"][i]["status"] == "failed", (
            f"item {i} status={task['items'][i]['status']}"
        )
    # 至少有一个 item 的 error 标明熔断原因（"限流"或"熔断"）
    reasons = [it.get("error", "") for it in task["items"]]
    assert any("限流" in r or "熔断" in r for r in reasons), (
        f"no circuit-breaker reason in item errors: {reasons}"
    )


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


def test_runtime_skips_auto_apply_when_any_item_failed():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0), _item(1)])
    task["items"][0]["status"] = "done"
    task["items"][0]["dst_tos_key"] = "artifacts/image_translate/1/t-img-1/out_0.png"
    task["items"][1]["status"] = "failed"
    task["medias_context"] = {
        "entry": "medias_edit_detail",
        "product_id": 100,
        "target_lang": "de",
        "auto_apply_detail_images": True,
        "apply_status": "pending",
    }

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.medias, "replace_detail_images_for_lang", side_effect=AssertionError("must not apply")):
        runtime._finalize_auto_apply(task)

    assert task["medias_context"]["apply_status"] == "skipped_failed"
