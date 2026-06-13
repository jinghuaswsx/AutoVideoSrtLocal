"""Block3 R3: 尾部截断的被删句文本 + 任务级质量告警。"""
import os
from unittest.mock import patch

import pytest


def _make_runner():
    from appcore.events import EventBus
    from appcore.runtime import PipelineRunner
    return PipelineRunner(bus=EventBus(), user_id=1)


def test_truncate_returns_removed_texts(tmp_path, monkeypatch):
    runner = _make_runner()
    in_path = tmp_path / "in.mp3"
    in_path.write_bytes(b"fake")
    out_path = str(tmp_path / "out.mp3")

    segs = [
        {"index": 0, "tts_text": "Hook sentence.", "tts_duration": 10.0},
        {"index": 1, "tts_text": "Middle.", "tts_duration": 10.0},
        {"index": 2, "tts_text": "Final CTA: buy now.", "tts_duration": 10.0},
    ]

    # 让 ffmpeg 截断与元数据裁剪都成为可控桩：保留前 2 段。
    monkeypatch.setattr(
        "appcore.runtime._pipeline_runner._fit_tts_segments_to_duration",
        lambda tts_segments, duration: tts_segments[:2],
    )
    monkeypatch.setattr(
        "appcore.runtime._pipeline_runner._trim_tts_metadata_to_segments",
        lambda script, loc, fitted: (script, loc),
    )

    class _R:
        returncode = 0
        stderr = ""

    with patch("subprocess.run", return_value=_R()):
        result = runner._truncate_audio_to_duration(
            input_audio_path=str(in_path), output_audio_path=out_path,
            duration=20.0, tts_segments=segs, tts_script={}, localized_translation={},
        )

    assert result["removed_count"] == 1
    assert result["removed_texts"] == ["Final CTA: buy now."]


def test_segment_text_fallback_order():
    from appcore.runtime._pipeline_runner import _truncation_segment_text
    assert _truncation_segment_text({"tts_text": "a", "translated": "b"}) == "a"
    assert _truncation_segment_text({"translated": "b", "text": "c"}) == "b"
    assert _truncation_segment_text({"text": "c"}) == "c"
    assert _truncation_segment_text({}) == ""
    assert _truncation_segment_text({"tts_text": "  "}) == ""


def test_record_warning_appends_quality_warnings(monkeypatch):
    runner = _make_runner()
    captured = {"warnings": None}

    fake_task = {"quality_warnings": []}
    monkeypatch.setattr(
        "appcore.task_state.get", lambda task_id: fake_task)

    def fake_update(task_id, **kwargs):
        captured["warnings"] = kwargs.get("quality_warnings")

    monkeypatch.setattr("appcore.task_state.update", fake_update)

    round_record = {}
    runner._record_tail_truncation_warning(
        "t1", round_record,
        {"removed_count": 2, "removed_texts": ["s1", "s2", "s3", "s4"]},
    )
    assert captured["warnings"] is not None
    w = captured["warnings"][0]
    assert w["type"] == "tail_truncated"
    assert w["removed_count"] == 2
    assert w["removed_texts"] == ["s1", "s2", "s3", "s4"]
    assert "尾部被截断 2 句" in w["message"]
    assert round_record["removed_texts_preview"] == ["s1", "s2", "s3"]


def test_record_warning_noop_when_nothing_removed(monkeypatch):
    runner = _make_runner()
    called = {"update": False}
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {})
    monkeypatch.setattr(
        "appcore.task_state.update",
        lambda *a, **kw: called.__setitem__("update", True))
    round_record = {}
    runner._record_tail_truncation_warning("t1", round_record, {"removed_count": 0})
    assert called["update"] is False
    assert "removed_texts_preview" not in round_record


def test_record_warning_preserves_existing_warnings(monkeypatch):
    runner = _make_runner()
    captured = {}
    existing = [{"type": "other", "message": "x"}]
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda task_id: {"quality_warnings": existing})
    monkeypatch.setattr(
        "appcore.task_state.update",
        lambda task_id, **kw: captured.update(kw))
    runner._record_tail_truncation_warning(
        "t1", {}, {"removed_count": 1, "removed_texts": ["s"]})
    assert len(captured["quality_warnings"]) == 2
    assert captured["quality_warnings"][0]["type"] == "other"
    assert captured["quality_warnings"][1]["type"] == "tail_truncated"
