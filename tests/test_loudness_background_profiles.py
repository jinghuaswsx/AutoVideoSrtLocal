import math

import pytest

from appcore.audio_loudness import (
    BOOST_MAX_BACKGROUND_VOLUME,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_MANUAL_BOOST,
    LOUDNESS_PROFILE_STANDARD,
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
    assert result["background_boost"]["target_gap_lu"] == 10.0
    assert result["background_boost"]["standard_volume"] == 0.8
    assert result["background_boost"]["max_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["background_boost"]["accompaniment_lufs"] == -24.0
    assert result["background_boost"]["tts_reference_lufs"] == -13.0
    assert result["background_boost"]["fallback_reason"] is None
    assert math.isclose(result["background_boost"]["raw_volume"], 0.8 * (10 ** (1 / 20)), rel_tol=1e-6)
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
    [(10, 0.88), (50, 1.2), (100, 1.6)],
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
        manual_boost_pct=100,
    )

    assert result["manual_boost"]["raw_volume"] == 2.4
    assert result["effective_background_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["manual_boost"]["capped"] is True


@pytest.mark.parametrize("pct", [0, 5, 55, 101, "abc", None])
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
