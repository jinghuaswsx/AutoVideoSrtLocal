from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from appcore.tts_engines.elevenlabs import ElevenLabsEngine


def _use_test_tts_pool(monkeypatch, tts, max_workers: int = 4) -> ThreadPoolExecutor:
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="test-tts")
    monkeypatch.setattr(tts, "_get_tts_pool", lambda: pool)
    return pool


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


def test_apply_speaker_voices_accepts_elevenlabs_voice_id_and_preserves_existing_when_missing():
    from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

    tts_segments = [
        {"tts_text": "first", "voice_id": "old-a"},
        {"tts_text": "second", "voice_id": "old-b"},
    ]
    dialogue_segments = [
        {"index": 0, "speaker_id": "speaker_a"},
        {"index": 1, "speaker_id": "speaker_b"},
    ]
    selected = {
        "speaker_a": {"elevenlabs_voice_id": "eleven-a", "voice_name": "Eleven A"},
        "speaker_b": {"voice_name": "Missing Voice"},
    }

    mapped = apply_speaker_voices_to_tts_segments(tts_segments, dialogue_segments, selected)

    assert mapped[0]["voice_id"] == "eleven-a"
    assert mapped[0]["voice_name"] == "Eleven A"
    assert mapped[1]["voice_id"] == "old-b"
    assert mapped[1]["voice_name"] == "Missing Voice"


def test_apply_speaker_voices_treats_malformed_selected_voice_mapping_as_empty():
    from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

    mapped = apply_speaker_voices_to_tts_segments(
        [{"tts_text": "first", "voice_id": "existing"}],
        [{"index": 0, "speaker_id": "speaker_a"}],
        None,
    )

    assert mapped == [{"tts_text": "first", "voice_id": "existing", "speaker_id": "speaker_a"}]


def test_sentence_reconcile_initial_state_carries_dialogue_voice_metadata():
    from pipeline import duration_reconcile, duration_reconcile_v2

    for module in (duration_reconcile, duration_reconcile_v2):
        current = module._initial_sentence_state(
            position=0,
            av_sentence={
                "asr_index": 7,
                "text": "Hallo",
                "start_time": 1.0,
                "end_time": 2.0,
                "target_duration": 1.0,
            },
            tts_by_index={
                7: {
                    "tts_path": "speaker-a.mp3",
                    "tts_duration": 0.9,
                    "speaker_id": "A",
                    "voice_id": "voice-a",
                    "voice_name": "Voice A",
                }
            },
            max_rewrite_rounds=3,
            max_tts_regenerate_attempts=3,
        )

        assert current["speaker_id"] == "A"
        assert current["voice_id"] == "voice-a"
        assert current["voice_name"] == "Voice A"


def test_av_tts_segments_keep_dialogue_speaker_voice_metadata():
    from appcore.runtime._av_helpers import _build_av_tts_segments

    segments = _build_av_tts_segments(
        [
            {
                "asr_index": 3,
                "text": "Hallo",
                "speaker_id": "B",
                "voice_id": "voice-b",
                "voice_name": "Voice B",
            }
        ]
    )

    assert segments[0]["speaker_id"] == "B"
    assert segments[0]["voice_id"] == "voice-b"
    assert segments[0]["voice_name"] == "Voice B"


def test_sentence_reconcile_strategies_apply_dialogue_segment_voice_hook():
    for path in (
        Path("appcore/tts_strategies/sentence_reconcile.py"),
        Path("appcore/tts_strategies/sentence_reconcile_v2.py"),
    ):
        source = path.read_text(encoding="utf-8")
        hook_pos = source.index("runner._prepare_tts_segments_for_audio_gen")
        synth_pos = source.index("tts_engine.synthesize_full")

        assert hook_pos < synth_pos


