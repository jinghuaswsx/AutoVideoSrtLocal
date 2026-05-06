"""TtsEngine registry + ElevenLabsEngine delegation tests (PR5)."""
from __future__ import annotations

import pytest

from appcore.tts_engines import (
    ElevenLabsEngine,
    TtsEngine,
    available_engines,
    get_engine,
    register_engine,
)


def test_elevenlabs_registered():
    codes = {e.code for e in available_engines()}
    assert "elevenlabs" in codes


def test_get_engine_returns_singleton():
    assert get_engine("elevenlabs") is get_engine("elevenlabs")
    assert isinstance(get_engine("elevenlabs"), ElevenLabsEngine)


def test_get_engine_unknown_raises():
    with pytest.raises(KeyError):
        get_engine("nope")


def test_register_duplicate_raises():
    class Dummy(TtsEngine):
        code = "elevenlabs"
        name = "dummy"

        def synthesize_full(self, *args, **kwargs):
            return {"full_audio_path": "", "segments": []}

        def regenerate_with_speed(self, *args, **kwargs):
            return {"full_audio_path": "", "segments": []}

        def get_audio_duration(self, audio_path):
            return 0.0

    with pytest.raises(ValueError):
        register_engine(Dummy())


def test_elevenlabs_supports_speed_param():
    engine = get_engine("elevenlabs")
    assert engine.supports_speed_param is True


def test_elevenlabs_synthesize_full_delegates_to_pipeline_tts(monkeypatch):
    """ElevenLabsEngine.synthesize_full 应 delegate 到 ``pipeline.tts.generate_full_audio``。"""
    captured = {}

    def fake_generate(segments, voice_id, output_dir, **kwargs):
        captured["segments"] = segments
        captured["voice_id"] = voice_id
        captured["output_dir"] = output_dir
        captured["kwargs"] = kwargs
        return {"full_audio_path": "/tmp/x.mp3", "segments": segments}

    monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_generate)

    engine = ElevenLabsEngine()
    result = engine.synthesize_full(
        [{"index": 0, "tts_text": "hi"}],
        "voice-x",
        "/tmp/task",
        variant="round_1",
        model_id="eleven_turbo_v2_5",
        language_code="de",
    )
    assert result["full_audio_path"] == "/tmp/x.mp3"
    assert captured["voice_id"] == "voice-x"
    assert captured["kwargs"]["variant"] == "round_1"
    assert captured["kwargs"]["model_id"] == "eleven_turbo_v2_5"
    assert captured["kwargs"]["language_code"] == "de"


def test_elevenlabs_regenerate_with_speed_delegates(monkeypatch):
    captured = {}

    def fake_regen(segments, voice_id, output_dir, **kwargs):
        captured["kwargs"] = kwargs
        return {"full_audio_path": "/tmp/sp.mp3", "segments": segments}

    monkeypatch.setattr("pipeline.tts.regenerate_full_audio_with_speed", fake_regen)

    engine = ElevenLabsEngine()
    engine.regenerate_with_speed(
        [{"index": 0, "tts_text": "hi"}],
        "voice-x",
        "/tmp/task",
        variant="round_2",
        speed=1.05,
        language_code="ja",
    )
    assert captured["kwargs"]["variant"] == "round_2"
    assert captured["kwargs"]["speed"] == 1.05
    assert captured["kwargs"]["language_code"] == "ja"


def test_elevenlabs_get_audio_duration_delegates(monkeypatch):
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 42.5)
    engine = ElevenLabsEngine()
    assert engine.get_audio_duration("/tmp/x.mp3") == 42.5


# === Profile.get_tts_engine wiring ===


def test_default_profile_uses_elevenlabs_engine():
    from appcore.translate_profiles import get_profile
    p = get_profile("default")
    engine = p.get_tts_engine()
    assert isinstance(engine, ElevenLabsEngine)


def test_omni_profile_uses_elevenlabs_engine():
    from appcore.translate_profiles import get_profile
    p = get_profile("omni")
    engine = p.get_tts_engine()
    assert isinstance(engine, ElevenLabsEngine)


def test_av_sync_profile_uses_elevenlabs_engine():
    from appcore.translate_profiles import get_profile
    p = get_profile("av_sync")
    engine = p.get_tts_engine()
    assert isinstance(engine, ElevenLabsEngine)


def test_profile_can_swap_engine_via_class_attr():
    """新 profile 想换 provider 只需覆盖 ``tts_engine_code`` 类属性。"""
    from appcore.translate_profiles.base import TranslateProfile

    class StubEngine(TtsEngine):
        code = "stub_for_swap_test"
        name = "Stub"
        supports_speed_param = False

        def synthesize_full(self, *a, **kw):
            return {"full_audio_path": "", "segments": []}

        def regenerate_with_speed(self, *a, **kw):
            raise NotImplementedError

        def get_audio_duration(self, p):
            return 0.0

    register_engine(StubEngine())

    class StubProfile(TranslateProfile):
        code = "stub_for_swap_test_profile"
        name = "stub"
        tts_engine_code = "stub_for_swap_test"

        def post_asr(self, runner, task_id): ...
        def translate(self, runner, task_id): ...
        def tts(self, runner, task_id, task_dir): ...
        def subtitle(self, runner, task_id, task_dir): ...

    p = StubProfile()
    engine = p.get_tts_engine()
    assert engine.code == "stub_for_swap_test"
    assert engine.supports_speed_param is False
