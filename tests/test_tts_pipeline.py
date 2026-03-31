from pipeline.localization import build_tts_segments


def test_build_tts_segments_projects_block_ranges_back_to_source_segments():
    script_segments = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.5},
    ]
    tts_script = {
        "full_text": "Hook line. Closing line.",
        "blocks": [
            {"index": 0, "text": "Hook line.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "sentence_indices": [1], "source_segment_indices": [0, 1]},
        ],
        "subtitle_chunks": [],
    }

    segments = build_tts_segments(tts_script, script_segments)

    assert segments[1]["start_time"] == 0.0
    assert segments[1]["end_time"] == 2.5
    assert segments[1]["translated"] == "Closing line."
