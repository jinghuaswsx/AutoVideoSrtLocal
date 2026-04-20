"""Tests for appcore/task_state.py"""
import sys
import types

import pytest
import appcore.task_state as ts


@pytest.fixture(autouse=True)
def clear_tasks():
    """Reset in-memory store between tests."""
    ts._tasks.clear()
    yield
    ts._tasks.clear()


def test_create_initializes_expected_keys():
    task = ts.create("t1", "/video.mp4", "/task/t1")
    assert task["id"] == "t1"
    assert task["status"] == "uploaded"
    assert task["video_path"] == "/video.mp4"
    assert task["task_dir"] == "/task/t1"
    assert task["steps"]["extract"] == "pending"
    assert task["steps"]["asr"] == "pending"
    assert "variants" in task
    assert "normal" in task["variants"]
    assert task["source_tos_key"] == ""
    assert task["source_object_info"] == {}
    assert task["tos_uploads"] == {}
    assert "preparation" not in task


def test_create_stores_original_filename():
    task = ts.create("t2", "/v.mp4", "/task/t2", original_filename="my_video.mp4")
    assert task["original_filename"] == "my_video.mp4"


def test_create_contains_av_translate_defaults():
    task = ts.create("t-av", "/v.mp4", "/task/t-av")
    assert "av_translate_inputs" in task
    assert task["av_translate_inputs"]["target_language"] is None
    assert task["av_translate_inputs"]["target_language_name"] is None
    assert task["av_translate_inputs"]["target_market"] is None
    assert task["av_translate_inputs"]["product_overrides"]["product_name"] is None
    assert task["av_translate_inputs"]["product_overrides"]["brand"] is None
    assert task["av_translate_inputs"]["product_overrides"]["selling_points"] is None
    assert task["av_translate_inputs"]["product_overrides"]["price"] is None
    assert task["av_translate_inputs"]["product_overrides"]["target_audience"] is None
    assert task["av_translate_inputs"]["product_overrides"]["extra_info"] is None
    assert task["shot_notes"] is None


def test_create_subtitle_removal_initializes_expected_shape():
    task = ts.create_subtitle_removal(
        "sr-init",
        "uploads/source.mp4",
        "output/sr-init",
        original_filename="source.mp4",
        user_id=9,
    )

    assert task["type"] == "subtitle_removal"
    assert task["status"] == "uploaded"
    assert task["steps"] == {
        "prepare": "pending",
        "submit": "pending",
        "poll": "pending",
        "download_result": "pending",
        "upload_result": "pending",
    }
    assert task["remove_mode"] == ""
    assert task["selection_box"] is None
    assert task["result_tos_key"] == ""


def test_create_subtitle_removal_persists_project_type_in_db_upsert(monkeypatch):
    captured = []
    fake_db = types.SimpleNamespace(execute=lambda sql, args: captured.append((sql, args)))
    monkeypatch.setitem(sys.modules, "appcore.db", fake_db)

    ts.create_subtitle_removal(
        "sr-db-type",
        "uploads/source.mp4",
        "output/sr-db-type",
        original_filename="source.mp4",
        user_id=9,
    )

    assert captured
    sql, args = captured[0]
    assert "type" in sql
    assert "subtitle_removal" in args


def test_get_returns_task():
    ts.create("t1", "/v.mp4", "/d")
    assert ts.get("t1") is not None
    assert ts.get("t1")["id"] == "t1"


def test_get_returns_none_for_missing_task():
    assert ts.get("nonexistent") is None


def test_get_all_returns_copy():
    ts.create("t1", "/v.mp4", "/d")
    ts.create("t2", "/v2.mp4", "/d2")
    all_tasks = ts.get_all()
    assert "t1" in all_tasks
    assert "t2" in all_tasks


def test_set_step_updates_status():
    ts.create("t1", "/v.mp4", "/d")
    ts.set_step("t1", "asr", "running")
    assert ts.get("t1")["steps"]["asr"] == "running"


