"""Tests for appcore/task_state.py"""
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
    assert "hook_cta" in task["variants"]
    assert task["source_tos_key"] == ""
    assert task["source_object_info"] == {}
    assert task["tos_uploads"] == {}
    assert "preparation" not in task


def test_create_stores_original_filename():
    task = ts.create("t2", "/v.mp4", "/task/t2", original_filename="my_video.mp4")
    assert task["original_filename"] == "my_video.mp4"


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
