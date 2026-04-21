import pytest

from pipeline.localization import build_tts_segments


def test_build_tts_segments_projects_block_ranges_back_to_source_segments():
    script_segments = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "part two", "start_time": 1.0, "end_time": 2.5},
    ]
    tts_script = {
        "full_text": "Hook line. Closing line.",
        "blocks": [
            {"index": 0, "text": "Hook line.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "sentence_indices": [1], "source_segment_indices": [0, 1]},
        ],
        "subtitle_chunks": [],
    }

    segments = build_tts_segments(tts_script, script_segments)

    assert segments[1]["start_time"] == 0.0
    assert segments[1]["end_time"] == 2.5
    assert segments[1]["translated"] == "Closing line."


def test_build_tts_segments_drops_out_of_range_source_indices():
    """rewrite 偶尔会给新增的句子配越界 source_segment_indices（比如 ASR 8 段，
    却给 [7, 8]）。修复前 build_tts_segments 直接 KeyError: 8，前端就弹"错误：8"。
    修复后应过滤越界索引，留合法的那些。"""
    script_segments = [
        {"index": 0, "text": "a", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "b", "start_time": 1.0, "end_time": 2.0},
    ]
    tts_script = {
        "full_text": "solo.",
        "blocks": [
            {"index": 0, "text": "solo", "sentence_indices": [0], "source_segment_indices": [1, 5]},
        ],
        "subtitle_chunks": [],
    }

    segments = build_tts_segments(tts_script, script_segments)

    assert len(segments) == 1
    assert segments[0]["source_segment_indices"] == [1]
    assert segments[0]["start_time"] == 1.0
    assert segments[0]["end_time"] == 2.0


def test_build_tts_segments_falls_back_to_last_segment_when_all_indices_invalid():
    """极端情况：block 的 source_segment_indices 全部越界，退回最后一段作兜底，
    绝不抛 KeyError 打断整条流水线。"""
    script_segments = [
        {"index": 0, "text": "a", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "b", "start_time": 1.0, "end_time": 2.0},
    ]
    tts_script = {
        "full_text": "x.",
        "blocks": [
            {"index": 0, "text": "x", "sentence_indices": [0], "source_segment_indices": [8, 9]},
        ],
        "subtitle_chunks": [],
    }

    segments = build_tts_segments(tts_script, script_segments)

    assert len(segments) == 1
    assert segments[0]["source_segment_indices"] == [1]
    assert segments[0]["end_time"] == 2.0


def test_generate_segment_audio_passes_speed_via_voice_settings(tmp_path, monkeypatch):
    import pipeline.tts as tts

    captured = {}

    class DummyVoiceSettings:
        def __init__(self, **kwargs):
            captured["voice_settings_kwargs"] = kwargs

    class FakeTextToSpeech:
        @staticmethod
        def convert(**kwargs):
            captured["convert_kwargs"] = kwargs
            return [b"audio-bytes"]

    class FakeClient:
        text_to_speech = FakeTextToSpeech()

    monkeypatch.setattr(tts, "_get_client", lambda api_key=None: FakeClient())
    monkeypatch.setattr(tts, "VoiceSettings", DummyVoiceSettings, raising=False)

    out = tmp_path / "seg.mp3"
    path = tts.generate_segment_audio(
        text="hello world",
        voice_id="voice-1",
        output_path=str(out),
        speed=1.05,
    )

    assert path == str(out)
    assert captured["voice_settings_kwargs"]["speed"] == pytest.approx(1.05)
    assert captured["convert_kwargs"]["voice_settings"].__class__ is DummyVoiceSettings
