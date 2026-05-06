"""Tests for OmniTranslateRunner / OmniProfile plugin_config dispatch (Phase 2).

覆盖：
- ``_resolve_plugin_config``：task 有 cfg / 没 cfg / cfg 不合法 时回退路径
- ``_get_pipeline_steps``：4 个 baseline preset 各自跑出预期 step list
- ``OmniProfile.{post_asr,translate,tts,subtitle}``: 按 cfg dispatch 到正确的
  runner method / 抽象包
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from appcore.events import EventBus
from appcore.runtime_omni import OmniTranslateRunner
from appcore.translate_profiles import get_profile


# ---------------------------------------------------------------------------
# Baseline preset cfgs — 跟 db/migrations/2026_05_07_omni_translate_presets.sql
# 的 4 个 seed 一致
# ---------------------------------------------------------------------------

CFG_MULTI_LIKE = {
    "asr_post": "asr_normalize", "shot_decompose": False,
    "translate_algo": "standard", "source_anchored": False,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}
CFG_OMNI_CURRENT = {
    "asr_post": "asr_clean", "shot_decompose": False,
    "translate_algo": "standard", "source_anchored": True,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}
CFG_AV_SYNC_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": False,
    "translate_algo": "av_sentence", "source_anchored": False,
    "tts_strategy": "sentence_reconcile", "subtitle": "sentence_units",
    "voice_separation": True, "loudness_match": True,
}
CFG_LAB_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": True,
    "translate_algo": "shot_char_limit", "source_anchored": False,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}


@pytest.fixture
def omni_runner():
    return OmniTranslateRunner(bus=EventBus(), user_id=1)


def _patch_resolve_cfg(monkeypatch, cfg):
    """让 OmniRunner._resolve_plugin_config 返回固定 cfg。"""
    monkeypatch.setattr(
        "appcore.runtime_omni.OmniTranslateRunner._resolve_plugin_config",
        lambda self, task_id: cfg,
    )


def _step_names(runner):
    return [name for name, _fn in runner._get_pipeline_steps("t", "/tmp/v.mp4", "/tmp")]


# ---------------------------------------------------------------------------
# _get_pipeline_steps 各 preset 跑出来的 step list
# ---------------------------------------------------------------------------


def test_pipeline_steps_for_omni_current(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_OMNI_CURRENT)
    assert _step_names(omni_runner) == [
        "extract", "asr", "separate",
        "asr_clean",
        "voice_match", "alignment",
        "translate", "tts", "loudness_match", "subtitle",
        "compose", "export",
    ]


def test_pipeline_steps_for_multi_like(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_MULTI_LIKE)
    names = _step_names(omni_runner)
    # multi-like 用 asr_normalize + standard + five_round + asr_realign
    assert names == [
        "extract", "asr", "separate",
        "asr_normalize",
        "voice_match", "alignment",
        "translate", "tts", "loudness_match", "subtitle",
        "compose", "export",
    ]


def test_pipeline_steps_for_av_sync_current(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_AV_SYNC_CURRENT)
    names = _step_names(omni_runner)
    # av_sentence translate 不需要 alignment
    assert names == [
        "extract", "asr", "separate",
        "asr_normalize",
        "voice_match",
        "translate", "tts", "loudness_match", "subtitle",
        "compose", "export",
    ]
    assert "alignment" not in names


def test_pipeline_steps_for_lab_current(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_LAB_CURRENT)
    names = _step_names(omni_runner)
    # shot_decompose 插在 separate 后、post_asr 前
    assert names == [
        "extract", "asr", "separate",
        "shot_decompose",
        "asr_normalize",
        "voice_match", "alignment",
        "translate", "tts", "loudness_match", "subtitle",
        "compose", "export",
    ]


def test_pipeline_skips_separate_when_voice_separation_disabled(
    monkeypatch, omni_runner,
):
    cfg = dict(CFG_OMNI_CURRENT)
    cfg["voice_separation"] = False
    cfg["loudness_match"] = False  # 依赖 voice_separation
    _patch_resolve_cfg(monkeypatch, cfg)
    names = _step_names(omni_runner)
    assert "separate" not in names
    assert "loudness_match" not in names


def test_pipeline_keeps_loudness_when_voice_separation_on(
    monkeypatch, omni_runner,
):
    cfg = dict(CFG_OMNI_CURRENT)
    cfg["voice_separation"] = True
    cfg["loudness_match"] = True
    _patch_resolve_cfg(monkeypatch, cfg)
    assert "loudness_match" in _step_names(omni_runner)


# ---------------------------------------------------------------------------
# OmniProfile dispatch
# ---------------------------------------------------------------------------


def test_post_asr_dispatches_to_asr_clean_when_cfg_says_so(
    monkeypatch, omni_runner,
):
    _patch_resolve_cfg(monkeypatch, CFG_OMNI_CURRENT)
    omni_runner._step_asr_clean = MagicMock()
    omni_runner._step_asr_normalize = MagicMock()
    profile = get_profile("omni")
    profile.post_asr(omni_runner, "t-x")
    omni_runner._step_asr_clean.assert_called_once_with("t-x")
    omni_runner._step_asr_normalize.assert_not_called()


def test_post_asr_dispatches_to_asr_normalize_when_cfg_says_so(
    monkeypatch, omni_runner,
):
    _patch_resolve_cfg(monkeypatch, CFG_MULTI_LIKE)
    omni_runner._step_asr_clean = MagicMock()
    omni_runner._step_asr_normalize = MagicMock()
    profile = get_profile("omni")
    profile.post_asr(omni_runner, "t-x")
    omni_runner._step_asr_normalize.assert_called_once_with("t-x")
    omni_runner._step_asr_clean.assert_not_called()


def test_translate_standard_propagates_source_anchored_flag(
    monkeypatch, omni_runner,
):
    _patch_resolve_cfg(monkeypatch, CFG_OMNI_CURRENT)  # source_anchored=True
    omni_runner._step_translate_standard = MagicMock()
    profile = get_profile("omni")
    profile.translate(omni_runner, "t-x")
    omni_runner._step_translate_standard.assert_called_once_with(
        "t-x", source_anchored=True,
    )


def test_translate_standard_with_source_anchored_off(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_MULTI_LIKE)  # source_anchored=False
    omni_runner._step_translate_standard = MagicMock()
    profile = get_profile("omni")
    profile.translate(omni_runner, "t-x")
    omni_runner._step_translate_standard.assert_called_once_with(
        "t-x", source_anchored=False,
    )


def test_translate_dispatches_to_shot_limit(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_LAB_CURRENT)
    omni_runner._step_translate_shot_limit = MagicMock()
    profile = get_profile("omni")
    profile.translate(omni_runner, "t-x")
    omni_runner._step_translate_shot_limit.assert_called_once_with("t-x")


def test_translate_dispatches_to_av_sentence_via_av_sync_profile(
    monkeypatch, omni_runner,
):
    _patch_resolve_cfg(monkeypatch, CFG_AV_SYNC_CURRENT)
    monkeypatch.setattr(
        "appcore.translate_profiles.av_sync_profile.AvSyncProfile.translate",
        lambda self, runner, task_id: setattr(runner, "_av_translate_called", task_id),
    )
    profile = get_profile("omni")
    profile.translate(omni_runner, "t-x")
    assert getattr(omni_runner, "_av_translate_called", None) == "t-x"


def test_subtitle_dispatches_to_asr_realign(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_OMNI_CURRENT)
    omni_runner._step_subtitle_asr_realign = MagicMock()
    profile = get_profile("omni")
    profile.subtitle(omni_runner, "t-x", "/tmp/x")
    omni_runner._step_subtitle_asr_realign.assert_called_once_with("t-x", "/tmp/x")


def test_subtitle_dispatches_to_sentence_units_via_av_sync_profile(
    monkeypatch, omni_runner,
):
    _patch_resolve_cfg(monkeypatch, CFG_AV_SYNC_CURRENT)
    monkeypatch.setattr(
        "appcore.translate_profiles.av_sync_profile.AvSyncProfile.subtitle",
        lambda self, runner, task_id, task_dir:
            setattr(runner, "_av_subtitle_called", (task_id, task_dir)),
    )
    profile = get_profile("omni")
    profile.subtitle(omni_runner, "t-x", "/tmp/x")
    assert getattr(omni_runner, "_av_subtitle_called", None) == ("t-x", "/tmp/x")


def test_tts_dispatches_to_strategy_by_cfg(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_AV_SYNC_CURRENT)
    seen = {}

    class _Stub:
        def run(self, runner, profile, task_id, task_dir):
            seen["called"] = (task_id, task_dir)

    monkeypatch.setattr(
        "appcore.tts_strategies.get_strategy",
        lambda code: _Stub() if code == "sentence_reconcile" else None,
    )
    profile = get_profile("omni")
    profile.tts(omni_runner, "t-x", "/tmp/x")
    assert seen.get("called") == ("t-x", "/tmp/x")


def test_tts_dispatches_to_five_round_strategy_by_cfg(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_OMNI_CURRENT)
    seen = {}

    class _Stub:
        def run(self, runner, profile, task_id, task_dir):
            seen["code"] = "five_round"
            seen["called"] = (task_id, task_dir)

    monkeypatch.setattr(
        "appcore.tts_strategies.get_strategy",
        lambda code: _Stub() if code == "five_round_rewrite" else None,
    )
    profile = get_profile("omni")
    profile.tts(omni_runner, "t-x", "/tmp/x")
    assert seen.get("code") == "five_round"


# ---------------------------------------------------------------------------
# _resolve_plugin_config 兜底链
# ---------------------------------------------------------------------------


def test_resolve_plugin_config_reads_task_field_when_present(
    monkeypatch, omni_runner,
):
    fake_task = {"plugin_config": dict(CFG_LAB_CURRENT)}
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: fake_task)
    cfg = omni_runner._resolve_plugin_config("t-x")
    assert cfg["translate_algo"] == "shot_char_limit"


def test_resolve_plugin_config_falls_back_to_default_preset_when_task_missing_cfg(
    monkeypatch, omni_runner,
):
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {})
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get_default",
        lambda: {"plugin_config": dict(CFG_AV_SYNC_CURRENT)},
    )
    cfg = omni_runner._resolve_plugin_config("t-x")
    assert cfg["translate_algo"] == "av_sentence"


def test_resolve_plugin_config_falls_back_to_hardcoded_default_when_db_fails(
    monkeypatch, omni_runner,
):
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {})
    def _boom():
        raise RuntimeError("DB down")
    monkeypatch.setattr("appcore.omni_preset_dao.get_default", _boom)
    cfg = omni_runner._resolve_plugin_config("t-x")
    # 走硬编码 DEFAULT_PLUGIN_CONFIG = omni-current 基线
    assert cfg["asr_post"] == "asr_clean"
    assert cfg["translate_algo"] == "standard"
    assert cfg["source_anchored"] is True


def test_resolve_plugin_config_drops_invalid_cfg_and_falls_back(
    monkeypatch, omni_runner,
):
    """task.plugin_config 不合法时不报错，自动 fallback。"""
    bad_cfg = {"asr_post": "magic"}  # 非法
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda task_id: {"plugin_config": bad_cfg},
    )
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get_default",
        lambda: {"plugin_config": dict(CFG_OMNI_CURRENT)},
    )
    cfg = omni_runner._resolve_plugin_config("t-x")
    assert cfg["asr_post"] == "asr_clean"  # 来自全站默认