def test_set_step_noop_for_missing_task():
    ts.set_step("missing", "asr", "running")  # must not raise


def test_set_artifact_stores_payload():
    ts.create("t1", "/v.mp4", "/d")
    ts.set_artifact("t1", "asr", {"segments": ["a", "b"]})
    assert ts.get("t1")["artifacts"]["asr"] == {"segments": ["a", "b"]}


def test_set_variant_artifact_stores_under_variant():
    ts.create("t1", "/v.mp4", "/d")
    ts.set_variant_artifact("t1", "normal", "tts", {"audio": "x.wav"})
    assert ts.get("t1")["variants"]["normal"]["artifacts"]["tts"] == {"audio": "x.wav"}


def test_confirm_alignment_updates_dict_and_flag():
    ts.create("t1", "/v.mp4", "/d")
    ts.confirm_alignment("t1", break_after=[2], script_segments=[{"index": 0, "text": "hi"}])
    task = ts.get("t1")
    assert task["_alignment_confirmed"] is True
    assert task["alignment"]["break_after"] == [2]
    assert task["script_segments"] == [{"index": 0, "text": "hi"}]


def test_confirm_segments_updates_and_sets_flag():
    ts.create("t1", "/v.mp4", "/d")
    ts.confirm_segments("t1", [{"index": 0, "translated": "Hello"}])
    task = ts.get("t1")
    assert task["_segments_confirmed"] is True
    assert task["segments"] == [{"index": 0, "translated": "Hello"}]


def test_set_preview_file_stores_path():
    ts.create("t1", "/v.mp4", "/d")
    ts.set_preview_file("t1", "audio", "/task/t1/audio.wav")
    assert ts.get("t1")["preview_files"]["audio"] == "/task/t1/audio.wav"


def test_set_variant_preview_file_stores_path():
    ts.create("t1", "/v.mp4", "/d")
    ts.set_variant_preview_file("t1", "hook_cta", "tts_audio", "/task/t1/hook_tts.wav")
    assert ts.get("t1")["variants"]["hook_cta"]["preview_files"]["tts_audio"] == "/task/t1/hook_tts.wav"


# ── 并发安全测试 ──────────────────────────────────────

import threading


def test_concurrent_set_step_no_exception():
    """10 线程并发调用 set_step 不抛异常、不丢数据。"""
    ts.create("tc1", "/v.mp4", "/d")
    errors = []
    steps = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]

    def worker(step, status):
        try:
            for _ in range(50):
                ts.set_step("tc1", step, status)
                ts.set_step_message("tc1", step, f"msg-{status}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(steps[i % len(steps)], f"s{i}")) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 set_step 出错: {errors}"
    task = ts.get("tc1")
    # 所有 step 键仍然存在
    for s in steps:
        assert s in task["steps"]


def test_concurrent_create_copywriting_no_data_loss():
    """并发 create_copywriting 不丢失任务。"""
    errors = []
    ids = [f"cw_{i}" for i in range(20)]

    def worker(tid):
        try:
            ts.create_copywriting(tid, "/v.mp4", f"/d/{tid}", "test.mp4", user_id=1)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(tid,)) for tid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 create_copywriting 出错: {errors}"
    for tid in ids:
        assert ts.get(tid) is not None, f"任务 {tid} 丢失"


def test_concurrent_update_variant_no_exception():
    """并发 update_variant 不抛异常。"""
    ts.create("tv1", "/v.mp4", "/d")
    errors = []

    def worker(i):
        try:
            for _ in range(50):
                ts.update_variant("tv1", "normal", **{f"key_{i}": f"val_{i}"})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 update_variant 出错: {errors}"


