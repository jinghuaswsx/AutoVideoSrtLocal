"""Phase 2 unit tests: video_duration × default_wps target_words hint feeds
into build_localized_translation_messages so round 1 lands near correct length.
"""
from __future__ import annotations

import pytest

from appcore.runtime import _compute_initial_target_words, _DEFAULT_WPS
from pipeline.localization import build_localized_translation_messages
from pipeline import localization_de, localization_fr


SEGMENTS = [{"index": 0, "text": "hello world"}]


class TestComputeInitialTargetWords:
    def test_english_target_uses_default_wps(self):
        # 30s video × 2.5 wps = 75 words for English
        assert _compute_initial_target_words(30.0, "en") == 75

    def test_german_slower_wps(self):
        # 30s × 2.0 = 60 words for German
        assert _compute_initial_target_words(30.0, "de") == 60

    def test_spanish_target_wps(self):
        # 30s × 2.7 = 81 for Spanish
        assert _compute_initial_target_words(30.0, "es") == 81

    def test_unknown_target_falls_back_to_2_5(self):
        assert _compute_initial_target_words(30.0, "xx") == 75

    def test_zero_duration_returns_zero(self):
        assert _compute_initial_target_words(0.0, "en") == 0
        assert _compute_initial_target_words(-1.0, "en") == 0

    def test_minimum_three_words_for_tiny_videos(self):
        # 0.5s × 2.5 = 1.25 → max(3, ...) = 3
        assert _compute_initial_target_words(0.5, "en") == 3

    def test_default_wps_table_covers_supported_targets(self):
        for code in ("en", "de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi"):
            assert code in _DEFAULT_WPS, f"missing default wps for {code}"


class TestPromptHint:
    """When target_words + video_duration are passed, the user content gets a
    hint sentence; otherwise the legacy content is unchanged."""

    def test_main_localization_hint_appended(self):
        msgs = build_localized_translation_messages(
            "hello", SEGMENTS,
            target_words=88,
            video_duration=35.4,
        )
        user_content = msgs[1]["content"]
        assert "35.4s video" in user_content
        assert "approximately 88 words" in user_content
        assert "Stay within" in user_content

    def test_main_localization_no_hint_when_target_missing(self):
        msgs = build_localized_translation_messages("hello", SEGMENTS)
        user_content = msgs[1]["content"]
        assert "approximately" not in user_content
        assert "Stay within" not in user_content

    def test_main_localization_no_hint_when_only_one_param(self):
        # target_words alone, no video_duration → no hint
        msgs = build_localized_translation_messages(
            "hello", SEGMENTS, target_words=88,
        )
        assert "approximately" not in msgs[1]["content"]

    def test_de_localization_hint(self):
        msgs = localization_de.build_localized_translation_messages(
            "hello", SEGMENTS,
            target_words=60,
            video_duration=30.0,
        )
        user_content = msgs[1]["content"]
        assert "30.0s video" in user_content
        assert "approximately 60 German words" in user_content

    def test_fr_localization_hint(self):
        msgs = localization_fr.build_localized_translation_messages(
            "hello", SEGMENTS,
            target_words=70,
            video_duration=25.0,
        )
        user_content = msgs[1]["content"]
        assert "25.0s video" in user_content
        assert "approximately 70 French words" in user_content

    def test_de_localization_legacy_compat(self):
        # No hint params → behavior unchanged from pre-Phase-2.
        msgs = localization_de.build_localized_translation_messages("hello", SEGMENTS)
        assert "approximately" not in msgs[1]["content"]


@pytest.mark.parametrize(
    "video_duration,target_lang,expected",
    [
        (10.0, "en", 25),
        (10.0, "de", 20),
        (15.0, "fr", 42),  # 15 × 2.8 = 42
        (20.0, "ja", 44),  # 20 × 2.2 = 44
        (35.4, "es", 96),  # 35.4 × 2.7 ≈ 95.58 → round → 96
    ],
)
def test_realistic_video_durations(video_duration, target_lang, expected):
    assert _compute_initial_target_words(video_duration, target_lang) == expected
