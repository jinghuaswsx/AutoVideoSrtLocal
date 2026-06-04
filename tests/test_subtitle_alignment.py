from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr


def test_align_subtitle_chunks_to_asr_preserves_target_text_and_uses_word_timing():
    subtitle_chunks = [
        {"index": 0, "text": "Say it smooth."},
        {"index": 1, "text": "Keep it fun."},
    ]
    asr_result = {
        "utterances": [
            {
                "text": "say it smooth keep it fun",
                "start_time": 0.0,
                "end_time": 2.0,
                "words": [
                    {"text": "say", "start_time": 0.0, "end_time": 0.2},
                    {"text": "it", "start_time": 0.2, "end_time": 0.35},
                    {"text": "smooth", "start_time": 0.35, "end_time": 0.8},
                    {"text": "keep", "start_time": 1.0, "end_time": 1.2},
                    {"text": "it", "start_time": 1.2, "end_time": 1.35},
                    {"text": "fun", "start_time": 1.35, "end_time": 1.8},
                ],
            }
        ]
    }

    aligned = align_subtitle_chunks_to_asr(subtitle_chunks, asr_result, total_duration=2.0)

    assert aligned[0]["text"] == "Say it smooth."
    assert aligned[0]["start_time"] == 0.0
    assert aligned[1]["end_time"] == 1.8


def test_align_subtitle_chunks_to_asr_does_not_exhaust_cursor_on_missing_word():
    subtitle_chunks = [
        {"index": 0, "text": "Saviez-vous que accidentel"},
        {"index": 1, "text": "En vieillissant"},
    ]
    asr_result = {
        "utterances": [
            {
                "words": [
                    {"text": "Saviez-vous", "start_time": 34.92, "end_time": 35.42},
                    {"text": "que", "start_time": 35.44, "end_time": 35.52},
                    {"text": "en", "start_time": 39.42, "end_time": 39.52},
                    {"text": "vieillissant", "start_time": 39.54, "end_time": 40.18},
                ],
            }
        ]
    }

    aligned = align_subtitle_chunks_to_asr(subtitle_chunks, asr_result, total_duration=42.0)

    assert aligned[0]["source_asr_text"] == "Saviez-vous que"
    assert aligned[1]["source_asr_text"] == "en vieillissant"
    assert aligned[1]["start_time"] == 39.42
    assert aligned[1]["end_time"] == 40.18


def test_align_subtitle_chunks_to_asr_keeps_fallback_timing_monotonic():
    subtitle_chunks = [
        {"index": 0, "text": "Known start"},
        {"index": 1, "text": "missing subtitle text"},
        {"index": 2, "text": "later words"},
    ]
    asr_result = {
        "utterances": [
            {
                "words": [
                    {"text": "known", "start_time": 8.0, "end_time": 8.4},
                    {"text": "start", "start_time": 8.45, "end_time": 8.9},
                    {"text": "later", "start_time": 12.0, "end_time": 12.3},
                    {"text": "words", "start_time": 12.35, "end_time": 12.8},
                ],
            }
        ]
    }

    aligned = align_subtitle_chunks_to_asr(subtitle_chunks, asr_result, total_duration=15.0)

    assert aligned[1]["start_time"] >= aligned[0]["end_time"]
    assert aligned[1]["end_time"] <= aligned[2]["start_time"]
    assert aligned[2]["source_asr_text"] == "later words"
