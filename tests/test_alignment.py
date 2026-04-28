from pipeline.alignment import build_script_segments, suggest_break_after


def test_suggest_break_after_combines_punctuation_pause_and_scene_cuts():
    utterances = [
        {"text": "First sentence.", "start_time": 0.0, "end_time": 1.0},
        {"text": "Second keeps going", "start_time": 1.8, "end_time": 2.8},
        {"text": "third", "start_time": 2.9, "end_time": 3.7},
        {"text": "fourth", "start_time": 5.2, "end_time": 6.0},
    ]

    suggested = suggest_break_after(
        utterances,
        scene_cuts=[0.95, 3.75],
        min_pause_seconds=1.0,
        scene_tolerance_seconds=0.2,
    )

    assert suggested == [True, False, True, True]


def test_suggest_break_after_respects_forced_asr_boundaries():
    utterances = [
        {"text": "long english source part one", "start_time": 3.0, "end_time": 9.1, "force_break_after": True},
        {"text": "long english source part two", "start_time": 9.2, "end_time": 14.8, "force_break_after": True},
        {"text": "ending", "start_time": 14.9, "end_time": 18.0},
    ]

    assert suggest_break_after(utterances, min_pause_seconds=1.0) == [True, True, True]


def test_build_script_segments_merges_utterances_by_break_boundaries():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 0.8, "words": [{"text": "hello"}]},
        {"text": "world", "start_time": 0.8, "end_time": 1.6, "words": [{"text": "world"}]},
        {"text": "bye", "start_time": 2.0, "end_time": 2.7, "words": [{"text": "bye"}]},
    ]

    segments = build_script_segments(utterances, [False, True, True])

    assert segments == [
        {
            "index": 0,
            "text": "hello world",
            "start_time": 0.0,
            "end_time": 1.6,
            "utterance_indices": [0, 1],
            "words": [{"text": "hello"}, {"text": "world"}],
        },
        {
            "index": 1,
            "text": "bye",
            "start_time": 2.0,
            "end_time": 2.7,
            "utterance_indices": [2],
            "words": [{"text": "bye"}],
        },
    ]
