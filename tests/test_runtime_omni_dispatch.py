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
    "av_sync_audit": "off",
}
CFG_OMNI_CURRENT = {
    "asr_post": "asr_clean", "shot_decompose": False,
    "translate_algo": "standard", "source_anchored": True,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
    "av_sync_audit": "off",
}
CFG_AV_SYNC_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": False,
    "translate_algo": "av_sentence", "source_anchored": False,
    "tts_strategy": "sentence_reconcile", "subtitle": "sentence_units",
    "voice_separation": True, "loudness_match": True,
    "av_sync_audit": "off",
}
CFG_LAB_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": True,
    "translate_algo": "shot_char_limit", "source_anchored": False,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
    "av_sync_audit": "off",
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
    # 2026-05-07 fix: av_sentence 也需要 alignment 产出的 script_segments；
    # spec §6.1 之前以为可以跳，e2e 撞错后改回 always insert。
    assert names == [
        "extract", "asr", "separate",
        "asr_normalize",
        "voice_match", "alignment",
        "translate", "tts", "loudness_match", "subtitle",
        "compose", "export",
    ]
    assert "alignment" in names


def test_pipeline_inserts_av_sync_audit_after_tts_when_enabled(
    monkeypatch, omni_runner,
):
    cfg = dict(CFG_AV_SYNC_CURRENT)
    cfg["av_sync_audit"] = "report_only"
    _patch_resolve_cfg(monkeypatch, cfg)
    names = _step_names(omni_runner)
    assert names == [
        "extract", "asr", "separate",
        "asr_normalize",
        "voice_match", "alignment",
        "translate", "tts", "av_sync_audit", "loudness_match", "subtitle",
        "compose", "export",
    ]
    assert names.index("tts") < names.index("av_sync_audit") < names.index("subtitle")


def test_pipeline_skips_av_sync_audit_when_off(monkeypatch, omni_runner):
    _patch_resolve_cfg(monkeypatch, CFG_AV_SYNC_CURRENT)
    assert "av_sync_audit" not in _step_names(omni_runner)


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


def test_compose_variant_uses_av_for_sentence_reconcile(monkeypatch, omni_runner):
    cfg = dict(CFG_LAB_CURRENT)
    cfg["tts_strategy"] = "sentence_reconcile"
    cfg["subtitle"] = "sentence_units"
    task = {"plugin_config": cfg}

    assert omni_runner._resolve_compose_variant_name(task) == "av"


def test_shot_limit_translate_prepares_av_sentences_for_sentence_reconcile(
    monkeypatch, omni_runner,
):
    import appcore.task_state as task_state

    task_id = "omni-shot-sentence-reconcile"
    task_state.create(task_id, "/tmp/video.mp4", "/tmp/task", "video.mp4")
    cfg = dict(CFG_LAB_CURRENT)
    cfg["tts_strategy"] = "sentence_reconcile"
    cfg["subtitle"] = "sentence_units"
    task_state.update(
        task_id,
        plugin_config=cfg,
        target_lang="fr",
        selected_voice_id="voice-1",
        shots=[
            {
                "index": 1,
                "start": 0.0,
                "end": 2.0,
                "duration": 2.0,
                "source_text": "Source one",
                "description": "shot one",
            },
            {
                "index": 2,
                "start": 2.0,
                "end": 3.5,
                "duration": 1.5,
                "source_text": "Source two",
                "description": "shot two",
            },
        ],
    )
    monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda voice_id, lang: 10.0)
    monkeypatch.setattr(
        "pipeline.translate_v2.translate_shot",
        lambda shot, **kwargs: {
            "shot_index": shot["index"],
            "translated_text": f"Texte {shot['index']}",
            "char_count": 7,
            "over_limit": False,
            "retries": 0,
        },
    )
    monkeypatch.setattr(
        "appcore.llm_bindings.resolve",
        lambda use_case: {"model": "gemini-test"},
    )

    omni_runner._step_translate_shot_limit(task_id)

    task = task_state.get(task_id)
    av_sentences = task["variants"]["av"]["sentences"]
    assert [s["text"] for s in av_sentences] == ["Texte 1", "Texte 2"]
    assert av_sentences[0]["asr_index"] == 1
    assert av_sentences[0]["target_duration"] == 2.0
    assert av_sentences[0]["target_chars_range"] == [18, 22]
    assert task["variants"]["normal"]["localized_translation"]["full_text"] == "Texte 1\nTexte 2"
    assert task["steps"]["translate"] == "done"


