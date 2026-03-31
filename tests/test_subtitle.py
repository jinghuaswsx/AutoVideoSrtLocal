from pipeline.subtitle import build_srt_from_chunks, build_srt_from_manifest


def test_build_srt_from_manifest_uses_manifest_timing():
    manifest = {
        "segments": [
            {
                "index": 0,
                "translated": "hello there",
                "timeline_start": 0.0,
                "timeline_end": 1.25,
            },
            {
                "index": 1,
                "translated": "general kenobi",
                "timeline_start": 1.25,
                "timeline_end": 2.75,
            },
        ]
    }

    srt = build_srt_from_manifest(manifest)

    assert "00:00:00,000 --> 00:00:01,250" in srt
    assert "00:00:01,250 --> 00:00:02,750" in srt
    assert "Hello there" in srt


def test_build_srt_from_chunks_uses_corrected_text_and_timing():
    srt = build_srt_from_chunks(
        [
            {"index": 0, "text": "Say it smooth.", "start_time": 0.0, "end_time": 0.8},
            {"index": 1, "text": "Keep it fun.", "start_time": 0.8, "end_time": 1.8},
        ]
    )

    assert "00:00:00,000 --> 00:00:00,800" in srt
    assert "Say it smooth" in srt
    assert "Keep it fun" in srt
    assert "Say it smooth." not in srt


def test_build_srt_from_chunks_capitalizes_lowercase_chunk_text():
    srt = build_srt_from_chunks(
        [
            {"index": 0, "text": "say it smooth.", "start_time": 0.0, "end_time": 0.8},
        ]
    )

    assert "Say it smooth" in srt
    assert "say it smooth" not in srt


def test_build_srt_from_chunks_removes_terminal_punctuation_and_balances_two_lines():
    srt = build_srt_from_chunks(
        [
            {
                "index": 0,
                "text": "Follow the instructions step by step to build it.",
                "start_time": 0.0,
                "end_time": 2.4,
            }
        ]
    )

    lines = srt.splitlines()
    subtitle_lines = [line for line in lines[2:] if line]

    assert len(subtitle_lines) == 2
    assert all(not line.endswith((".", ",", "!", "?", ";", ":")) for line in subtitle_lines)
    counts = [len(line.split()) for line in subtitle_lines]
    assert sum(counts) == 9
    assert max(counts) - min(counts) <= 1
