import math
from pathlib import Path

import pytest

from appcore import audio_loudness
from appcore.audio_loudness import (
    BOOST_MAX_BACKGROUND_VOLUME,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_CLEAN_BACKGROUND,
    LOUDNESS_PROFILE_MANUAL_BOOST,
    LOUDNESS_PROFILE_STANDARD,
    LOUDNESS_PROFILE_VOICE_ONLY,
    VOICE_PRIORITY_TARGET_GAP_LU,
    measure_voice_priority_background_windows,
    resolve_voice_priority_background_volume,
    resolve_background_volume_profile,
    validate_loudness_profile,
)


def test_standard_profile_uses_current_background_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_STANDARD,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_STANDARD
    assert result["manual_boost_pct"] is None
    assert result["background_volume"] == 0.8
    assert result["effective_background_volume"] == 0.8
    assert result["background_boost"]["enabled"] is False
    assert result["manual_boost"]["enabled"] is False


def test_voice_priority_suppresses_background_when_sentence_window_masks_voice():
    result = resolve_voice_priority_background_volume(
        background_volume=1.0,
        window_loudness=[
            {"index": 46, "voice_lufs": -16.4, "background_lufs": -16.4},
            {"index": 47, "voice_lufs": -17.0, "background_lufs": -13.1},
        ],
    )

    assert result["enabled"] is True
    assert result["target_gap_lu"] == VOICE_PRIORITY_TARGET_GAP_LU
    assert result["risky_window_count"] == 2
    assert result["max_background_minus_voice_lu"] == 3.9
    assert result["required_attenuation_lu"] == -15.9
    assert math.isclose(result["effective_volume"], 10 ** (-15.9 / 20), rel_tol=1e-6)
    assert result["effective_volume"] < 0.17
    assert result["dominant_windows"][0]["index"] == 47


def test_voice_priority_keeps_background_when_already_below_target_gap():
    result = resolve_voice_priority_background_volume(
        background_volume=0.6,
        window_loudness=[
            {"index": 1, "voice_lufs": -16.0, "background_lufs": -33.0},
            {"index": 2, "voice_lufs": -15.0, "background_lufs": -31.5},
        ],
    )

    assert result["enabled"] is False
    assert result["fallback_reason"] == "already_below_target_gap"
    assert result["effective_volume"] == 0.6
    assert result["risky_window_count"] == 0


def test_voice_priority_window_measurement_prefers_segment_tts_path(
    monkeypatch, tmp_path,
):
    full_tts = tmp_path / "tts_full.mp3"
    segment_tts = tmp_path / "segment_26.mp3"
    background = tmp_path / "background.wav"
    for path in (full_tts, segment_tts, background):
        path.write_text("audio", encoding="utf-8")
    calls = []

    def fake_measure(path, start_time, end_time):
        calls.append((Path(path).name, start_time, end_time))
        return -16.0 if Path(path) == segment_tts else -24.0

    monkeypatch.setattr(audio_loudness, "measure_window_lufs", fake_measure)

    records = measure_voice_priority_background_windows(
        tts_audio_path=str(full_tts),
        background_path=str(background),
        segments=[{
            "index": 26,
            "audio_start_time": 155.72,
            "audio_end_time": 158.72,
            "tts_path": str(segment_tts),
            "tts_duration": 2.4,
        }],
    )

    assert records == [{
        "index": 26,
        "start": 155.72,
        "end": 158.72,
        "voice_lufs": -16.0,
        "background_lufs": -24.0,
    }]
    assert calls == [
        ("segment_26.mp3", 0.0, 2.4),
        ("background.wav", 155.72, 158.72),
    ]


def test_voice_only_profile_suppresses_background_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_VOICE_ONLY,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_VOICE_ONLY
    assert result["manual_boost_pct"] is None
    assert result["background_volume"] == 0.8
    assert result["effective_background_volume"] == 0.0
    assert result["background_suppression"]["enabled"] is True
    assert result["background_boost"]["enabled"] is False
    assert result["manual_boost"]["enabled"] is False


def test_clean_background_profile_keeps_background_volume_and_enables_cleanup():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_CLEAN_BACKGROUND,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_CLEAN_BACKGROUND
    assert result["manual_boost_pct"] is None
    assert result["background_volume"] == 0.8
    assert result["effective_background_volume"] == 0.8
    assert result["background_cleanup"]["enabled"] is True
    assert result["background_cleanup"]["mode"] == "de_electric"
    assert result["background_suppression"]["enabled"] is False
    assert result["background_boost"]["enabled"] is False
    assert result["manual_boost"]["enabled"] is False


