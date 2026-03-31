from pipeline.localization import (
    build_source_full_text_zh,
    validate_localized_translation,
    validate_tts_script,
)


def test_build_source_full_text_zh_joins_confirmed_segments_with_newlines():
    segments = [
        {"index": 0, "text": "part one"},
        {"index": 1, "text": "part two"},
    ]

    assert build_source_full_text_zh(segments) == "part one\npart two"


def test_validate_localized_translation_requires_full_text_and_source_segment_indices():
    payload = {
        "full_text": "Hook line. Closing line.",
        "sentences": [
            {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
        ],
    }

    validated = validate_localized_translation(payload)

    assert validated["full_text"] == "Hook line. Closing line."
    assert validated["sentences"][1]["source_segment_indices"] == [1]


def test_validate_tts_script_rejects_chunk_text_that_does_not_match_full_text():
    payload = {
        "full_text": "Say it smooth. Keep it fun.",
        "blocks": [
            {"index": 0, "text": "Say it smooth.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Keep it fun.", "sentence_indices": [1], "source_segment_indices": [1]},
        ],
        "subtitle_chunks": [
            {"index": 0, "text": "Say it smooth.", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Keep this different.", "block_indices": [1], "sentence_indices": [1], "source_segment_indices": [1]},
        ],
    }

    try:
        validate_tts_script(payload)
    except ValueError as exc:
        assert "subtitle_chunks" in str(exc)
    else:
        raise AssertionError("expected ValueError")
