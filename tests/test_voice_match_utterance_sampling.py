import pytest

from pipeline.voice_match import pick_utterance_window


def test_pick_utterance_window_single_long_is_enough():
    utts = [
        {"start_time": 0.0, "end_time": 3.0, "text": "hi"},
        {"start_time": 3.5, "end_time": 15.0, "text": "long single"},
        {"start_time": 16.0, "end_time": 17.0, "text": "short"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    assert start == pytest.approx(3.5)
    assert end == pytest.approx(15.0)


def test_pick_utterance_window_needs_stitching():
    utts = [
        {"start_time": 0.0, "end_time": 2.0, "text": "a"},
        {"start_time": 2.1, "end_time": 4.0, "text": "b"},
        {"start_time": 4.2, "end_time": 6.5, "text": "c"},
        {"start_time": 6.6, "end_time": 9.0, "text": "d"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    assert end - start >= 8.0


def test_pick_utterance_window_fallback_when_total_too_short():
    utts = [
        {"start_time": 0.0, "end_time": 1.0, "text": "a"},
        {"start_time": 1.5, "end_time": 3.0, "text": "b"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    assert start == pytest.approx(0.0)
    assert end == pytest.approx(3.0)


def test_pick_utterance_window_empty_raises():
    with pytest.raises(ValueError):
        pick_utterance_window([], min_duration=8.0)
