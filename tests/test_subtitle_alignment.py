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
