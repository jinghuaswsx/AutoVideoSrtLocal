"""Tests for appcore.omni_plugin_config (Phase 1)."""
from __future__ import annotations

import pytest

from appcore.omni_plugin_config import (
    CAPABILITY_GROUPS,
    DEFAULT_PLUGIN_CONFIG,
    validate_plugin_config,
)


# ---------------------------------------------------------------------------
# Static metadata sanity
# ---------------------------------------------------------------------------


def test_capability_groups_have_8_entries():
    """8 分组（spec §3）：1 个 ASR 后处理 + shot_decompose + 翻译算法 + prompt
    增强 + TTS 收敛 + 字幕 + 人声分离 + 响度匹配。"""
    assert len(CAPABILITY_GROUPS) == 8


def test_default_plugin_config_has_all_keys():
    expected = {
        "asr_post", "shot_decompose", "translate_algo", "source_anchored",
        "tts_strategy", "subtitle", "voice_separation", "loudness_match",
    }
    assert set(DEFAULT_PLUGIN_CONFIG) == expected


def test_default_matches_omni_current_baseline():
    assert DEFAULT_PLUGIN_CONFIG == {
        "asr_post": "asr_clean",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": True,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_validate_passes_for_default():
    out = validate_plugin_config(dict(DEFAULT_PLUGIN_CONFIG))
    assert out == DEFAULT_PLUGIN_CONFIG


def test_validate_returns_copy_not_mutating_input():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    out = validate_plugin_config(cfg)
    out["asr_post"] = "asr_normalize"
    assert cfg["asr_post"] == "asr_clean"


# ---------------------------------------------------------------------------
# Defaults filled in for missing fields
# ---------------------------------------------------------------------------


def test_validate_fills_missing_radio_with_default():
    cfg = {}  # 全部缺失
    out = validate_plugin_config(cfg)
    assert out == DEFAULT_PLUGIN_CONFIG


def test_validate_fills_missing_boolean_with_default():
    cfg = {
        "asr_post": "asr_normalize",
        "translate_algo": "standard",
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
    }
    out = validate_plugin_config(cfg)
    assert out["voice_separation"] is True
    assert out["loudness_match"] is True
    assert out["shot_decompose"] is False
    assert out["source_anchored"] is True


def test_validate_accepts_none_as_empty():
    out = validate_plugin_config(None)
    assert out == DEFAULT_PLUGIN_CONFIG


# ---------------------------------------------------------------------------
# Radio value validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("asr_post", "asr_unknown"),
        ("translate_algo", "magic"),
        ("tts_strategy", "asdf"),
        ("subtitle", "wat"),
    ],
)
def test_validate_rejects_unknown_radio_value(field, bad_value):
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg[field] = bad_value
    with pytest.raises(ValueError, match=field):
        validate_plugin_config(cfg)


def test_validate_rejects_non_string_radio():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["asr_post"] = 1
    with pytest.raises(ValueError, match="asr_post"):
        validate_plugin_config(cfg)


# ---------------------------------------------------------------------------
# Boolean coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    (1, True), (0, False),
    ("true", True), ("false", False),
    ("True", True), ("False", False),
    ("1", True), ("0", False),
])
def test_validate_coerces_boolean_friendly_inputs(value, expected):
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["voice_separation"] = value
    cfg["loudness_match"] = value if expected else False  # 满足 ⑦/⑧ 依赖
    out = validate_plugin_config(cfg)
    assert out["voice_separation"] is expected


def test_validate_rejects_garbage_boolean():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["voice_separation"] = "yes please"
    with pytest.raises(ValueError, match="voice_separation"):
        validate_plugin_config(cfg)


# ---------------------------------------------------------------------------
# Dependency rules
# ---------------------------------------------------------------------------


def test_validate_rejects_shot_char_limit_without_shot_decompose():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["translate_algo"] = "shot_char_limit"
    cfg["shot_decompose"] = False
    with pytest.raises(ValueError, match="shot_decompose"):
        validate_plugin_config(cfg)


def test_validate_accepts_shot_char_limit_with_shot_decompose():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["translate_algo"] = "shot_char_limit"
    cfg["shot_decompose"] = True
    out = validate_plugin_config(cfg)
    assert out["translate_algo"] == "shot_char_limit"
    assert out["shot_decompose"] is True


def test_validate_rejects_sentence_units_without_sentence_reconcile():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["subtitle"] = "sentence_units"
    cfg["tts_strategy"] = "five_round_rewrite"  # 不匹配
    with pytest.raises(ValueError, match="sentence_reconcile"):
        validate_plugin_config(cfg)


def test_validate_rejects_loudness_without_voice_separation():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["loudness_match"] = True
    cfg["voice_separation"] = False
    with pytest.raises(ValueError, match="voice_separation"):
        validate_plugin_config(cfg)


# ---------------------------------------------------------------------------
# Silent fix
# ---------------------------------------------------------------------------


def test_validate_silent_fixes_av_sentence_with_source_anchored():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["translate_algo"] = "av_sentence"
    cfg["source_anchored"] = True
    out = validate_plugin_config(cfg)
    assert out["translate_algo"] == "av_sentence"
    # source_anchored 自动 silent fix 成 False
    assert out["source_anchored"] is False


def test_validate_keeps_source_anchored_for_standard_translate():
    cfg = dict(DEFAULT_PLUGIN_CONFIG)
    cfg["translate_algo"] = "standard"
    cfg["source_anchored"] = True
    out = validate_plugin_config(cfg)
    assert out["source_anchored"] is True


def test_validate_rejects_non_dict_input():
    with pytest.raises(ValueError):
        validate_plugin_config("not a dict")


# ---------------------------------------------------------------------------
# 4 baseline preset configurations all valid
# ---------------------------------------------------------------------------


def test_baseline_preset_multi_like_validates():
    cfg = {
        "asr_post": "asr_normalize",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": False,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }
    assert validate_plugin_config(cfg) == cfg


def test_baseline_preset_omni_current_validates():
    cfg = {
        "asr_post": "asr_clean",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": True,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }
    assert validate_plugin_config(cfg) == cfg


def test_baseline_preset_av_sync_current_validates():
    cfg = {
        "asr_post": "asr_normalize",
        "shot_decompose": False,
        "translate_algo": "av_sentence",
        "source_anchored": False,
        "tts_strategy": "sentence_reconcile",
        "subtitle": "sentence_units",
        "voice_separation": True,
        "loudness_match": True,
    }
    assert validate_plugin_config(cfg) == cfg


def test_baseline_preset_lab_current_validates():
    cfg = {
        "asr_post": "asr_normalize",
        "shot_decompose": True,
        "translate_algo": "shot_char_limit",
        "source_anchored": False,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }
    assert validate_plugin_config(cfg) == cfg
