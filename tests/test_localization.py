import types

from pipeline.localization import (
    build_source_full_text_zh,
    validate_localized_translation,
    validate_tts_script,
)
from pipeline.translate import generate_localized_translation, generate_tts_script


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


def test_generate_localized_translation_parses_structured_output(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content='{"full_text":"Hook line. Closing line.","sentences":[{"index":0,"text":"Hook line.","source_segment_indices":[0]},{"index":1,"text":"Closing line.","source_segment_indices":[1]}]}'
                    )
                )
            ]
        )

    monkeypatch.setattr("pipeline.translate.client.chat.completions.create", fake_create)

    payload = generate_localized_translation(
        source_full_text_zh="part one\npart two",
        script_segments=[
            {"index": 0, "text": "part one"},
            {"index": 1, "text": "part two"},
        ],
    )

    assert payload["sentences"][0]["text"] == "Hook line."
    assert captured["model"] == "anthropic/claude-sonnet-4.5"


def test_generate_tts_script_returns_validated_blocks(monkeypatch):
    def fake_create(**kwargs):
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content='{"full_text":"Say it smooth. Keep it fun.","blocks":[{"index":0,"text":"Say it smooth.","sentence_indices":[0],"source_segment_indices":[0]},{"index":1,"text":"Keep it fun.","sentence_indices":[1],"source_segment_indices":[1]}],"subtitle_chunks":[{"index":0,"text":"Say it smooth.","block_indices":[0],"sentence_indices":[0],"source_segment_indices":[0]},{"index":1,"text":"Keep it fun.","block_indices":[1],"sentence_indices":[1],"source_segment_indices":[1]}]}'
                    )
                )
            ]
        )

    monkeypatch.setattr("pipeline.translate.client.chat.completions.create", fake_create)

    payload = generate_tts_script(
        localized_translation={
            "full_text": "Say it smooth. Keep it fun.",
            "sentences": [
                {"index": 0, "text": "Say it smooth.", "source_segment_indices": [0]},
                {"index": 1, "text": "Keep it fun.", "source_segment_indices": [1]},
            ],
        }
    )

    assert payload["subtitle_chunks"][1]["text"] == "Keep it fun."
