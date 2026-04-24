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


def test_runtime_uses_registered_billing_use_case(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])

    def fake_download(key, local_path):
        open(local_path, "wb").write(b"IMG-" + key.encode())
        return local_path

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda local_path, key: None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")) as gen:
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert gen.call_args.kwargs["service"] == "image_translate.generate"


def test_runtime_passes_seedream_model_through_generate_image(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["model_id"] = "doubao-seedream-5-0-260128"

    def fake_download(key, local_path):
        open(local_path, "wb").write(b"IMG-" + key.encode())
        return local_path

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda local_path, key: None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")) as gen:
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert gen.call_args.kwargs["model"] == "doubao-seedream-5-0-260128"


def test_runtime_skips_gif_source_without_text_detection_or_generate(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([{
        **_item(0, src="1/medias/1/en_anim.gif"),
        "filename": "en_anim.gif",
    }])
    written = {}

    def fake_download_local(key, local_path):
        open(local_path, "wb").write(b"GIF89a-test")
        return local_path

    def fake_write_bytes(object_key, data):
        written["object_key"] = object_key
        written["data"] = data

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.local_media_storage, "exists", return_value=True), \
         patch.object(rt.local_media_storage, "download_to", side_effect=fake_download_local), \
         patch.object(rt.local_media_storage, "write_bytes", side_effect=fake_write_bytes), \
         patch.object(
             rt.ImageTranslateRuntime,
             "_detect_source_text",
             side_effect=AssertionError("gif source should bypass text detection"),
         ), \
         patch.object(
             rt.gemini_image,
             "generate_image",
             side_effect=AssertionError("gif source should bypass image generation"),
         ):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "done"
    assert task["items"][0]["result_source"] == "copied_source"
    assert task["items"][0]["dst_tos_key"].endswith(".gif")
    assert written["object_key"].endswith(".gif")
    assert written["data"] == b"GIF89a-test"


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

    def fake_write_bytes(object_key, data):
        applied["uploaded_key"] = object_key
        applied["uploaded_size"] = len(data)

    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, images)
        return [901]

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_media_file", side_effect=fake_download_media), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.local_media_storage, "write_bytes", side_effect=fake_write_bytes), \
         patch.object(rt.tos_clients, "build_media_object_key", return_value="1/medias/100/de_1.png"), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")), \
         patch.object(rt.medias, "replace_translated_detail_images_for_lang", side_effect=fake_replace):
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
         patch.object(rt.medias, "replace_translated_detail_images_for_lang", side_effect=AssertionError("must not apply")):
        runtime._finalize_auto_apply(task)

    assert task["medias_context"]["apply_status"] == "skipped_failed"


def _done_item(idx: int, *, src_id: int | None = None) -> dict:
    return {
        "idx": idx,
        "filename": f"a{idx}.jpg",
        "src_tos_key": f"1/medias/100/src_{idx}.jpg",
        "source_bucket": "media",
        "source_detail_image_id": src_id or (10 + idx),
        "dst_tos_key": f"artifacts/image_translate/1/t-img-1/out_{idx}.png",
        "status": "done",
        "attempts": 1,
        "error": "",
    }


def _apply_test_task(items: list[dict]) -> dict:
    """minimal task dict for apply_translated_detail_images_from_task."""
    task = _fake_task(items)
    task["preset"] = "detail"
    task["medias_context"] = {
        "entry": "medias_edit_detail",
        "product_id": 100,
        "source_lang": "en",
        "target_lang": "de",
        "source_bucket": "media",
        "auto_apply_detail_images": True,
        "apply_status": "pending",
    }
    return task


def _patch_tos_success(rt, applied: dict):
    def fake_download(key, lp):
        open(lp, "wb").write(b"PNG-" + key.encode())
        return lp
    def fake_write_bytes(object_key, data):
        applied.setdefault("uploaded", []).append((object_key, len(data)))
    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, list(images))
        return list(range(900, 900 + len(images)))
    return (
        patch.object(rt.tos_clients, "download_file", side_effect=fake_download),
        patch.object(rt.local_media_storage, "write_bytes", side_effect=fake_write_bytes),
        patch.object(rt.tos_clients, "build_media_object_key",
                     side_effect=lambda uid, pid, fn: f"{uid}/medias/{pid}/{fn}"),
        patch.object(rt.medias, "replace_translated_detail_images_for_lang", side_effect=fake_replace),
    )


