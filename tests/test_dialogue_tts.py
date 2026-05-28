from __future__ import annotations

from pathlib import Path

from appcore.tts_engines.elevenlabs import ElevenLabsEngine


def test_apply_speaker_voices_to_tts_segments_maps_by_dialogue_index():
    from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

    tts_segments = [
        {"tts_text": "hello"},
        {"tts_text": "world", "voice_id": "original"},
        {"tts_text": "again"},
    ]
    dialogue_segments = [
        {"source_index": 1, "speaker_id": "speaker_b"},
        {"index": 0, "speaker_id": "speaker_a"},
        {"segment_index": 2, "speaker_id": "speaker_a"},
    ]
    selected = {
        "speaker_a": {"voice_id": "voice-a", "voice_name": "Voice A"},
        "speaker_b": {"voice_id": "voice-b", "name": "Voice B"},
    }

    mapped = apply_speaker_voices_to_tts_segments(tts_segments, dialogue_segments, selected)

    assert mapped == [
        {"tts_text": "hello", "speaker_id": "speaker_a", "voice_id": "voice-a", "voice_name": "Voice A"},
        {"tts_text": "world", "voice_id": "voice-b", "speaker_id": "speaker_b", "voice_name": "Voice B"},
        {"tts_text": "again", "speaker_id": "speaker_a", "voice_id": "voice-a", "voice_name": "Voice A"},
    ]
    assert tts_segments[1]["voice_id"] == "original"


def test_apply_speaker_voices_to_out_of_order_tts_segments_uses_tts_index_fields():
    from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

    tts_segments = [
        {"tts_text": "third", "source_index": 2},
        {"tts_text": "first", "segment_index": 0},
        {"tts_text": "second", "source_index": 1},
    ]
    dialogue_segments = [
        {"index": 0, "speaker_id": "speaker_a"},
        {"index": 1, "speaker_id": "speaker_b"},
        {"index": 2, "speaker_id": "speaker_a"},
    ]
    selected = {
        "speaker_a": {"voice_id": "voice-a", "voice_name": "Voice A"},
        "speaker_b": {"voice_id": "voice-b", "voice_name": "Voice B"},
    }

    mapped = apply_speaker_voices_to_tts_segments(tts_segments, dialogue_segments, selected)

    assert [segment["speaker_id"] for segment in mapped] == ["speaker_a", "speaker_a", "speaker_b"]
    assert [segment["voice_id"] for segment in mapped] == ["voice-a", "voice-a", "voice-b"]
    assert [segment["tts_text"] for segment in mapped] == ["third", "first", "second"]


def test_generate_full_audio_with_segment_voices_uses_each_segment_voice(monkeypatch, tmp_path):
    from pipeline import tts

    calls: list[tuple[str, str, str]] = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        calls.append((text, voice_id, Path(output_path).name))
        Path(output_path).write_bytes(b"mp3")
        return output_path

    def fake_run(cmd, capture_output, text):
        Path(cmd[-1]).write_bytes(b"full")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tts, "generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr(tts, "_get_audio_duration", lambda path: 1.25 if path.endswith("0000.mp3") else 2.5)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    result = tts.generate_full_audio_with_segment_voices(
        [
            {"tts_text": "A", "voice_id": "voice-a"},
            {"tts_text": "B"},
            {"tts_text": "C", "voice_id": "voice-c"},
        ],
        default_voice_id="default-voice",
        output_dir=str(tmp_path),
        variant="dialogue",
    )

    calls_by_segment = sorted(calls, key=lambda call: call[2])
    assert [call[1] for call in calls_by_segment] == ["voice-a", "default-voice", "voice-c"]
    assert result["full_audio_path"] == str(tmp_path / "tts_full.dialogue.mp3")
    assert [seg["tts_duration"] for seg in result["segments"]] == [1.25, 2.5, 2.5]
    assert [Path(seg["tts_path"]).name for seg in result["segments"]] == [
        "seg_0000.mp3",
        "seg_0001.mp3",
        "seg_0002.mp3",
    ]


def test_elevenlabs_engine_uses_segment_voice_helper_when_voice_differs(monkeypatch, tmp_path):
    calls = []

    def fake_segment_voice_generate(segments, default_voice_id, output_dir, **kwargs):
        calls.append(("segment", segments, default_voice_id, output_dir, kwargs))
        return {"full_audio_path": str(tmp_path / "segment.mp3"), "segments": segments}

    def fake_single_voice_generate(segments, voice_id, output_dir, **kwargs):
        calls.append(("single", segments, voice_id, output_dir, kwargs))
        return {"full_audio_path": str(tmp_path / "single.mp3"), "segments": segments}

    monkeypatch.setattr("pipeline.tts.generate_full_audio_with_segment_voices", fake_segment_voice_generate)
    monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_single_voice_generate)

    segments = [
        {"tts_text": "A", "voice_id": "voice-default"},
        {"tts_text": "B", "voice_id": "voice-other"},
    ]

    result = ElevenLabsEngine().synthesize_full(
        segments,
        "voice-default",
        str(tmp_path),
        variant="dialogue",
        model_id="model-1",
        language_code="en",
    )

    assert result["full_audio_path"].endswith("segment.mp3")
    assert calls == [
        (
            "segment",
            segments,
            "voice-default",
            str(tmp_path),
            {"variant": "dialogue", "model_id": "model-1", "language_code": "en"},
        )
    ]
