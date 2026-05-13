from unittest.mock import patch
from pipeline.shot_decompose import decompose_shots, align_asr_to_shots


def test_decompose_shots_parses_response_and_normalizes_boundaries():
    fake_response = {
        "json": {
            "shots": [
                {"index": 1, "start": 0.0, "end": 5.2,
                 "description": "女主角走进咖啡厅"},
                {"index": 2, "start": 5.3, "end": 9.8,
                 "description": "镜头切到吧台"},
            ]
        },
        "text": None,
        "raw": None,
        "usage": {},
    }
    with patch("pipeline.shot_decompose.gemini_generate",
               return_value=fake_response):
        shots = decompose_shots(
            video_path="/tmp/v.mp4",
            user_id=1,
            duration_seconds=10.0,
        )
    assert len(shots) == 2
    # 首尾强制对齐
    assert shots[0]["start"] == 0.0
    assert shots[-1]["end"] == 10.0
    # 首尾相接
    assert shots[1]["start"] == shots[0]["end"]
    # duration 计算
    assert "duration" in shots[0]


def test_decompose_shots_keeps_model_end_when_duration_unknown():
    fake_response = {
        "json": {
            "shots": [
                {"index": 1, "start": 0.0, "end": 17.33,
                 "description": "product demo"},
                {"index": 2, "start": 17.33, "end": 23.47,
                 "description": "rear mirror and CTA"},
            ]
        },
        "text": None,
        "raw": None,
        "usage": {},
    }
    with patch("pipeline.shot_decompose.gemini_generate",
               return_value=fake_response):
        shots = decompose_shots(
            video_path="/tmp/v.mp4",
            user_id=1,
            duration_seconds=0.0,
        )

    assert shots[-1]["end"] == 23.47
    assert shots[-1]["duration"] > 0


def test_decompose_shots_uses_configured_binding_model_by_default():
    fake_response = {
        "json": {
            "shots": [
                {"index": 1, "start": 0.0, "end": 10.0, "description": "shot"},
            ]
        },
        "text": None,
        "raw": None,
        "usage": {},
    }
    with patch("pipeline.shot_decompose.gemini_generate",
               return_value=fake_response) as generate:
        decompose_shots(
            video_path="/tmp/v.mp4",
            user_id=1,
            duration_seconds=10.0,
        )

    assert generate.call_args.kwargs["model_override"] is None


def test_decompose_shots_raises_when_empty():
    with patch("pipeline.shot_decompose.gemini_generate",
               return_value={"json": {"shots": []}, "text": None, "raw": None, "usage": {}}):
        try:
            decompose_shots(video_path="/tmp/v.mp4", user_id=1,
                             duration_seconds=10.0)
        except ValueError:
            return
    assert False, "应该抛出 ValueError"


def test_align_asr_to_shots_groups_segments_by_time():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0, "duration": 5.0,
         "description": "d1"},
        {"index": 2, "start": 5.0, "end": 10.0, "duration": 5.0,
         "description": "d2"},
    ]
    asr_segments = [
        {"start": 0.5, "end": 4.5, "text": "她推开门"},
        {"start": 5.2, "end": 9.0, "text": "咖啡师正在忙碌"},
    ]
    aligned = align_asr_to_shots(shots, asr_segments)
    assert aligned[0]["source_text"] == "她推开门"
    assert aligned[1]["source_text"] == "咖啡师正在忙碌"
    # 未提供文本的分镜应标记 silent
    aligned2 = align_asr_to_shots(shots, [])
    assert aligned2[0]["silent"] is True
    assert aligned2[1]["silent"] is True


def test_align_asr_splits_cross_boundary_segment_by_overlap():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0, "duration": 5.0,
         "description": "d1"},
        {"index": 2, "start": 5.0, "end": 10.0, "duration": 5.0,
         "description": "d2"},
    ]
    # 4.0 - 7.0：shot1 占 1s，shot2 占 2s，应归到 shot2
    asr_segments = [{"start": 4.0, "end": 7.0, "text": "跨越的句子"}]
    aligned = align_asr_to_shots(shots, asr_segments)
    assert aligned[1]["source_text"] == "跨越的句子"
    assert aligned[0]["source_text"] == ""


def test_align_asr_records_overlap_text_and_does_not_mark_overlapped_shot_silent():
    shots = [
        {"index": 1, "start": 0.0, "end": 3.0, "duration": 3.0, "description": "hook"},
        {"index": 2, "start": 3.0, "end": 6.0, "duration": 3.0, "description": "demo"},
        {"index": 3, "start": 6.0, "end": 10.33, "duration": 4.33, "description": "storage"},
    ]
    asr_segments = [
        {"start": 0.179, "end": 4.159, "text": "Opening hook keeps speaking"},
        {"start": 4.319, "end": 8.679, "text": "Second ASR sentence continues"},
    ]

    aligned = align_asr_to_shots(shots, asr_segments)

    assert aligned[1]["source_text"] == ""
    assert aligned[1]["overlap_source_text"] == (
        "Opening hook keeps speaking Second ASR sentence continues"
    )
    assert [seg["text"] for seg in aligned[1]["overlapping_asr_segments"]] == [
        "Opening hook keeps speaking",
        "Second ASR sentence continues",
    ]
    assert aligned[1]["silent"] is False


def test_align_asr_concatenates_multiple_segments_in_same_shot():
    shots = [
        {"index": 1, "start": 0.0, "end": 10.0, "duration": 10.0,
         "description": "d1"},
    ]
    asr_segments = [
        {"start": 0.5, "end": 2.0, "text": "第一段"},
        {"start": 4.0, "end": 6.0, "text": "第二段"},
    ]
    aligned = align_asr_to_shots(shots, asr_segments)
    assert "第一段" in aligned[0]["source_text"]
    assert "第二段" in aligned[0]["source_text"]
