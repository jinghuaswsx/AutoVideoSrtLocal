from __future__ import annotations

from pipeline import ja_translate


def test_count_visible_japanese_chars_ignores_spacing():
    assert ja_translate.count_visible_japanese_chars("帽子 収納に\n便利です。") == 10


def test_compute_ja_char_range_uses_measured_cps(monkeypatch):
    monkeypatch.setattr(ja_translate.speech_rate_model, "get_rate", lambda voice_id, language: 6.0)

    assert ja_translate.compute_ja_char_range(2.0, "voice-ja") == (11, 13)


def test_build_sentence_inputs_preserves_each_source_segment(monkeypatch):
    monkeypatch.setattr(ja_translate.speech_rate_model, "get_rate", lambda voice_id, language: 5.0)

    segments = [
        {"index": 0, "start_time": 0.0, "end_time": 2.0, "text": "Hats everywhere?"},
        {"index": 1, "start_time": 2.0, "end_time": 5.0, "text": "Stack and protect every cap."},
    ]

    result = ja_translate.build_sentence_inputs(segments, voice_id="voice-ja")

    assert [item["asr_index"] for item in result] == [0, 1]
    assert result[0]["target_chars_range"] == (9, 11)
    assert result[1]["target_chars_range"] == (13, 16)
    assert result[0]["source_text"] == "Hats everywhere?"
    assert result[1]["source_text"] == "Stack and protect every cap."


def test_build_ja_tts_script_chunks_do_not_start_with_particles():
    localized = {
        "sentences": [
            {
                "index": 0,
                "text": "帽子をまとめて収納できて、ほこりから守れます。",
                "source_segment_indices": [0],
            }
        ]
    }

    script = ja_translate.build_ja_tts_script(localized)

    assert script["full_text"] == "帽子をまとめて収納できて、ほこりから守れます。"
    assert script["blocks"][0]["text"] == "帽子をまとめて収納できて、ほこりから守れます。"
    assert script["subtitle_chunks"]
    assert all(
        not chunk["text"].startswith(("は", "が", "を", "に", "で", "と", "の", "も"))
        for chunk in script["subtitle_chunks"]
    )


def test_build_rewrite_sentence_inputs_distributes_total_chars():
    localized = {
        "sentences": [
            {"asr_index": 0, "text": "帽子収納に便利です。", "source_segment_indices": [0]},
            {"asr_index": 1, "text": "重ねられてほこりも防げます。", "source_segment_indices": [1]},
        ]
    }
    segments = [
        {"index": 0, "start_time": 0.0, "end_time": 2.0, "text": "Great for hats."},
        {"index": 1, "start_time": 2.0, "end_time": 5.0, "text": "Stackable and dust proof."},
    ]

    result = ja_translate.build_rewrite_sentence_inputs(
        localized,
        segments,
        target_total_chars=18,
    )

    assert [item["asr_index"] for item in result] == [0, 1]
    assert sum(item["target_chars"] for item in result) == 18
    assert result[0]["target_chars_range"] == (7, 9)
    assert result[1]["target_chars_range"] == (9, 11)


def test_split_ja_subtitle_chunks_respects_length_and_particle_boundary():
    chunks = ja_translate.split_ja_subtitle_chunks(
        "帽子をまとめて収納できてほこりから守れて省スペースです。",
        max_chars=12,
    )

    assert chunks
    assert all(ja_translate.count_visible_japanese_chars(chunk) <= 14 for chunk in chunks)
    assert all(
        not chunk.startswith(("は", "が", "を", "に", "で", "と", "の", "も"))
        for chunk in chunks
    )
