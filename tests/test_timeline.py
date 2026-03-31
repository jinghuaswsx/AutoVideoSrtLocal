from pipeline.timeline import build_timeline_manifest


def test_build_timeline_manifest_creates_continuous_timeline():
    segments = [
        {
            "index": 0,
            "text": "第一段",
            "translated": "First line",
            "start_time": 0.0,
            "end_time": 1.0,
            "utterance_indices": [0],
            "tts_duration": 1.5,
            "tts_path": "seg0.mp3",
        },
        {
            "index": 1,
            "text": "第二段",
            "translated": "Second line",
            "start_time": 1.0,
            "end_time": 2.0,
            "utterance_indices": [1],
            "tts_duration": 2.0,
            "tts_path": "seg1.mp3",
        },
    ]

    manifest = build_timeline_manifest(segments, video_duration=10.0)

    assert manifest["total_tts_duration"] == 3.5
    assert manifest["segments"][0]["timeline_start"] == 0.0
    assert manifest["segments"][0]["timeline_end"] == 1.5
    assert manifest["segments"][1]["timeline_start"] == 1.5
    assert manifest["segments"][1]["timeline_end"] == 3.5
    assert manifest["segments"][0]["video_ranges"] == [{"start": 0.0, "end": 1.5}]
    assert manifest["segments"][1]["video_ranges"] == [{"start": 1.5, "end": 3.5}]


def test_build_timeline_manifest_marks_tail_truncation_when_video_runs_out():
    segments = [
        {
            "index": 0,
            "text": "第一段",
            "translated": "First line",
            "start_time": 0.0,
            "end_time": 1.0,
            "utterance_indices": [0],
            "tts_duration": 4.0,
            "tts_path": "seg0.mp3",
        },
        {
            "index": 1,
            "text": "第二段",
            "translated": "Second line",
            "start_time": 1.0,
            "end_time": 2.0,
            "utterance_indices": [1],
            "tts_duration": 4.0,
            "tts_path": "seg1.mp3",
        },
    ]

    manifest = build_timeline_manifest(segments, video_duration=6.0)

    assert manifest["segments"][0]["video_ranges"] == [{"start": 0.0, "end": 4.0}]
    assert manifest["segments"][1]["video_ranges"] == [{"start": 4.0, "end": 6.0}]
    assert manifest["segments"][1]["video_truncated"] is True
    assert manifest["video_consumed_duration"] == 6.0


def test_build_timeline_manifest_uses_tts_block_text_and_source_window():
    manifest = build_timeline_manifest(
        [
            {
                "index": 0,
                "text": "part one",
                "translated": "Hook line.",
                "source_segment_indices": [0],
                "start_time": 0.0,
                "end_time": 1.0,
                "tts_duration": 1.5,
            }
        ],
        video_duration=10.0,
    )

    assert manifest["segments"][0]["translated"] == "Hook line."
    assert manifest["segments"][0]["source_window"]["end"] == 1.0
    assert manifest["segments"][0]["source_segment_indices"] == [0]
