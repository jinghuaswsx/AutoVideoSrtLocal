from pipeline.alignment import build_script_segments, suggest_break_after


def test_suggest_break_after_combines_punctuation_pause_and_scene_cuts():
    utterances = [
        {"text": "第一句。", "start_time": 0.0, "end_time": 1.0},
        {"text": "第二句继续", "start_time": 1.8, "end_time": 2.8},
        {"text": "第三句", "start_time": 2.9, "end_time": 3.7},
        {"text": "第四句", "start_time": 5.2, "end_time": 6.0},
    ]

    suggested = suggest_break_after(
        utterances,
        scene_cuts=[0.95, 3.75],
        min_pause_seconds=1.0,
        scene_tolerance_seconds=0.2,
    )

    assert suggested == [True, False, True, True]


def test_build_script_segments_merges_utterances_by_break_boundaries():
    utterances = [
        {"text": "你好", "start_time": 0.0, "end_time": 0.8, "words": [{"text": "你"}]},
        {"text": "世界", "start_time": 0.8, "end_time": 1.6, "words": [{"text": "世"}]},
        {"text": "再见", "start_time": 2.0, "end_time": 2.7, "words": [{"text": "再"}]},
    ]

    segments = build_script_segments(utterances, [False, True, True])

    assert segments == [
        {
            "index": 0,
            "text": "你好世界",
            "start_time": 0.0,
            "end_time": 1.6,
            "utterance_indices": [0, 1],
            "words": [{"text": "你"}, {"text": "世"}],
        },
        {
            "index": 1,
            "text": "再见",
            "start_time": 2.0,
            "end_time": 2.7,
            "utterance_indices": [2],
            "words": [{"text": "再"}],
        },
    ]