def test_apply_allow_partial_ignores_failed_and_applies_done():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _apply_test_task([_done_item(0), _done_item(1)])
    task["items"][1]["status"] = "failed"
    task["items"][1]["dst_tos_key"] = ""
    task["items"][1]["error"] = "gemini rejected"

    applied: dict = {}
    patches = _patch_tos_success(rt, applied)
    with patch.object(store, "update"), patches[0], patches[1], patches[2], patches[3]:
        result = rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )

    assert result["apply_status"] == "applied_partial"
    assert result["applied_ids"] == [900]
    assert result["skipped_failed_indices"] == [1]
    assert len(applied["replace"][2]) == 1
    assert applied["replace"][2][0]["source_detail_image_id"] == 10
    assert task["medias_context"]["apply_status"] == "applied_partial"
    assert task["medias_context"]["skipped_failed_indices"] == [1]


def test_apply_allow_partial_applies_all_when_all_done():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _apply_test_task([_done_item(0), _done_item(1)])

    applied: dict = {}
    patches = _patch_tos_success(rt, applied)
    with patch.object(store, "update"), patches[0], patches[1], patches[2], patches[3]:
        result = rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )

    assert result["apply_status"] == "applied"
    assert result["skipped_failed_indices"] == []
    assert len(applied["replace"][2]) == 2


def test_apply_allow_partial_skips_done_gif_source_items():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _apply_test_task([
        _done_item(0),
        {
            **_done_item(1),
            "filename": "anim.gif",
            "src_tos_key": "1/medias/100/src_1.gif",
            "dst_tos_key": "artifacts/image_translate/1/t-img-1/out_1.gif",
        },
    ])
    applied = {}

    def fake_download(key, local_path):
        open(local_path, "wb").write(b"BIN-" + key.encode())
        return local_path

    def fake_write_bytes(object_key, payload):
        applied.setdefault("stored", []).append((object_key, payload))

    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, list(images))
        return [901]

    with patch.object(store, "update"), \
         patch.object(rt.local_media_storage, "exists", return_value=True), \
         patch.object(rt.local_media_storage, "download_to", side_effect=fake_download), \
         patch.object(rt.local_media_storage, "write_bytes", side_effect=fake_write_bytes), \
         patch.object(rt.object_keys, "build_media_object_key", return_value="1/medias/100/detail_0.png"), \
         patch.object(rt.medias, "replace_translated_detail_images_for_lang", side_effect=fake_replace):
        result = rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )

    assert result["apply_status"] == "applied"
    assert result["applied_ids"] == [901]
    assert result["skipped_source_gif_indices"] == [1]
    assert len(applied["replace"][2]) == 1
    assert applied["replace"][2][0]["source_detail_image_id"] == 10
    assert task["medias_context"]["skipped_source_gif_indices"] == [1]


def test_apply_allow_partial_false_skips_when_any_failed():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _apply_test_task([_done_item(0), _done_item(1)])
    task["items"][1]["status"] = "failed"
    task["items"][1]["error"] = "gemini rejected"

    with patch.object(store, "update"), \
         patch.object(rt.medias, "replace_translated_detail_images_for_lang",
                      side_effect=AssertionError("must not apply")):
        result = rt.apply_translated_detail_images_from_task(
            task, allow_partial=False, user_id=1,
        )

    assert result["apply_status"] == "skipped_failed"
    assert result["applied_ids"] == []
    assert result["skipped_failed_indices"] == [1]
    assert task["medias_context"]["apply_status"] == "skipped_failed"


def test_apply_raises_when_pending_items():
    from appcore import image_translate_runtime as rt

    task = _apply_test_task([_done_item(0), _done_item(1)])
    task["items"][1]["status"] = "pending"
    task["items"][1]["dst_tos_key"] = ""

    with pytest.raises(RuntimeError, match="pending"):
        rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )


def test_apply_raises_when_no_done_items():
    from appcore import image_translate_runtime as rt

    task = _apply_test_task([_done_item(0)])
    task["items"][0]["status"] = "failed"
    task["items"][0]["dst_tos_key"] = ""
    task["items"][0]["error"] = "gemini rejected"

    with pytest.raises(RuntimeError, match="没有成功的翻译结果"):
        rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )


def test_apply_raises_when_medias_context_missing():
    from appcore import image_translate_runtime as rt

    task = _fake_task([_done_item(0)])
    task.pop("medias_context", None)

    with pytest.raises(ValueError, match="medias_context"):
        rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )


def test_runtime_prefers_local_media_store_before_legacy_tos(tmp_path):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([
        {
            **_item(0, src="1/medias/1/en_1.jpg"),
            "source_bucket": "media",
        }
    ])

    def fake_download_local(key, local_path):
        open(local_path, "wb").write(b"LOCAL-" + key.encode())
        return local_path

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.local_media_storage, "download_to", side_effect=fake_download_local) as local_dl, \
         patch.object(
             rt.tos_clients,
             "download_media_file",
             side_effect=AssertionError("should not fall back to legacy TOS when local copy exists"),
         ), \
         patch.object(rt.tos_clients, "upload_file", lambda local_path, key: None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert local_dl.call_count == 1
    assert task["items"][0]["status"] == "done"


def test_apply_translated_detail_images_writes_local_media_store_instead_of_tos_media(
    tmp_path,
):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _apply_test_task([_done_item(0)])
    applied = {}

    def fake_download(key, local_path):
        open(local_path, "wb").write(b"PNG-" + key.encode())
        return local_path

    def fake_write_bytes(object_key, payload):
        applied["stored"] = (object_key, payload)

    def fake_replace(product_id, lang, images):
        applied["replace"] = (product_id, lang, list(images))
        return [901]

    with patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.local_media_storage, "write_bytes", side_effect=fake_write_bytes), \
         patch.object(
             rt.tos_clients,
             "upload_media_object",
             side_effect=AssertionError("should not upload long-lived detail images back to TOS media"),
         ), \
         patch.object(rt.tos_clients, "build_media_object_key", return_value="1/medias/100/detail_0.png"), \
         patch.object(rt.medias, "replace_translated_detail_images_for_lang", side_effect=fake_replace):
        result = rt.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=1,
        )

    assert result["apply_status"] == "applied"
    assert applied["stored"][0] == "1/medias/100/detail_0.png"
    assert applied["replace"][2][0]["object_key"] == "1/medias/100/detail_0.png"


def test_create_image_translate_stores_concurrency_mode():
    """task_state.create_image_translate 接受 concurrency_mode 并写入 state；默认 sequential。"""
    from appcore import task_state as ts
    from unittest.mock import patch

    with patch.object(ts, "_db_upsert"):  # 不走 DB
        # 1) 默认
        t1 = ts.create_image_translate(
            "t-cm-1", "/tmp/x",
            user_id=1, preset="cover", target_language="de",
            target_language_name="德语", model_id="gemini-x",
            prompt="p", items=[],
        )
        assert t1["concurrency_mode"] == "sequential"

        # 2) 显式 parallel
        t2 = ts.create_image_translate(
            "t-cm-2", "/tmp/x",
            user_id=1, preset="cover", target_language="de",
            target_language_name="德语", model_id="gemini-x",
            prompt="p", items=[],
            concurrency_mode="parallel",
        )
        assert t2["concurrency_mode"] == "parallel"

    # cleanup
    with ts._lock:
        ts._tasks.pop("t-cm-1", None)
        ts._tasks.pop("t-cm-2", None)


def test_parallel_runs_all_items_and_is_faster_than_sequential(tmp_path):
    """并行模式：20 张图每张 sleep 50ms，总耗时 << 串行 1s。"""
    import time as _time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(20)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        _time.sleep(0.05)
        return b"OUT", "image/png"

    t0 = _time.monotonic()
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")
    elapsed = _time.monotonic() - t0

    assert elapsed < 0.5, f"parallel should be fast, got {elapsed:.2f}s"
    for it in task["items"]:
        assert it["status"] == "done", (it["idx"], it)