def test_auto_boost_raises_background_toward_target_gap_and_caps():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_AUTO_BOOST
    assert result["manual_boost_pct"] is None
    assert result["background_boost"]["enabled"] is True
    assert result["background_boost"]["target_gap_lu"] == 7.0
    assert result["background_boost"]["standard_volume"] == 0.8
    assert result["background_boost"]["max_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["background_boost"]["accompaniment_lufs"] == -24.0
    assert result["background_boost"]["tts_reference_lufs"] == -13.0
    assert result["background_boost"]["fallback_reason"] is None
    assert math.isclose(result["background_boost"]["raw_volume"], 0.8 * (10 ** (4 / 20)), rel_tol=1e-6)
    assert result["effective_background_volume"] > 0.8
    assert result["effective_background_volume"] <= BOOST_MAX_BACKGROUND_VOLUME


def test_auto_boost_caps_at_max_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=1.2,
        accompaniment_lufs=-35.0,
        tts_reference_lufs=-13.0,
    )

    assert result["effective_background_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["background_boost"]["capped"] is True


def test_auto_boost_near_silent_accompaniment_falls_back_to_standard():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=0.8,
        accompaniment_lufs=-70.0,
        tts_reference_lufs=-13.0,
    )

    assert result["effective_background_volume"] == 0.8
    assert result["background_boost"]["enabled"] is False
    assert result["background_boost"]["fallback_reason"] == "accompaniment_near_silence"


def test_auto_boost_unavailable_tts_reference_falls_back_to_standard():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=None,
    )

    assert result["manual_boost_pct"] is None
    assert result["effective_background_volume"] == 0.8
    assert result["background_boost"]["enabled"] is False
    assert result["background_boost"]["fallback_reason"] == "tts_reference_lufs_unavailable"


@pytest.mark.parametrize(
    ("pct", "expected"),
    [(10, 0.88), (50, 1.2), (100, 1.6), (200, 2.4)],
)
def test_manual_boost_scales_standard_volume_linearly(pct, expected):
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_MANUAL_BOOST,
        standard_volume=0.8,
        manual_boost_pct=pct,
    )

    assert result["profile"] == LOUDNESS_PROFILE_MANUAL_BOOST
    assert result["manual_boost_pct"] == pct
    assert result["manual_boost"]["enabled"] is True
    assert result["manual_boost"]["boost_pct"] == pct
    assert result["manual_boost"]["standard_volume"] == 0.8
    assert result["manual_boost"]["max_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert math.isclose(result["effective_background_volume"], expected, rel_tol=1e-9)


def test_manual_boost_caps_at_max_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_MANUAL_BOOST,
        standard_volume=1.2,
        manual_boost_pct=200,
    )

    assert math.isclose(result["manual_boost"]["raw_volume"], 3.6, rel_tol=1e-9)
    assert result["effective_background_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["manual_boost"]["capped"] is True


@pytest.mark.parametrize("pct", [0, 5, 55, 101, 210, "abc", None])
def test_validate_loudness_profile_rejects_invalid_manual_pct(pct):
    with pytest.raises(ValueError):
        validate_loudness_profile(LOUDNESS_PROFILE_MANUAL_BOOST, pct)


@pytest.mark.parametrize("profile", ["", "louder"])
def test_validate_loudness_profile_rejects_invalid_profile_strings(profile):
    with pytest.raises(ValueError):
        validate_loudness_profile(profile, None)


def test_validate_loudness_profile_normalizes_non_manual_profiles():
    assert validate_loudness_profile(None, None) == (LOUDNESS_PROFILE_STANDARD, None)
    assert validate_loudness_profile(LOUDNESS_PROFILE_AUTO_BOOST, None) == (
        LOUDNESS_PROFILE_AUTO_BOOST,
        None,
    )
    assert validate_loudness_profile(LOUDNESS_PROFILE_VOICE_ONLY, None) == (
        LOUDNESS_PROFILE_VOICE_ONLY,
        None,
    )
    assert validate_loudness_profile(LOUDNESS_PROFILE_CLEAN_BACKGROUND, None) == (
        LOUDNESS_PROFILE_CLEAN_BACKGROUND,
        None,
    )