def test_shot_limit_translate_sets_process_preview_artifact(
    monkeypatch, omni_runner,
):
    import appcore.task_state as task_state

    task_id = "omni-shot-process-preview"
    task_state.create(task_id, "/tmp/video.mp4", "/tmp/task", "video.mp4")
    task_state.update(
        task_id,
        plugin_config=CFG_LAB_CURRENT,
        target_lang="es",
        selected_voice_id="voice-1",
        shots=[
            {
                "index": 1,
                "start": 0.0,
                "end": 2.0,
                "duration": 2.0,
                "source_text": "Source one",
                "description": "shot one",
            },
            {
                "index": 2,
                "start": 2.0,
                "end": 3.5,
                "duration": 1.5,
                "source_text": "Source two",
                "description": "shot two",
            },
        ],
    )
    monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda voice_id, lang: 10.0)
    monkeypatch.setattr(
        "pipeline.translate_v2.translate_shot",
        lambda shot, **kwargs: {
            "shot_index": shot["index"],
            "translated_text": f"Texto {shot['index']}",
            "char_count": 7,
            "over_limit": False,
            "retries": 1 if shot["index"] == 2 else 0,
        },
    )
    monkeypatch.setattr(
        "appcore.llm_bindings.resolve",
        lambda use_case: {"model": "gemini-test"},
    )

    omni_runner._step_translate_shot_limit(task_id)

    task = task_state.get(task_id)
    artifact = task["artifacts"]["translate"]
    assert artifact["title"] == "翻译本土化"
    assert artifact["items"][0]["type"] == "shot_translation_summary"
    assert artifact["items"][0]["total"] == 2
    assert artifact["items"][0]["retry_count"] == 1
    assert artifact["items"][1]["type"] == "shot_translations"
    first_row = artifact["items"][1]["shots"][0]
    assert first_row["source_text"] == "Source one"
    assert first_row["translated_text"] == "Texto 1"
    assert first_row["char_limit"] == 18
    assert artifact["items"][2]["type"] == "side_by_side"


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


def test_shot_decompose_falls_back_to_asr_end_when_video_duration_missing(
    monkeypatch, tmp_path, omni_runner,
):
    import appcore.task_state as task_state
    from appcore.runtime_omni_steps import step_shot_decompose

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.llm_bindings.resolve",
        lambda use_case: {
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
        },
    )
    captured = {}

    def fake_decompose(video_path, **kwargs):
        captured["duration_seconds"] = kwargs["duration_seconds"]
        return [
            {
                "index": 1,
                "start": 0.0,
                "end": kwargs["duration_seconds"],
                "duration": kwargs["duration_seconds"],
                "description": "full video",
            }
        ]

    monkeypatch.setattr("pipeline.shot_decompose.decompose_shots", fake_decompose)
    monkeypatch.setattr(
        "pipeline.shot_decompose.align_asr_to_shots",
        lambda shots, asr_segments: shots,
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 0.0)
    task_id = "omni-shot-duration-fallback"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        utterances=[
            {"start_time": 0.0, "end_time": 1.5, "text": "Intro."},
            {"start_time": 21.8, "end_time": 23.3, "text": "CTA."},
        ],
    )

    step_shot_decompose(
        omni_runner,
        task_id,
        str(tmp_path / "video.mp4"),
        str(tmp_path),
    )

    assert captured["duration_seconds"] == 23.3


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


def test_sentence_units_subtitle_triggers_quality_assessment(
    monkeypatch, tmp_path, omni_runner,
):
    import appcore.task_state as task_state
    from appcore.translate_profiles.av_sync_profile import AvSyncProfile

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *args, **kwargs: None)
    task_id = "omni-sentence-units-qa"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        target_lang="fr",
        variants={
            "av": {
                "sentences": [
                    {
                        "asr_index": 0,
                        "source_text": "A must-have.",
                        "text": "Indispensable.",
                        "tts_duration": 1.0,
                        "target_duration": 1.0,
                        "status": "ok",
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        omni_runner,
        "_complete_original_video_passthrough",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        omni_runner,
        "_resolve_av_inputs",
        lambda task: {
            "target_language": "fr",
            "target_language_name": "French",
            "sync_granularity": "sentence",
        },
    )
    monkeypatch.setattr(
        omni_runner,
        "_target_language_name",
        lambda av_inputs: av_inputs["target_language_name"],
    )
    calls = []
    monkeypatch.setattr(
        "appcore.quality_assessment.trigger_assessment",
        lambda **kwargs: calls.append(kwargs) or 1,
    )

    AvSyncProfile().subtitle(omni_runner, task_id, str(tmp_path))

    assert task_state.get(task_id)["steps"]["subtitle"] == "done"
    assert calls == [{
        "task_id": task_id,
        "project_type": "omni_translate",
        "triggered_by": "auto",
        "user_id": 1,
    }]


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