def test_concurrent_confirm_segments_no_exception():
    """并发 confirm_segments 不抛异常。"""
    ts.create("ts1", "/v.mp4", "/d")
    errors = []

    def worker(i):
        try:
            ts.confirm_segments("ts1", [{"index": 0, "translated": f"Hello {i}"}])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 confirm_segments 出错: {errors}"
    task = ts.get("ts1")
    assert task["_segments_confirmed"] is True


def test_create_image_translate_minimal(tmp_path):
    task_id = "tid-img-1"
    task_dir = str(tmp_path / task_id)
    task = ts.create_image_translate(
        task_id,
        task_dir,
        user_id=1,
        preset="cover",
        target_language="de",
        target_language_name="德语",
        model_id="gemini-3-pro-image-preview",
        prompt="把图中文字翻译成德语",
        items=[
            {"idx": 0, "filename": "a.jpg", "src_tos_key": "src/0.jpg"},
            {"idx": 1, "filename": "b.png", "src_tos_key": "src/1.png"},
        ],
    )
    assert task["type"] == "image_translate"
    assert task["status"] == "queued"
    assert task["preset"] == "cover"
    assert task["target_language"] == "de"
    assert task["model_id"] == "gemini-3-pro-image-preview"
    assert len(task["items"]) == 2
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0
    assert task["progress"] == {"total": 2, "done": 0, "failed": 0, "running": 0}
    assert task["steps"]["process"] == "pending"
    # 读回来状态一致
    got = ts.get(task_id)
    assert got["preset"] == "cover"


def test_create_image_translate_persists_medias_context(tmp_path):
    task_id = "tid-img-medias-1"
    task_dir = str(tmp_path / task_id)
    task = ts.create_image_translate(
        task_id,
        task_dir,
        user_id=1,
        preset="detail",
        target_language="de",
        target_language_name="德语",
        model_id="gemini-3-pro-image-preview",
        prompt="translate to de",
        items=[
            {
                "idx": 0,
                "filename": "en_1.jpg",
                "src_tos_key": "1/medias/1/en_1.jpg",
                "source_bucket": "media",
                "source_detail_image_id": 11,
            }
        ],
        medias_context={
            "entry": "medias_edit_detail",
            "product_id": 123,
            "source_lang": "en",
            "target_lang": "de",
            "source_bucket": "media",
            "auto_apply_detail_images": True,
            "apply_status": "pending",
            "source_detail_image_ids": [11],
        },
    )

    assert task["items"][0]["source_bucket"] == "media"
    assert task["items"][0]["source_detail_image_id"] == 11
    assert task["medias_context"]["product_id"] == 123
    assert task["medias_context"]["apply_status"] == "pending"
    got = ts.get(task_id)
    assert got["medias_context"]["target_lang"] == "de"


def test_create_link_check_initializes_summary_and_progress(tmp_path, monkeypatch):
    captured = {}

    def fake_db_upsert(task_id, user_id, task, original_filename=""):
        captured["task_id"] = task_id
        captured["user_id"] = user_id
        captured["task"] = task
        captured["original_filename"] = original_filename

    monkeypatch.setattr(ts, "_db_upsert", fake_db_upsert)

    task = ts.create_link_check(
        "lc-init-1",
        str(tmp_path / "lc-init-1"),
        user_id=1,
        link_url="https://newjoyloo.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    assert task["type"] == "link_check"
    assert task["status"] == "queued"
    assert task["progress"]["total"] == 0
    assert task["summary"]["overall_decision"] == "running"
    assert task.get("_persist_state") is not False
    assert captured["task_id"] == "lc-init-1"
    assert captured["user_id"] == 1
    assert captured["task"]["type"] == "link_check"


def test_create_task_initializes_tts_duration_fields(tmp_path):
    from appcore import task_state
    task_id = "test-duration-init"
    task = task_state.create(
        task_id, str(tmp_path / "video.mp4"), str(tmp_path / "out"),
        original_filename="video.mp4", user_id=None,
    )
    assert task["tts_duration_rounds"] == []
    assert task["tts_duration_status"] is None
