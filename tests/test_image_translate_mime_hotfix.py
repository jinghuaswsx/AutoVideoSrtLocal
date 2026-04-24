from unittest.mock import MagicMock, patch


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _fake_task(items):
    return {
        "id": "t-img-mime",
        "type": "image_translate",
        "status": "queued",
        "task_dir": "/tmp/t-img-mime",
        "preset": "detail",
        "target_language": "ja",
        "target_language_name": "日语",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "...",
        "items": items,
        "progress": {"total": len(items), "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "pending"},
        "step_messages": {"prepare": "", "process": ""},
        "error": "",
        "_user_id": 1,
    }


def test_runtime_sniffs_png_mime_when_source_key_has_no_extension():
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([{
        "idx": 0,
        "filename": "detail_001",
        "src_tos_key": "1/medias/42/detail_001",
        "source_bucket": "media",
        "dst_tos_key": "",
        "status": "pending",
        "attempts": 0,
        "error": "",
    }])

    def fake_download_local(key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(PNG_BYTES)
        return local_path

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.local_media_storage, "exists", return_value=True), \
         patch.object(rt.local_media_storage, "download_to", side_effect=fake_download_local), \
         patch.object(rt.local_media_storage, "write_bytes"), \
         patch.object(rt.ImageTranslateRuntime, "_detect_source_text", return_value=True), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")) as gen:
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-mime")

    assert gen.call_args.kwargs["source_mime"] == "image/png"
