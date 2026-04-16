"""Tests for TTS duration convergence helpers."""
import pytest

from appcore.runtime import _compute_next_target


class TestComputeNextTarget:
    def test_round2_shrink_when_audio_over_video(self):
        # video=30, audio=35 (over by 5)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=35.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(28.0)  # video - 2.0
        assert tc == round(28.0 * 15.0)  # 420

    def test_round2_expand_when_audio_below_lower_bound(self):
        # video=30, lo=27, audio=25 (under lo by 2)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.0)  # video - 1.0
        assert tc == round(29.0 * 15.0)  # 435

    def test_round3_adaptive_overcorrection_when_still_long(self):
        # video=30, center=28.5, audio=33 (still long by ~4.5 from center)
        # target = center - 0.5 * (33 - 28.5) = 28.5 - 2.25 = 26.25
        # clamp: max(lo+0.3, min(hi-0.3, 26.25)) = max(27.3, min(29.7, 26.25)) = 27.3
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=33.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(27.3)  # clamped to duration_lo + 0.3

    def test_round3_adaptive_overcorrection_when_still_short(self):
        # video=30, center=28.5, audio=25 (still short)
        # target = 28.5 - 0.5 * (25 - 28.5) = 28.5 + 1.75 = 30.25
        # clamp to hi - 0.3 = 29.7
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.7)  # clamped to duration_hi - 0.3

    def test_target_chars_floor_at_10(self):
        # Tiny video + small cps → target_chars would be ~0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=0.1, video_duration=1.0,
        )
        assert tc >= 10

    def test_short_video_below_3s_lo_is_zero(self):
        # video=2 → duration_lo = 0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=15.0, video_duration=2.0,
        )
        # round 2 shrink → target = video - 2.0 = 0.0; target_chars clamped to >=10
        assert direction == "shrink"
        assert tc >= 10