def test_parallel_runs_in_batches_of_10(tmp_path):
    """21 个 item：前 10 个并发，第二批在第一批之后启动，第 21 个自成一批。"""
    import time as _time
    import threading
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(21)])
    task["concurrency_mode"] = "parallel"

    starts = {}
    lock = threading.Lock()

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        with lock:
            starts[_time.monotonic()] = True
        _time.sleep(0.1)
        return b"OUT", "image/png"

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    start_times = sorted(starts.keys())
    assert len(start_times) == 21
    assert start_times[9] - start_times[0] < 0.1, f"first batch spread: {start_times[9]-start_times[0]:.3f}s"
    assert start_times[10] - start_times[0] > 0.08, f"batch 2 gap: {start_times[10]-start_times[0]:.3f}s"
    for it in task["items"]:
        assert it["status"] == "done"


def test_parallel_skips_already_terminal_items(tmp_path):
    """已 done/failed 的 item 在并行模式下也不重跑。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(12)])
    task["concurrency_mode"] = "parallel"
    task["items"][0]["status"] = "done"
    task["items"][1]["status"] = "failed"

    call_count = [0]

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        call_count[0] += 1
        return b"OUT", "image/png"

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert call_count[0] == 10
    assert task["items"][0]["status"] == "done"
    assert task["items"][1]["status"] == "failed"
    for i in range(2, 12):
        assert task["items"][i]["status"] == "done"


def test_parallel_circuit_breaker_aborts_remaining(tmp_path):
    """并行下若上游持续 429，_CircuitOpen 穿透后剩余 items 全部标 failed。"""
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(i) for i in range(15)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        raise GeminiImageRetryable("429 Too Many Requests")

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    for it in task["items"]:
        assert it["status"] == "failed", (it["idx"], it)
    reasons = [it.get("error", "") for it in task["items"]]
    assert any("限流" in r or "熔断" in r for r in reasons), reasons
    assert task["status"] == "error"


def test_parallel_progress_is_consistent(tmp_path):
    """并行跑完后 progress 自洽：total=sum(done+failed+running+pending)，running=0。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(15)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    p = task["progress"]
    assert p["total"] == 15
    assert p["done"] == 15
    assert p["failed"] == 0
    assert p["running"] == 0


def test_parallel_no_lost_updates_under_contention(tmp_path):
    """20 个 item 并发 done，最终 items 列表每个 status=done，无丢失更新。"""
    import time as _time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(20)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        _time.sleep(0.02)
        return b"OUT", "image/png"

    store_updates = []

    def rec_update(task_id, **kw):
        if "progress" in kw:
            p = kw["progress"]
            store_updates.append(dict(p))

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update", side_effect=rec_update), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert all(it["status"] == "done" for it in task["items"])
    for p in store_updates:
        assert p["done"] + p["failed"] + p["running"] <= p["total"], p
    assert task["progress"]["done"] == 20


def test_recovery_poll_resumes_existing_apimart_task_within_window():
    """item 带有 10 分钟内的 apimart_task_id 时，应直接轮询而不是重新提交。"""
    import time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["channel"] = "apimart"
    task["items"][0]["apimart_task_id"] = "task_resume_me"
    task["items"][0]["apimart_submitted_at"] = time.time() - 30  # 30 秒前提交

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.gemini_image, "APIMART_IMAGE_API_KEY", "key"), \
         patch.object(
             rt.gemini_image, "poll_apimart_task",
             return_value=(b"RESUMED", "image/png", {}),
         ) as m_poll, \
         patch.object(rt.gemini_image, "generate_image") as m_gen:
        out, mime = runtime._generate_with_apimart_recovery(
            task, "t-img-1", task["items"][0], 0, b"SRC", "image/png",
        )

    assert out == b"RESUMED"
    assert mime == "image/png"
    m_poll.assert_called_once()
    assert m_poll.call_args.kwargs["initial_wait"] is False
    m_gen.assert_not_called()  # 不能走重新提交


