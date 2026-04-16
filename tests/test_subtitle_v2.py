from pipeline.subtitle_v2 import (
    split_into_blocks, compute_unified_font_size, generate_srt,
)


def test_split_keeps_short_text_as_single_line():
    blocks = split_into_blocks("Short.", max_chars_per_line=40)
    assert blocks == [["Short."]]


def test_split_wraps_medium_text_into_two_lines():
    text = "She stepped inside the cafe, looking around for her friend."
    blocks = split_into_blocks(text, max_chars_per_line=30)
    assert len(blocks) == 1
    assert len(blocks[0]) == 2
    assert all(len(line) <= 30 for line in blocks[0])


def test_split_creates_second_block_when_more_than_two_lines_needed():
    long = (
        "Part one goes here. Part two goes here. Part three goes here. "
        "Part four goes here. Part five goes here."
    )
    blocks = split_into_blocks(long, max_chars_per_line=20)
    assert len(blocks) >= 2
    for block in blocks:
        assert len(block) <= 2
        for line in block:
            assert len(line) <= 20


def test_split_handles_empty_text():
    assert split_into_blocks("", max_chars_per_line=30) == []
    assert split_into_blocks("   ", max_chars_per_line=30) == []


def test_compute_unified_font_size_fits_worst_case():
    shots = [
        {"index": 1, "final_text": "Short."},
        {"index": 2, "final_text": "A medium length caption here."},
        {"index": 3,
         "final_text": "This is the longest caption in the whole video that we have."},
    ]
    size = compute_unified_font_size(
        shots, video_width=1920, video_height=1080,
        min_size=16, max_size=42,
    )
    assert 16 <= size <= 42


def test_compute_unified_font_size_returns_max_when_all_short():
    shots = [{"index": 1, "final_text": "Hi."}]
    size = compute_unified_font_size(
        shots, video_width=1920, video_height=1080,
        min_size=16, max_size=42,
    )
    assert size == 42


def test_compute_unified_font_size_returns_min_when_extremely_long():
    long_text = "A" * 500
    shots = [{"index": 1, "final_text": long_text}]
    size = compute_unified_font_size(
        shots, video_width=640, video_height=360,
        min_size=16, max_size=42,
    )
    assert size == 16


def test_generate_srt_outputs_correct_timestamps_and_text():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0,
         "final_text": "Hello world.", "final_duration": 4.5},
        {"index": 2, "start": 5.0, "end": 10.0,
         "final_text": "Second caption.", "final_duration": 4.8},
    ]
    srt = generate_srt(shots, font_size=28, max_chars_per_line=40)
    assert "1\n00:00:00,000 --> 00:00:04,500" in srt
    assert "Hello world." in srt
    assert "2\n00:00:05,000 --> 00:00:09,800" in srt
    assert "Second caption." in srt


def test_generate_srt_skips_empty_final_text():
    shots = [
        {"index": 1, "start": 0.0, "end": 5.0,
         "final_text": "", "final_duration": 0.0},
        {"index": 2, "start": 5.0, "end": 10.0,
         "final_text": "Only this.", "final_duration": 3.0},
    ]
    srt = generate_srt(shots, font_size=28, max_chars_per_line=40)
    assert "Only this." in srt
    # 只应有 1 条字幕条目
    assert srt.count("-->") == 1