def test_generate_full_audio_with_segment_voices_uses_each_segment_voice(monkeypatch, tmp_path):
    from pipeline import tts

    pool = _use_test_tts_pool(monkeypatch, tts)
    calls: list[tuple[str, str, str]] = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        calls.append((text, voice_id, Path(output_path).name))
        Path(output_path).write_bytes(b"mp3")
        return output_path

    def fake_run(cmd, capture_output, text):
        Path(cmd[-1]).write_bytes(b"full")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tts, "generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr(tts, "_get_audio_duration", lambda path: 1.25 if "seg_0000." in path else 2.5)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    try:
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
    finally:
        pool.shutdown(wait=True)

    calls_by_segment = sorted(calls, key=lambda call: call[2])
    assert [call[1] for call in calls_by_segment] == ["voice-a", "default-voice", "voice-c"]
    assert result["full_audio_path"] == str(tmp_path / "tts_full.dialogue.mp3")
    assert [seg["tts_duration"] for seg in result["segments"]] == [1.25, 2.5, 2.5]
    assert [Path(seg["tts_path"]).name for seg in result["segments"]] == [
        Path(calls_by_segment[0][2]).name,
        Path(calls_by_segment[1][2]).name,
        Path(calls_by_segment[2][2]).name,
    ]


def test_generate_full_audio_with_segment_voices_uses_distinct_paths_for_distinct_voice_settings(
    monkeypatch,
    tmp_path,
):
    from pipeline import tts

    pool = _use_test_tts_pool(monkeypatch, tts)
    output_paths: list[str] = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        output_paths.append(output_path)
        Path(output_path).write_bytes(b"mp3")
        return output_path

    def fake_run(cmd, capture_output, text):
        Path(cmd[-1]).write_bytes(b"full")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(tts, "generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr(tts, "_get_audio_duration", lambda path: 1.0)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    try:
        tts.generate_full_audio_with_segment_voices(
            [{"tts_text": "same text", "voice_id": "voice-a"}],
            default_voice_id="default",
            output_dir=str(tmp_path),
            variant="dialogue",
        )
        tts.generate_full_audio_with_segment_voices(
            [{"tts_text": "same text", "voice_id": "voice-b"}],
            default_voice_id="default",
            output_dir=str(tmp_path),
            variant="dialogue",
        )
        tts.regenerate_full_audio_with_segment_voices_speed(
            [{"tts_text": "same text", "voice_id": "voice-b"}],
            default_voice_id="default",
            output_dir=str(tmp_path),
            variant="dialogue",
            speed=1.1,
        )
    finally:
        pool.shutdown(wait=True)

    assert len(output_paths) == 3
    assert len(set(output_paths)) == 3
    assert all("seg_0000." in Path(path).name for path in output_paths)


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


def test_elevenlabs_engine_regenerate_with_speed_uses_segment_voice_helper_when_voice_differs(monkeypatch, tmp_path):
    calls = []

    def fake_segment_voice_regenerate(segments, default_voice_id, output_dir, **kwargs):
        calls.append(("segment", segments, default_voice_id, output_dir, kwargs))
        return {"full_audio_path": str(tmp_path / "segment-speed.mp3"), "segments": segments}

    def fake_single_voice_regenerate(segments, voice_id, output_dir, **kwargs):
        calls.append(("single", segments, voice_id, output_dir, kwargs))
        return {"full_audio_path": str(tmp_path / "single-speed.mp3"), "segments": segments}

    monkeypatch.setattr(
        "pipeline.tts.regenerate_full_audio_with_segment_voices_speed",
        fake_segment_voice_regenerate,
    )
    monkeypatch.setattr("pipeline.tts.regenerate_full_audio_with_speed", fake_single_voice_regenerate)

    segments = [
        {"tts_text": "A", "voice_id": "voice-default"},
        {"tts_text": "B", "voice_id": "voice-other"},
    ]

    result = ElevenLabsEngine().regenerate_with_speed(
        segments,
        "voice-default",
        str(tmp_path),
        variant="round_2",
        speed=1.1,
        stability=0.4,
        similarity_boost=0.7,
        model_id="model-1",
        language_code="en",
    )

    assert result["full_audio_path"].endswith("segment-speed.mp3")
    assert calls == [
        (
            "segment",
            segments,
            "voice-default",
            str(tmp_path),
            {
                "variant": "round_2",
                "speed": 1.1,
                "stability": 0.4,
                "similarity_boost": 0.7,
                "model_id": "model-1",
                "language_code": "en",
            },
        )
    ]