def test_recovery_falls_back_to_regenerate_when_task_expired():
    """apimart_task_id 超出 10 分钟窗口时放弃快照，走完整重新提交。"""
    import time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["channel"] = "apimart"
    task["items"][0]["apimart_task_id"] = "task_too_old"
    task["items"][0]["apimart_submitted_at"] = time.time() - 9999

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.gemini_image, "APIMART_IMAGE_API_KEY", "key"), \
         patch.object(rt.gemini_image, "poll_apimart_task") as m_poll, \
         patch.object(
             rt.gemini_image, "generate_image",
             return_value=(b"NEW", "image/png"),
         ) as m_gen:
        out, _ = runtime._generate_with_apimart_recovery(
            task, "t-img-1", task["items"][0], 0, b"SRC", "image/png",
        )

    assert out == b"NEW"
    m_poll.assert_not_called()
    m_gen.assert_called_once()


def test_recovery_clears_task_id_and_regenerates_on_upstream_failure():
    """已提交的 APIMART 任务在 upstream 明确 failed 时，清掉快照并重新提交。"""
    import time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["channel"] = "apimart"
    task["items"][0]["apimart_task_id"] = "task_will_fail"
    task["items"][0]["apimart_submitted_at"] = time.time() - 20

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.gemini_image, "APIMART_IMAGE_API_KEY", "key"), \
         patch.object(
             rt.gemini_image, "poll_apimart_task",
             side_effect=rt.gemini_image.GeminiImageError("content policy violation"),
         ) as m_poll, \
         patch.object(
             rt.gemini_image, "generate_image",
             return_value=(b"RETRY", "image/png"),
         ) as m_gen:
        out, _ = runtime._generate_with_apimart_recovery(
            task, "t-img-1", task["items"][0], 0, b"SRC", "image/png",
        )

    assert out == b"RETRY"
    m_poll.assert_called_once()
    m_gen.assert_called_once()
    # 快照应被清理，避免下轮又去 poll 同一个已失败的 task
    assert task["items"][0]["provider_task_id"] == ""
    assert task["items"][0]["provider_task_submitted_at"] == 0.0
    # 旧字段同步清空
    assert task["items"][0]["apimart_task_id"] == ""
    assert task["items"][0]["apimart_submitted_at"] == 0.0


def test_recovery_normal_path_saves_task_id_via_callback():
    """首次提交时，generate_image 的 on_apimart_submitted 回调要把 task_id 落到 item 上。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["channel"] = "apimart"
    # 没有 apimart_task_id
    assert not task["items"][0].get("apimart_task_id")

    def fake_generate(**kwargs):
        on_cb = kwargs.get("on_apimart_submitted")
        assert callable(on_cb)
        on_cb("task_fresh_xyz")
        return b"FRESH", "image/png"

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.gemini_image, "APIMART_IMAGE_API_KEY", "key"), \
         patch.object(rt.gemini_image, "poll_apimart_task") as m_poll, \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_generate):
        out, _ = runtime._generate_with_apimart_recovery(
            task, "t-img-1", task["items"][0], 0, b"SRC", "image/png",
        )

    assert out == b"FRESH"
    m_poll.assert_not_called()
    # 新代码统一写通用字段 provider_task_id / provider_task_submitted_at
    assert task["items"][0]["provider_task_id"] == "task_fresh_xyz"
    assert task["items"][0]["provider_task_submitted_at"] > 0


def test_recovery_non_apimart_channel_skips_recovery_logic():
    """非 APIMART 通道不启用 task_id 快照机制。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0)])
    task["channel"] = "openrouter"
    # 即使误带 apimart_task_id，也不该触发 poll
    task["items"][0]["apimart_task_id"] = "task_should_be_ignored"

    runtime = rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1)
    with patch.object(store, "update"), \
         patch.object(rt.gemini_image, "poll_apimart_task") as m_poll, \
         patch.object(
             rt.gemini_image, "generate_image",
             return_value=(b"OPENROUTER-OUT", "image/png"),
         ) as m_gen:
        runtime._generate_with_apimart_recovery(
            task, "t-img-1", task["items"][0], 0, b"SRC", "image/png",
        )

    m_poll.assert_not_called()
    m_gen.assert_called_once()
    # non-apimart 通道，on_apimart_submitted 应该传 None
    assert m_gen.call_args.kwargs.get("on_apimart_submitted") is None
